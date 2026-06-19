/*
 * Lymow drive card — dual virtual joysticks for BLE manual drive.
 *
 * Config:
 *   type: custom:lymow-drive-card
 *   mower_entity: lawn_mower.lymow_THING   # required — target for ble_drive service
 *   camera_entity: camera.THING_camera     # optional — embeds camera feed above joysticks
 *   title: Drive                            # optional
 *
 * How it works:
 *   The card streams lymow.ble_drive service calls at ~10 Hz while either
 *   joystick is held. Each call covers a 150 ms window; the backend clamps
 *   velocity and duration.  Releasing all joysticks sends one final stop call.
 *
 *   Left joystick  → linear (forward/backward, ±0.5 m/s)
 *   Right joystick → angular (left/right, ±0.6 rad/s)
 *   Both together  → combined arc turns
 *
 *   ble_drive auto-discovers the robot by its Bluetooth name (deviceBluetooth
 *   entity attribute) — no BLE address config needed.
 */

const SEND_HZ = 10;
const SEND_INTERVAL_MS = 1000 / SEND_HZ;
const DRIVE_DURATION_S = 0.2; // slightly longer than interval so motion never gaps

class LymowDriveCard extends HTMLElement {
  setConfig(config) {
    if (!config || !(config.mower_entity || config.mower)) {
      throw new Error("lymow-drive-card: 'mower_entity' is required");
    }
    this._config = {
      ...config,
      mower_entity: config.mower_entity || config.mower,
      camera_entity: config.camera_entity || config.camera,
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) return;
    if (this._config.camera_entity && this._camCard) {
      this._camCard.hass = hass;
    }
  }

  connectedCallback() { this._build(); }

  disconnectedCallback() {
    this._stopLoop();
    if (this._camCard) this._camCard.disconnectedCallback?.();
  }

