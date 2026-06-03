/*
 * Lymow camera card — live onboard camera with a LAN / Cloud source switch.
 *
 * Config:
 *   type: custom:lymow-camera-card
 *   mower_entity:  lawn_mower.lymow_THING   # required (used for the cloud session)
 *   camera_entity: camera.THING_camera      # required for the LAN view
 *   title: Lymow Camera                     # optional
 *   default_source: lan | cloud             # optional (default: lan)
 *
 * Two genuinely separate transport paths:
 *   LAN   — <ha-camera-stream> which HA tries in order: WebRTC (go2rtc) →
 *           HLS (stream component) → MJPEG fallback. Smooth video when the
 *           stream component is active; no ffmpeg-reconnect-per-frame latency.
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
        .stage { position:relative; background:#000; aspect-ratio:4/3; display:flex; align-items:center; justify-content:center; }
        .stage video, .stage ha-camera-stream { width:100%; height:100%; object-fit:contain; background:#000; }
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
        </div>
        <div class="stage">
          <ha-camera-stream class="lan hidden"></ha-camera-stream>
          <video class="cloud hidden" autoplay playsinline muted></video>
          <div class="status"></div>
        </div>
      </ha-card>`;
    this._els = {
      title: root.querySelector(".title"),
      seg: root.querySelectorAll(".seg button"),
      lanStream: root.querySelector("ha-camera-stream.lan"),
      video: root.querySelector("video.cloud"),
      status: root.querySelector(".status"),
    };
    this._els.title.textContent = this._config.title || "Lymow Camera";
    this._els.seg.forEach((b) => b.addEventListener("click", () => this._select(b.dataset.src)));
    this._built = true;
  }

  _setStatus(msg, isErr = false) {
    this._els.status.textContent = msg || "";
    this._els.status.classList.toggle("err", isErr);
    this._els.status.classList.toggle("hidden", !msg);
  }

  _select(src) {
    this._source = src;
    this._els.seg.forEach((b) => b.classList.toggle("on", b.dataset.src === src));
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

  // ---- LAN: ha-camera-stream (WebRTC → HLS → MJPEG, chosen by HA) -----------
  _renderLan() {
    const eid = this._config.camera_entity;
    const st = eid && this._hass && this._hass.states[eid];
    if (!st) {
      this._setStatus(eid ? `${eid} unavailable` : "Set camera_entity for LAN view", true);
      this._stopLan();
      return;
    }
    if (st.state === "unavailable") {
      this._setStatus("Robot offline (LAN) — try Cloud", true);
      this._stopLan();
      return;
    }
    this._setStatus("");
    // ha-camera-stream picks the best available path automatically
    this._els.lanStream.hass = this._hass;
    this._els.lanStream.stateObj = st;
  }

  _stopLan() {
    if (this._els && this._els.lanStream) {
      this._els.lanStream.stateObj = null;
    }
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
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (!msg.messagePayload) return;
      let payload;
      try {
        payload = JSON.parse(atob(msg.messagePayload));
      } catch {
        return;
      }
      const kind = msg.messageType || msg.action;
      if (kind === "SDP_ANSWER") {
        await pc.setRemoteDescription({ type: "answer", sdp: payload.sdp });
      } else if (kind === "ICE_CANDIDATE") {
        try {
          await pc.addIceCandidate(payload);
        } catch {
          /* late/duplicate candidate */
        }
      }
    };
    ws.onerror = () => {
      if (token === this._cloudToken) this._setStatus("Signaling socket error", true);
    };
    ws.onopen = async () => {
      try {
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        ws.send(
          JSON.stringify({
            action: "SDP_OFFER",
            messagePayload: btoa(JSON.stringify({ type: "offer", sdp: pc.localDescription.sdp })),
          })
        );
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
    if (this._frameTimer) {
      clearTimeout(this._frameTimer);
      this._frameTimer = null;
    }
    if (this._ws) {
      try {
        this._ws.close();
      } catch {
        /* already closed */
      }
      this._ws = null;
    }
    if (this._pc) {
      try {
        this._pc.close();
      } catch {
        /* already closed */
      }
      this._pc = null;
    }
    if (this._els && this._els.video) this._els.video.srcObject = null;
  }
}

customElements.define("lymow-camera-card", LymowCameraCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "lymow-camera-card",
  name: "Lymow Camera",
  description: "Onboard camera with a LAN (WebRTC/HLS/MJPEG via ha-camera-stream) / Cloud (KVS WebRTC) source switch.",
});

console.info("%c LYMOW-CAMERA-CARD ", "background:#43a047;color:#fff;border-radius:3px");
