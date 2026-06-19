/*
 * Lymow camera card — live onboard camera with a LAN / Cloud source switch.
 *
 * Config:
 *   type: custom:lymow-camera-card
 *   mower_entity:  lawn_mower.lymow_THING   # required (used for the cloud session)
 *   camera_entity: camera.THING_camera      # required for the LAN view
 *   title: Lymow Camera                     # optional
 *   default_source: lan | cloud             # optional (default: lan)
 *   default_lan_mode: stream | snap         # optional (default: stream)
 *   snap_fps: 2                             # optional — target FPS in Snap mode (0.5-5, default 2)
 *
 * Two transport paths:
 *   LAN Stream  — smooth continuous video via HA's stream component.
 *   LAN Snap    — periodic JPEG snapshots at configurable FPS (adjustable in UI).
 *   Cloud       — in-browser AWS KVS WebRTC, works away from home.
 */

const FRAME_TIMEOUT_MS = 25000;

class LymowCameraCard extends HTMLElement {
  setConfig(config) {
    // Accept both 'mower'/'camera' (short) and 'mower_entity'/'camera_entity' (long)
    const cfg = {
      ...config,
      mower_entity: config.mower_entity || config.mower,
      camera_entity: config.camera_entity || config.camera,
    };
    if (!cfg.mower_entity) {
      throw new Error("lymow-camera-card: 'mower_entity' (or 'mower') is required");
    }
    this._config = cfg;
    this._source = cfg.default_source === "cloud" ? "cloud" : "lan";
    this._lanMode = cfg.default_lan_mode === "snap" ? "snap" : "stream";
    this._snapFps = Math.max(0.5, Math.min(5, cfg.snap_fps ?? 2));
    this._built = false;
    this._lanActive = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) return;
    if (this._source === "lan") {
      if (!this._restarting) this._renderLan(); // don't interfere during proxy restart
      if (this._lanActive && this._lanMode === "stream" && this._els?.lanStream) {
        const eid = this._config.camera_entity;
        const st = eid && hass.states[eid];
        // Only push hass/stateObj on ha-camera-stream (fallback path)
        if (this._els.lanStream.tagName === "HA-CAMERA-STREAM") {
          this._els.lanStream.hass = hass;
          if (st) this._els.lanStream.stateObj = st;
        }
        // Sync quality selector value from entity attribute
        const segSecs = st?.attributes?.hls_segment_seconds;
        if (segSecs != null && this._els.qualitySel) {
          const closest = ["0.5","1","2","4","8"].reduce((a,b) => Math.abs(b-segSecs) < Math.abs(a-segSecs) ? b : a);
          this._els.qualitySel.value = closest;
        }
        this._updateProxyHint();
      }
    } else if (!this._pc) this._startCloud();
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
        .quality-sel { border:1px solid var(--divider-color,#e0e0e0); border-radius:14px; background:transparent; color:var(--primary-text-color); padding:3px 8px; font-size:12px; cursor:pointer; }
        .fs-btn { border:0; background:transparent; padding:4px 8px; cursor:pointer; color:var(--primary-text-color); font-size:18px; border-radius:4px; line-height:1; }
        .fs-btn:hover { background:var(--secondary-background-color); }
        .interval-ctrl { display:inline-flex; align-items:center; gap:4px; font-size:12px; color:var(--secondary-text-color); }
        .iv-btn { border:0; background:transparent; padding:2px 6px; cursor:pointer; color:var(--primary-text-color); font-size:14px; border-radius:4px; line-height:1; }
        .iv-btn:hover { background:var(--secondary-background-color); }
        .iv-val { min-width:36px; text-align:center; font-size:11px; }
        .stage { position:relative; background:#000; aspect-ratio:4/3; width:100%; overflow:hidden; display:flex; align-items:center; justify-content:center; }
        .stage ha-camera-stream, .stage video, .stage img.snap { width:100%; height:100%; object-fit:contain; background:#000; display:block; }
        /* Native OS fullscreen (desktop only) */
        :host(:fullscreen) ha-card { display:flex; flex-direction:column; width:100vw; height:100vh; overflow:hidden; }
        :host(:fullscreen) .stage { aspect-ratio:unset; flex:1; min-height:0; overflow:hidden; }
        :host(:fullscreen) .stage ha-camera-stream, :host(:fullscreen) .stage video, :host(:fullscreen) .stage img.snap { width:100%; height:100%; object-fit:contain; }
        /* In-window fullscreen — fixed overlay covering entire browser viewport */
        :host(.wfs) { position:fixed; inset:0; z-index:9999; display:block; }
        :host(.wfs) ha-card { display:flex; flex-direction:column; width:100%; height:100%; overflow:hidden; border-radius:0; }
        :host(.wfs) .stage { aspect-ratio:unset; flex:1; min-height:0; overflow:hidden; }
        :host(.wfs) .stage ha-camera-stream, :host(.wfs) .stage video, :host(.wfs) .stage img.snap { width:100%; height:100%; object-fit:contain; }
        .status { position:absolute; color:#eee; font-size:14px; text-align:center; padding:0 16px; }
        .status.err { color:#ff8a80; }
        .ffmpeg-hint { padding:8px 12px; font-size:12px; color:var(--secondary-text-color); display:flex; align-items:center; gap:6px; border-top:1px solid var(--divider-color,#e0e0e0); }
        .ffmpeg-hint a { color:var(--primary-color,#03a9f4); text-decoration:none; }
        .ffmpeg-hint a:hover { text-decoration:underline; }
        .hidden { display:none !important; }
      </style>
      <ha-card>
        <div class="bar">
          <span class="title"></span>
          <span class="seg">
            <button data-src="lan">LAN</button>
            <button data-src="cloud">Cloud</button>
          </span>
          <span class="seg mode-seg">
            <button data-mode="stream" title="Smooth continuous stream">▶</button>
            <button data-mode="snap" title="Periodic snapshots — adjustable FPS">📷</button>
          </span>
          <select class="quality-sel hidden" title="Stream smoothness — longer buffers give smoother video but add a few seconds of delay. Shorter buffers are more responsive but may stutter briefly between clips.">
            <option value="0.5">0.5s</option>
            <option value="1">1s</option>
            <option value="2" selected>2s</option>
            <option value="4">4s</option>
          </select>
          <span class="interval-ctrl hidden">
            <button class="iv-btn" data-delta="-0.5">−</button>
            <span class="iv-val"></span>
            <button class="iv-btn" data-delta="0.5">+</button>
          </span>
          <button class="fs-btn wfs-btn" title="Expand in window">⤡</button>
          <button class="fs-btn nfs-btn" title="Fullscreen">⛶</button>
        </div>
        <div class="stage">
          <ha-camera-stream class="lan hidden"></ha-camera-stream>
          <img class="snap hidden" alt="">
          <video class="cloud hidden" autoplay playsinline muted></video>
          <div class="status hidden"></div>
        </div>
        <div class="ffmpeg-hint hidden">
          ℹ️ Install the
          <a href="https://my.home-assistant.io/redirect/supervisor_addon/?addon=core_ffmpeg" target="_blank" rel="noopener">FFmpeg add-on</a>
          to enable stream quality settings and smoother video.
        </div>
      </ha-card>`;
    this._els = {
      title: root.querySelector(".title"),
      seg: root.querySelectorAll(".seg:not(.mode-seg) button"),
      modeSeg: root.querySelectorAll(".mode-seg button"),
      lanStream: root.querySelector("ha-camera-stream.lan"),
      snapImg: root.querySelector("img.snap"),
      video: root.querySelector("video.cloud"),
      status: root.querySelector(".status"),
      wfsBtn: root.querySelector(".wfs-btn"),
      nfsBtn: root.querySelector(".nfs-btn"),
      intervalCtrl: root.querySelector(".interval-ctrl"),
      intervalVal: root.querySelector(".iv-val"),
      qualitySel: root.querySelector(".quality-sel"),
      ffmpegHint: root.querySelector(".ffmpeg-hint"),
    };
    this._els.title.textContent = this._config.title || "Lymow Camera";
    this._els.seg.forEach((b) => b.addEventListener("click", () => this._select(b.dataset.src)));
    this._els.modeSeg.forEach((b) => b.addEventListener("click", () => this._setLanMode(b.dataset.mode)));
    root.querySelectorAll(".iv-btn").forEach((b) => {
      b.addEventListener("click", () => {
        this._snapFps = Math.round(Math.max(0.5, Math.min(5, this._snapFps + parseFloat(b.dataset.delta))) * 2) / 2;
        this._updateIntervalDisplay();
        // Restart snap workers with new rate if already running
        if (this._lanActive && this._lanMode === "snap") {
          this._lanActive = false;
          this._renderLan();
        }
      });
    });
    this._updateModeUI();
    this._updateIntervalDisplay();
    this._els.qualitySel.addEventListener("change", () => this._setHlsQuality(parseFloat(this._els.qualitySel.value)));

    this._els.wfsBtn.addEventListener("click", () => this._toggleWindowFS());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.classList.contains("wfs")) this._toggleWindowFS(false);
    });

    if (!document.fullscreenEnabled) {
      this._els.nfsBtn.classList.add("hidden");
    } else {
      this._els.nfsBtn.addEventListener("click", () => {
        if (!document.fullscreenElement) this.requestFullscreen().catch(() => {});
        else document.exitFullscreen().catch(() => {});
      });
    }
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

  _setLanMode(mode) {
    if (this._source !== "lan") { this._lanMode = mode; this._updateModeUI(); return; }
    const wasStream = this._lanMode === "stream";
    this._stopLan(); // recycles ha-camera-stream when switching away from stream
    this._lanMode = mode;
    this._updateModeUI();
    this._lanActive = false;
    // Show the correct element before rendering
    if (mode === "snap") {
      this._els.lanStream.classList.add("hidden");
      this._els.snapImg.classList.remove("hidden");
    } else {
      this._els.snapImg.classList.add("hidden");
      this._els.lanStream.classList.remove("hidden");
    }
    this._renderLan();
  }

  _updateModeUI() {
    const isLan = this._source === "lan";
    this._els.modeSeg.forEach((b) => b.classList.toggle("on", b.dataset.mode === this._lanMode));
    this._els.modeSeg.forEach((b) => b.parentElement.style.display = isLan ? "" : "none");
    this._els.intervalCtrl.classList.toggle("hidden", !(isLan && this._lanMode === "snap"));
    // Quality selector and ffmpeg hint depend on both mode AND proxy state
    this._updateProxyHint();
  }

  _updateProxyHint() {
    // Quality selector and FFmpeg hint are no longer relevant for the live stream
    // (stream_source() uses direct RTSP, not the HLS proxy). Hide both permanently.
    this._els.qualitySel?.classList.add("hidden");
    this._els.ffmpegHint?.classList.add("hidden");
  }

  _setHlsQuality(secs) {
    const eid = this._config.camera_entity;
    if (!eid || !this._hass) return;

    // Block hass-setter from restarting the stream mid-wait
    this._restarting = true;
    this._stopLan();
    this._setStatus("Restarting stream…");

    this._hass.callService("lymow", "set_hls_quality", { entity_id: eid, segment_seconds: secs })
      .then(() => {
        // Wait for proxy to stop, restart and write its first segments (~8s)
        setTimeout(() => {
          this._restarting = false;
          this._setStatus("");
          this._lanActive = false;
          if (this._source === "lan" && this._lanMode === "stream") {
            this._recycleLanStream();
            this._els.lanStream.classList.remove("hidden");
            this._renderLan();
          }
        }, 9000);
      })
      .catch(e => {
        this._restarting = false;
        this._setStatus(String(e), true);
      });
  }

  _updateIntervalDisplay() {
    this._els.intervalVal.textContent = `${this._snapFps % 1 === 0 ? this._snapFps : this._snapFps.toFixed(1)} fps`;
  }

  _select(src) {
    this._source = src;
    this._els.seg.forEach((b) => b.classList.toggle("on", b.dataset.src === src));
    this._updateModeUI();
    if (src === "lan") {
      this._stopCloud();
      this._els.video.classList.add("hidden");
      this._renderLan(); // _renderLan manages lanStream visibility itself
    } else {
      this._stopLan();
      this._els.lanStream.classList.add("hidden");
      this._els.video.classList.remove("hidden");
      this._startCloud();
    }
  }

  // ---- LAN: stream mode (ha-camera-stream) -----------------------------------
  // ha-camera-stream uses go2rtc WebRTC, fed from our local HLS proxy via
  // stream_source(). The proxy provides clean keyframe-aligned segments so
  // go2rtc gets no green frames. Segment duration controls stutter frequency.
  //
  // ---- LAN: snap mode (JPEG polling pipeline) --------------------------------
  // Single loop polling HA's camera proxy at the target FPS.
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

    if (this._lanMode === "snap") {
      this._els.lanStream.classList.add("hidden");
      this._els.snapImg.classList.remove("hidden");
      this._startSnapWorkers(eid, st);
    } else {
      this._els.snapImg.classList.add("hidden");
      this._els.lanStream.classList.remove("hidden");
      if (this._els.lanStream.tagName === "HA-CAMERA-STREAM") {
        this._els.lanStream.hass = this._hass;
        this._els.lanStream.stateObj = st;
      }
    }
  }

  _startSnapWorkers(eid, st) {
    const token = st.attributes.access_token;
    const base = `/api/camera_proxy/${eid}?token=${token}`;
    const img = this._els.snapImg;
    const targetMs = () => 1000 / this._snapFps;

    // Single polling loop: fires at target FPS, waits for each response
    // before scheduling the next. Shows achieved FPS in the label.
    const run = () => {
      if (!this._lanActive || this._lanMode !== "snap") return;
      const started = Date.now();
      const next = new Image();
      next.onload = () => {
        if (!this._lanActive || this._lanMode !== "snap") return;
        img.src = next.src;
        const elapsed = Date.now() - started;
        const wait = Math.max(0, targetMs() - elapsed);
        setTimeout(run, wait);
      };
      next.onerror = () => { if (this._lanActive) setTimeout(run, 1000); };
      next.src = `${base}&_=${Date.now()}`;
    };
    run();
  }

  _stopLan() {
    this._lanActive = false;
    if (this._els?.snapImg) this._els.snapImg.src = "";
    this._recycleLanStream();
  }

  // Replace with a fresh ha-camera-stream so the next _renderLan() starts a
  // clean WebRTC negotiation instead of reusing a stale session.
  _recycleLanStream() {
    const old = this._els?.lanStream;
    if (!old) return;
    if (old.tagName === "HA-CAMERA-STREAM") { old.stateObj = null; old.hass = null; }
    const fresh = document.createElement("ha-camera-stream");
    fresh.className = "lan hidden";
    old.parentNode?.replaceChild(fresh, old);
    this._els.lanStream = fresh;
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