  getCardSize() { return this._config.camera_entity ? 6 : 4; }

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { overflow: hidden; user-select: none; -webkit-user-select: none; }
        .title { font-weight: 600; padding: 12px 16px 4px; }
        .camera-slot { width: 100%; }
        .controls { display: flex; align-items: center; justify-content: space-around; padding: 16px 8px; gap: 8px; }
        .joystick-wrap { display: flex; flex-direction: column; align-items: center; gap: 6px; }
        .joystick-label { font-size: 11px; color: var(--secondary-text-color); text-align: center; }
        .joystick { position: relative; width: 120px; height: 120px; border-radius: 50%;
                    background: var(--secondary-background-color);
                    border: 2px solid var(--divider-color, #e0e0e0);
                    touch-action: none; cursor: grab; }
        .joystick.active { border-color: var(--primary-color, #03a9f4); }
        .thumb { position: absolute; width: 40px; height: 40px; border-radius: 50%;
                 background: var(--primary-color, #03a9f4); opacity: 0.85;
                 transform: translate(-50%, -50%); left: 50%; top: 50%;
                 pointer-events: none; transition: background 0.15s; }
        .joystick.active .thumb { background: var(--primary-color, #03a9f4); }
        .status { padding: 4px 16px 12px; font-size: 12px; color: var(--secondary-text-color); min-height: 20px; }
        .status.err { color: var(--error-color, #f44336); }
      </style>
      <ha-card>
        <div class="title"></div>
        <div class="camera-slot"></div>
        <div class="controls">
          <div class="joystick-wrap">
            <div class="joystick" id="joy-left" touch-action="none">
              <div class="thumb"></div>
            </div>
            <span class="joystick-label">Forward / Backward</span>
          </div>
          <div class="joystick-wrap">
            <div class="joystick" id="joy-right" touch-action="none">
              <div class="thumb"></div>
            </div>
            <span class="joystick-label">Left / Right</span>
          </div>
        </div>
        <div class="status"></div>
      </ha-card>`;

    root.querySelector(".title").textContent = this._config.title || "Drive";

    // Embed the camera card if camera_entity is configured.
    if (this._config.camera_entity) {
      customElements.whenDefined("lymow-camera-card").then(() => {
        const cam = document.createElement("lymow-camera-card");
        cam.setConfig({
          mower_entity: this._config.mower_entity,
          camera_entity: this._config.camera_entity,
          default_source: "lan",
        });
        if (this._hass) cam.hass = this._hass;
        this._camCard = cam;
        root.querySelector(".camera-slot").appendChild(cam);
      });
    }

    this._els = {
      joyLeft: root.getElementById("joy-left"),
      joyRight: root.getElementById("joy-right"),
      status: root.querySelector(".status"),
    };

    this._linear = 0;
    this._angular = 0;
    this._loopTimer = null;

    this._bindJoystick(this._els.joyLeft, (nx, ny) => { this._linear = -ny; }); // up = forward
    this._bindJoystick(this._els.joyRight, (nx, ny) => { this._angular = -nx; }); // left = +angular

    this._built = true;
  }

  // Bind pointer events to a joystick element. callback(normX, normY) where
  // both values are in [-1, +1] relative to the joystick centre.
  _bindJoystick(el, onChange) {
    const thumb = el.querySelector(".thumb");
    const R = 60; // half of joystick width
    const THUMB_R = 20;
    let pointerId = null;

    const move = (e) => {
      if (e.pointerId !== pointerId) return;
      const rect = el.getBoundingClientRect();
      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      let dx = e.clientX - cx;
      let dy = e.clientY - cy;
      const dist = Math.hypot(dx, dy);
      const maxDist = R - THUMB_R;
      if (dist > maxDist) { dx = dx / dist * maxDist; dy = dy / dist * maxDist; }
      thumb.style.left = `${50 + (dx / R) * 50}%`;
      thumb.style.top = `${50 + (dy / R) * 50}%`;
      onChange(dx / maxDist, dy / maxDist);
      this._ensureLoop();
    };

    const end = (e) => {
      if (e.pointerId !== pointerId) return;
      pointerId = null;
      el.classList.remove("active");
      thumb.style.left = "50%";
      thumb.style.top = "50%";
      onChange(0, 0);
      if (this._linear === 0 && this._angular === 0) this._stopLoop();
    };

    el.addEventListener("pointerdown", (e) => {
      if (pointerId !== null) return;
      pointerId = e.pointerId;
      el.setPointerCapture(e.pointerId);
      el.classList.add("active");
      move(e);
    });
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }

  _ensureLoop() {
    if (this._loopTimer !== null) return;
    this._sendDrive(); // immediate first frame
    this._loopTimer = setInterval(() => this._sendDrive(), SEND_INTERVAL_MS);
  }

  _stopLoop() {
    if (this._loopTimer !== null) {
      clearInterval(this._loopTimer);
      this._loopTimer = null;
    }
    if (this._hass && this._config.mower_entity) {
      // Send one stop frame
      this._callDrive(0, 0).catch(() => {});
    }
  }

  _sendDrive() {
    if (!this._hass) return;
    this._callDrive(this._linear, this._angular).catch((err) => {
      this._setStatus(String(err), true);
    });
  }

  _callDrive(linear, angular) {
    this._setStatus(`linear ${linear >= 0 ? "+" : ""}${linear.toFixed(2)}  angular ${angular >= 0 ? "+" : ""}${angular.toFixed(2)}`);
    return this._hass.callService("lymow", "ble_drive", {
      linear: parseFloat(linear.toFixed(3)),
      angular: parseFloat(angular.toFixed(3)),
      duration: DRIVE_DURATION_S,
    }, { entity_id: this._config.mower_entity });
  }

  _setStatus(msg, isErr = false) {
    this._els.status.textContent = msg;
    this._els.status.classList.toggle("err", isErr);
  }
}

if (!customElements.get("lymow-drive-card")) {
  customElements.define("lymow-drive-card", LymowDriveCard);
}

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === "lymow-drive-card")) {
  window.customCards.push({
    type: "lymow-drive-card",
    name: "Lymow Drive",
    description: "Dual virtual joysticks for BLE manual drive of the Lymow robot.",
  });
}

console.info("%c LYMOW-DRIVE-CARD ", "background:#1976d2;color:#fff;border-radius:3px");
