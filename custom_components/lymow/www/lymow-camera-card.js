/*
 * Lymow camera card — live onboard camera with a LAN / Cloud source switch.
 *
 * Config:
 *   type: custom:lymow-camera-card
 *   mower_entity:  lawn_mower.lymow_THING   # required (used for the cloud session)
 *   camera_entity: camera.THING_camera      # required for the LAN view
 *   title: Lymow Camera                     # optional
 *   default_source: lan | cloud             # optional (default: lan)
 *   lan_workers: 3                          # optional — parallel fetch workers (1-10, default: 3)
 *                                           #   More workers = higher frame rate but heavier on HA.
 *                                           #   Effective fps ≈ workers / generation_time_per_frame.
 *
 * Two genuinely separate transport paths:
 *   LAN   — ha-camera-stream picks the best path based on frontend_stream_type.
 *           The camera entity sets StreamType.HLS, bypassing go2rtc WebRTC
 *           (which produces green H.264 artifacts). Result: clean HLS stream.
 *   Cloud — in-browser AWS KVS WebRTC. The browser talks straight to AWS and
 *           the robot streams over its own internet link (works on 4G, away
 *           from home). The integration only hands us the signed session.
 */

const FRAME_TIMEOUT_MS = 25000;

class LymowCameraCard extends HTMLElement {
  setConfig(config) {
    if (!config || !config.mower_entity) {
      throw new Error("lymow-camera-card: 'mower_entity' is required");
    }
    this._config = config;
    this._source = config.default_source === "cloud" ? "cloud" : "lan";
    this._built = false;
    this._lanActive = false;
    this._lanWorkers = Math.max(1, Math.min(10, config.lan_workers ?? 3));
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) return;
    if (this._source === "lan") this._renderLan();
    else if (!this._pc) this._startCloud();
  }

  connectedCallback() {
    this._build();
    this._select(this._source);
  }

  disconnectedCallback() {
    this._stopCloud();
    this._stopLan();
    if (this.classList.contains("wfs")) this._toggleWindowFS(false);
  }

  getCardSize() {
    return 6;
  }

  static getStubConfig() {
    return { mower_entity: "lawn_mower.lymow", camera_entity: "camera.lymow_camera" };
  }

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .bar { display:flex; align-items:center; gap:8px; padding:8px 12px; }
        .title { font-weight:600; flex:1; }
        .seg { display:inline-flex; border:1px solid var(--divider-color,#e0e0e0); border-radius:18px; overflow:hidden; }
        .seg button { border:0; background:transparent; padding:5px 14px; cursor:pointer; font:inherit; color:var(--primary-text-color); }
        .seg button.on { background:var(--primary-color,#03a9f4); color:#fff; }
        .fs-btn { border:0; background:transparent; padding:4px 8px; cursor:pointer; color:var(--primary-text-color); font-size:18px; border-radius:4px; line-height:1; }
        .fs-btn:hover { background:var(--secondary-background-color); }
        .interval-ctrl { display:inline-flex; align-items:center; gap:4px; font-size:12px; color:var(--secondary-text-color); }
        .iv-btn { border:0; background:transparent; padding:2px 6px; cursor:pointer; color:var(--primary-text-color); font-size:14px; border-radius:4px; line-height:1; }
        .iv-btn:hover { background:var(--secondary-background-color); }
        .iv-val { min-width:36px; text-align:center; font-size:11px; }
        .stage { position:relative; background:#000; aspect-ratio:4/3; width:100%; overflow:hidden; display:flex; align-items:center; justify-content:center; }
        .stage img, .stage video { width:100%; height:100%; object-fit:contain; background:#000; display:block; }
        /* Native OS fullscreen (desktop only) */
        :host(:fullscreen) ha-card { display:flex; flex-direction:column; width:100vw; height:100vh; overflow:hidden; }
        :host(:fullscreen) .stage { aspect-ratio:unset; flex:1; min-height:0; overflow:hidden; }
        :host(:fullscreen) .stage img, :host(:fullscreen) .stage video { width:100%; height:100%; object-fit:contain; }
        /* In-window fullscreen — fixed overlay covering entire browser viewport */
        :host(.wfs) { position:fixed; inset:0; z-index:9999; display:block; }
        :host(.wfs) ha-card { display:flex; flex-direction:column; width:100%; height:100%; overflow:hidden; border-radius:0; }
        :host(.wfs) .stage { aspect-ratio:unset; flex:1; min-height:0; overflow:hidden; }
        :host(.wfs) .stage img, :host(.wfs) .stage video { width:100%; height:100%; object-fit:contain; }
        .status { position:absolute; color:#eee; font-size:14px; text-align:center; padding:0 16px; }
        .status.err { color:#ff8a80; }
        .hidden { display:none !important; }
      </style>
      <ha-card>
        <div class="bar">
          <span class="title"></span>
          <span class="seg">
            <button data-src="lan">LAN</button>
            <button data-src="cloud">Cloud</button>
          </span>
          <span class="interval-ctrl hidden">
            <button class="iv-btn" data-delta="-1">−</button>
            <span class="iv-val"></span>
            <button class="iv-btn" data-delta="1">+</button>
          </span>
          <button class="fs-btn wfs-btn" title="Expand in window">⤡</button>
          <button class="fs-btn nfs-btn" title="Fullscreen">⛶</button>
        </div>
        <div class="stage">
          <img class="lan hidden" alt="">
          <video class="cloud hidden" autoplay playsinline muted></video>
          <div class="status hidden"></div>
        </div>
      </ha-card>`;
    this._els = {
      title: root.querySelector(".title"),
      seg: root.querySelectorAll(".seg button"),
      lanStream: root.querySelector("img.lan"),
      video: root.querySelector("video.cloud"),
      status: root.querySelector(".status"),
      wfsBtn: root.querySelector(".wfs-btn"),
      nfsBtn: root.querySelector(".nfs-btn"),
      intervalCtrl: root.querySelector(".interval-ctrl"),
      intervalVal: root.querySelector(".iv-val"),
    };
    this._els.title.textContent = this._config.title || "Lymow Camera";
    this._els.seg.forEach((b) => b.addEventListener("click", () => this._select(b.dataset.src)));

    // In-window fullscreen: fixed overlay covering the browser viewport. Works on iOS.
    this._els.wfsBtn.addEventListener("click", () => this._toggleWindowFS());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.classList.contains("wfs")) this._toggleWindowFS(false);
    });

    // Native OS fullscreen: hidden on iOS/iPadOS where requestFullscreen() is unsupported.
    if (!document.fullscreenEnabled) {
      this._els.nfsBtn.classList.add("hidden");
    } else {
      this._els.nfsBtn.addEventListener("click", () => {
        if (!document.fullscreenElement) this.requestFullscreen().catch(() => {});
        else document.exitFullscreen().catch(() => {});
      });
    }
    root.querySelectorAll(".iv-btn").forEach((b) => {
      b.addEventListener("click", () => {
        this._lanWorkers = Math.max(1, Math.min(10, this._lanWorkers + parseInt(b.dataset.delta)));
        this._updateIntervalDisplay();
      });
    });
    this._updateIntervalDisplay();
    this._built = true;
  }

  _toggleWindowFS(force) {
    const on = force !== undefined ? force : !this.classList.contains("wfs");
    this.classList.toggle("wfs", on);
    // Prevent the page behind from scrolling while the overlay is open
    document.body.style.overflow = on ? "hidden" : "";
    // Flip icon: ⤡ when normal, ⤢ when expanded (so it acts as a close signal)
    this._els.wfsBtn.textContent = on ? "⤢" : "⤡";
  }

  _setStatus(msg, isErr = false) {
    this._els.status.textContent = msg || "";
    this._els.status.classList.toggle("err", isErr);
    this._els.status.classList.toggle("hidden", !msg);
  }

  _select(src) {
    this._source = src;
    this._els.seg.forEach((b) => b.classList.toggle("on", b.dataset.src === src));
    this._els.intervalCtrl.classList.toggle("hidden", src !== "lan");
    if (src === "lan") {
      this._stopCloud();
      this._els.video.classList.add("hidden");
      this._els.lanStream.classList.remove("hidden");
      this._renderLan();
    } else {
      this._stopLan();
      this._els.lanStream.classList.add("hidden");
      this._els.video.classList.remove("hidden");
      this._startCloud();
    }
  }

  _updateIntervalDisplay() {
    this._els.intervalVal.textContent = `${this._lanWorkers}×`;
  }

  // ---- LAN: concurrent JPEG pipeline via HA camera proxy --------------------
  // N workers fetch simultaneously. HA's ffmpeg generates each frame (~2-5s).
  // Effective fps ≈ N / generation_time. Workers are staggered at startup so
  // frames arrive evenly spaced rather than in bursts. Only the most recently
  // captured frame is shown; out-of-order completions are silently discarded.
  // Max 10 in-flight requests to avoid flooding HA.
  _renderLan() {
    const eid = this._config.camera_entity;
    const st = eid && this._hass && this._hass.states[eid];
    if (!st) {
      this._setStatus(eid ? `${eid} unavailable` : "Set camera_entity for LAN view", true);
      return;
    }
    if (st.state === "unavailable") {
      this._setStatus("Robot offline (LAN) — try Cloud", true);
      return;
    }
    if (this._lanActive) return;
    this._lanActive = true;
    this._setStatus("");

    const token = st.attributes.access_token;
    const base = `/api/camera_proxy/${eid}?token=${token}`;
    const img = this._els.lanStream;
    // Pre-start HA's HLS stream so ffmpeg's RTSP connection stays warm.
    this._hass.callWS({ type: "camera/stream", entity_id: eid, format: "hls" }).catch(() => {});

    let lastShownTs = 0; // monotonic capture-time stamp; only newer frames are shown

    // Each worker runs in an infinite loop: fetch → show if newest → repeat.
    // Stagger initial starts by estimatedGenerationMs/N so frames arrive evenly.
    const estimatedGenerationMs = 5000;
    const startWorker = (initialDelay) => {
      setTimeout(() => {
        const run = () => {
          if (!this._lanActive) return;
          const captureTs = Date.now();
          const url = `${base}&_=${captureTs}`;
          const next = new Image();
          next.onload = () => {
            if (!this._lanActive) return;
            // Only display if this frame is newer than what's already shown.
            if (captureTs > lastShownTs) {
              lastShownTs = captureTs;
              img.src = next.src;
            }
            run(); // immediately start next fetch — pipeline never idles
          };
          next.onerror = () => {
            if (this._lanActive) setTimeout(run, 2000);
          };
          next.src = url;
        };
        run();
      }, initialDelay);
    };

    const n = this._lanWorkers;
    for (let i = 0; i < n; i++) {
      startWorker(Math.round((estimatedGenerationMs / n) * i));
    }
  }

  _stopLan() {
    this._lanActive = false;
    if (this._els && this._els.lanStream) this._els.lanStream.src = "";
  }

  // ---- Cloud: in-browser AWS KVS WebRTC viewer ------------------------------
  async _startCloud() {
    this._stopCloud();
    if (!this._hass) {
      this._setStatus("Loading…");
      return;
    }
    const token = ++this._cloudToken | 0;
    this._cloudToken = token;
    this._setStatus("Connecting to cloud…");
    let session;
    try {
      const res = await this._hass.callService(
        "lymow",
        "start_video_session",
        { entity_id: [this._config.mower_entity] },
        undefined,
        false,
        true
      );
      session = res && res.response;
    } catch (e) {
      this._setStatus(`Session error: ${e.message || e}`, true);
      return;
    }
    if (token !== this._cloudToken) return;
    if (!session || !session.viewerWssUrl) {
      this._setStatus("Cloud camera unavailable (no signaling session)", true);
      return;
    }

    const pc = new RTCPeerConnection({
      iceServers: session.webrtcIceServers || [],
      bundlePolicy: "max-bundle",
    });
    this._pc = pc;
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = (ev) => {
      if (token !== this._cloudToken) return;
      this._els.video.srcObject = ev.streams[0] || new MediaStream([ev.track]);
      this._els.video.play().catch(() => {});
    };
    pc.onconnectionstatechange = () => {
      if (token !== this._cloudToken) return;
      if (pc.connectionState === "failed") this._setStatus("Cloud connection failed", true);
    };

    const ws = new WebSocket(session.viewerWssUrl);
    this._ws = ws;
    pc.onicecandidate = (e) => {
      if (e.candidate && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ action: "ICE_CANDIDATE", messagePayload: btoa(JSON.stringify(e.candidate)) }));
      }
    };
    ws.onmessage = async (ev) => {
      if (!ev.data || token !== this._cloudToken) return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (!msg.messagePayload) return;
      let payload;
      try { payload = JSON.parse(atob(msg.messagePayload)); } catch { return; }
      const kind = msg.messageType || msg.action;
      if (kind === "SDP_ANSWER") {
        await pc.setRemoteDescription({ type: "answer", sdp: payload.sdp });
      } else if (kind === "ICE_CANDIDATE") {
        try { await pc.addIceCandidate(payload); } catch { /* late/duplicate */ }
      }
    };
    ws.onerror = () => {
      if (token === this._cloudToken) this._setStatus("Signaling socket error", true);
    };
    ws.onopen = async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        ws.send(JSON.stringify({
          action: "SDP_OFFER",
          messagePayload: btoa(JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp })),
        }));
      } catch (e) {
        this._setStatus(`Offer error: ${e.message || e}`, true);
      }
    };

    this._frameTimer = setTimeout(async () => {
      if (token !== this._cloudToken) return;
      const stats = await pc.getStats();
      let frames = 0;
      stats.forEach((s) => {
        if (s.type === "inbound-rtp" && (s.kind === "video" || s.mediaType === "video")) frames = s.framesDecoded || 0;
      });
      if (!frames) this._setStatus("No video from robot — is it online?", true);
      else this._setStatus("");
    }, FRAME_TIMEOUT_MS);

    this._els.video.onloadeddata = () => {
      if (token === this._cloudToken) this._setStatus("");
    };
  }

  _stopCloud() {
    this._cloudToken = (this._cloudToken | 0) + 1;
    if (this._frameTimer) { clearTimeout(this._frameTimer); this._frameTimer = null; }
    if (this._ws) { try { this._ws.close(); } catch { /* already closed */ } this._ws = null; }
    if (this._pc) { try { this._pc.close(); } catch { /* already closed */ } this._pc = null; }
    if (this._els && this._els.video) this._els.video.srcObject = null;
  }
}

if (!customElements.get("lymow-camera-card")) {
  customElements.define("lymow-camera-card", LymowCameraCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "lymow-camera-card")) {
  window.customCards.push({
    type: "lymow-camera-card",
    name: "Lymow Camera",
    description: "Onboard camera with LAN (HLS via HA stream) / Cloud (KVS WebRTC) source switch.",
  });
}

console.info("%c LYMOW-CAMERA-CARD ", "background:#43a047;color:#fff;border-radius:3px");
