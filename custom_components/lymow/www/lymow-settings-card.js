/*
 * Lymow settings card — advanced settings not exposed as HA entities.
 *
 * Config:
 *   type: custom:lymow-settings-card
 *   mower_entity: lawn_mower.lymow_THING   # required
 *   title: Advanced Settings                # optional
 *   sections: [headlight, run_time, device, pin, rtk, geofence]  # optional — all shown by default
 *
 * Services called: lymow.set_headlight_schedule, set_run_time_config,
 *   set_device_name, set_pin, bind_rtk, set_geofence
 */

const ALL_SECTIONS = ["headlight", "run_time", "device", "pin", "rtk", "geofence"];

class LymowSettingsCard extends HTMLElement {
  setConfig(config) {
    if (!config || !(config.mower_entity || config.mower)) {
      throw new Error("lymow-settings-card: 'mower_entity' is required");
    }
    this._config = {
      ...config,
      mower_entity: config.mower_entity || config.mower,
      sections: config.sections || ALL_SECTIONS,
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._built) this._populateFromState();
  }

  connectedCallback() { this._build(); }
  getCardSize() { return 5; }

  _thing() {
    return this._config.mower_entity.split(".")[1];
  }

  _robotConfig() {
    const states = this._hass?.states || {};
    // robotConfig is in coordinator data, not directly in HA states.
    // Best we can do: read from any sensor that might expose it.
    return null;
  }

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    const sections = this._config.sections;

    root.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .card-header { font-size:16px; font-weight:600; padding:12px 16px 4px; }
        .section { border-bottom:1px solid var(--divider-color,#e0e0e0); padding:12px 16px; }
        .section:last-of-type { border-bottom:0; }
        .sec-title { font-weight:600; font-size:14px; margin-bottom:10px; display:flex; align-items:center; gap:6px; }
        .sec-icon { font-size:16px; }
        .field-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
        .label { font-size:13px; color:var(--secondary-text-color); min-width:100px; }
        input[type=text], input[type=password], input[type=number], input[type=time] {
          background:var(--input-fill-color,var(--secondary-background-color));
          color:var(--primary-text-color);
          border:1px solid var(--divider-color,#e0e0e0); border-radius:4px;
          padding:4px 8px; font-size:14px; flex:1; min-width:0;
        }
        input[type=range] { flex:1; }
        .range-val { font-size:13px; min-width:40px; text-align:right; color:var(--secondary-text-color); }
        .eye-btn { border:0; background:transparent; cursor:pointer; padding:2px 4px; font-size:16px; color:var(--secondary-text-color); }
        .apply-btn { border:0; background:var(--primary-color,#03a9f4); color:#fff; padding:5px 14px; border-radius:14px; cursor:pointer; font-size:13px; white-space:nowrap; }
        .pin-wrap { display:flex; align-items:center; gap:4px; flex:1; }
        .status { font-size:11px; color:var(--secondary-text-color); margin-top:4px; min-height:16px; }
        .status.err { color:var(--error-color,#f44336); }
        .status.ok { color:var(--success-color,#4caf50); }
      </style>
      <ha-card>
        <div class="card-header">${this._config.title || "Advanced Settings"}</div>
        ${sections.includes("headlight") ? this._tplHeadlight() : ""}
        ${sections.includes("run_time") ? this._tplRunTime() : ""}
        ${sections.includes("device") ? this._tplDevice() : ""}
        ${sections.includes("pin") ? this._tplPin() : ""}
        ${sections.includes("rtk") ? this._tplRtk() : ""}
        ${sections.includes("geofence") ? this._tplGeofence() : ""}
      </ha-card>`;

    this._root = root;

    if (sections.includes("headlight")) this._wireHeadlight();
    if (sections.includes("run_time")) this._wireRunTime();
    if (sections.includes("device")) this._wireDevice();
    if (sections.includes("pin")) this._wirePin();
    if (sections.includes("rtk")) this._wireRtk();
    if (sections.includes("geofence")) this._wireGeofence();

    this._built = true;
    this._populateFromState();
  }

  // ── Template fragments ──────────────────────────────────────────────────

  _tplHeadlight() {
    return `<div class="section" id="sec-headlight">
      <div class="sec-title"><span class="sec-icon">💡</span> Headlight schedule</div>
      <div class="field-row">
        <span class="label">Enable</span>
        <ha-switch id="hl-enable"></ha-switch>
      </div>
      <div class="field-row" id="hl-times">
        <span class="label">On at</span>
        <input type="time" id="hl-start" value="21:00">
        <span class="label" style="min-width:40px;text-align:center">Off at</span>
        <input type="time" id="hl-end" value="23:00">
      </div>
      <div class="field-row">
        <span class="label"></span>
        <button class="apply-btn" id="hl-save">Save</button>
      </div>
      <div class="status" id="hl-status"></div>
    </div>`;
  }

  _tplRunTime() {
    return `<div class="section" id="sec-run-time">
      <div class="sec-title"><span class="sec-icon">⚙️</span> Run-time config</div>
      <div class="field-row">
        <span class="label">Cut height</span>
        <input type="range" id="rt-cut-height" min="20" max="100" step="5" value="60">
        <span class="range-val" id="rt-cut-height-val">60 mm</span>
      </div>
      <div class="field-row">
        <span class="label">Move speed</span>
        <input type="range" id="rt-move-speed" min="0.1" max="1.5" step="0.1" value="0.8">
        <span class="range-val" id="rt-move-speed-val">0.8 m/s</span>
      </div>
      <div class="field-row">
        <span class="label">Cut speed</span>
        <input type="range" id="rt-cut-speed" min="0" max="1000" step="50" value="500">
        <span class="range-val" id="rt-cut-speed-val">500</span>
      </div>
      <div class="field-row">
        <span class="label"></span>
        <button class="apply-btn" id="rt-save">Apply</button>
      </div>
      <div class="status" id="rt-status"></div>
    </div>`;
  }

  _tplDevice() {
    return `<div class="section" id="sec-device">
      <div class="sec-title"><span class="sec-icon">🏷️</span> Device name</div>
      <div class="field-row">
        <span class="label">Name</span>
        <input type="text" id="dev-name" placeholder="e.g. Lymow One">
        <button class="apply-btn" id="dev-save">Rename</button>
      </div>
      <div class="status" id="dev-status"></div>
    </div>`;
  }

  _tplPin() {
    return `<div class="section" id="sec-pin">
      <div class="sec-title"><span class="sec-icon">🔐</span> PIN code</div>
      <div class="field-row">
        <span class="label">New PIN</span>
        <div class="pin-wrap">
          <input type="password" id="pin-input" placeholder="4 digits" maxlength="4" pattern="[0-9]{4}">
          <button class="eye-btn" id="pin-eye">👁️</button>
        </div>
        <button class="apply-btn" id="pin-save">Set PIN</button>
      </div>
      <div class="status" id="pin-status">PIN is write-only — current PIN is not shown by the robot.</div>
    </div>`;
  }

  _tplRtk() {
    return `<div class="section" id="sec-rtk">
      <div class="sec-title"><span class="sec-icon">📡</span> RTK base station</div>
      <div class="field-row">
        <span class="label">Base ID</span>
        <input type="text" id="rtk-base-id" placeholder="e.g. LK000000000000">
        <button class="apply-btn" id="rtk-bind">Bind</button>
      </div>
      <div class="status" id="rtk-status"></div>
    </div>`;
  }

  _tplGeofence() {
    return `<div class="section" id="sec-geofence">
      <div class="sec-title"><span class="sec-icon">🗺️</span> Geofence centre</div>
      <div class="field-row">
        <span class="label">Latitude</span>
        <input type="number" id="geo-lat" step="0.000001" placeholder="59.123456">
      </div>
      <div class="field-row">
        <span class="label">Longitude</span>
        <input type="number" id="geo-lon" step="0.000001" placeholder="18.123456">
      </div>
      <div class="field-row">
        <span class="label">Name</span>
        <input type="text" id="geo-name" placeholder="Home">
        <button class="apply-btn" id="geo-save">Set</button>
      </div>
      <div class="status" id="geo-status">Radius is set via the Geofence radius number entity.</div>
    </div>`;
  }

  // ── Wire-up ─────────────────────────────────────────────────────────────

  _wireHeadlight() {
    const root = this._root;
    const tog = root.getElementById("hl-enable");
    const timesRow = root.getElementById("hl-times");
    const showTimes = () => timesRow.style.display = tog.checked ? "" : "none";
    tog.addEventListener("change", showTimes);
    showTimes();
    root.getElementById("hl-save").addEventListener("click", () => {
      const enabled = !!tog.checked;
      const start = root.getElementById("hl-start").value;
      const end = root.getElementById("hl-end").value;
      const data = { enabled };
      if (enabled) { data.start = start; data.end = end; }
      this._call("set_headlight_schedule", data, "hl-status");
    });
  }

  _wireRunTime() {
    const root = this._root;
    [["rt-cut-height", "rt-cut-height-val", " mm"], ["rt-move-speed", "rt-move-speed-val", " m/s"], ["rt-cut-speed", "rt-cut-speed-val", ""]].forEach(([id, valId, suffix]) => {
      const inp = root.getElementById(id);
      const val = root.getElementById(valId);
      inp.addEventListener("input", () => { val.textContent = parseFloat(inp.value).toFixed(id === "rt-move-speed" ? 1 : 0) + suffix; });
    });
    root.getElementById("rt-save").addEventListener("click", () => {
      this._call("set_run_time_config", {
        cut_height: parseInt(root.getElementById("rt-cut-height").value),
        move_speed: parseFloat(root.getElementById("rt-move-speed").value),
        cut_speed: parseInt(root.getElementById("rt-cut-speed").value),
      }, "rt-status");
    });
  }

  _wireDevice() {
    this._root.getElementById("dev-save").addEventListener("click", () => {
      const name = this._root.getElementById("dev-name").value.trim();
      if (!name) { this._setStatus("dev-status", "Enter a name.", true); return; }
      this._call("set_device_name", { name }, "dev-status");
    });
  }

  _wirePin() {
    const root = this._root;
    root.getElementById("pin-eye").addEventListener("click", () => {
      const inp = root.getElementById("pin-input");
      inp.type = inp.type === "password" ? "text" : "password";
    });
    root.getElementById("pin-save").addEventListener("click", () => {
      const pin = root.getElementById("pin-input").value.trim();
      if (!/^\d{4}$/.test(pin)) { this._setStatus("pin-status", "PIN must be exactly 4 digits.", true); return; }
      this._call("set_pin", { pin }, "pin-status");
    });
  }

  _wireRtk() {
    this._root.getElementById("rtk-bind").addEventListener("click", () => {
      const id = this._root.getElementById("rtk-base-id").value.trim();
      if (!id) { this._setStatus("rtk-status", "Enter a base station ID.", true); return; }
      this._call("bind_rtk", { base_id: id }, "rtk-status");
    });
  }

  _wireGeofence() {
    this._root.getElementById("geo-save").addEventListener("click", () => {
      const lat = parseFloat(this._root.getElementById("geo-lat").value);
      const lon = parseFloat(this._root.getElementById("geo-lon").value);
      const name = this._root.getElementById("geo-name").value.trim();
      if (isNaN(lat) || isNaN(lon)) { this._setStatus("geo-status", "Enter valid latitude and longitude.", true); return; }
      const data = { latitude: lat, longitude: lon };
      if (name) data.name = name;
      this._call("set_geofence", data, "geo-status");
    });
  }

  _populateFromState() {
    if (!this._hass || !this._root) return;
    const base = this._thing();
    const states = this._hass.states;
    // Pre-fill device name from any sensor that has it
    const mowerSt = states[this._config.mower_entity];
    const devName = mowerSt?.attributes?.device_name || "";
    const devInput = this._root.getElementById("dev-name");
    if (devInput && devName && !devInput.value) devInput.value = devName;
  }

  _call(service, data, statusId) {
    this._setStatus(statusId, "Saving…");
    return this._hass.callService("lymow", service, data, { entity_id: this._config.mower_entity })
      .then(() => this._setStatus(statusId, "Saved ✓", false, true))
      .catch(e => this._setStatus(statusId, String(e), true));
  }

  _setStatus(id, msg, isErr = false, isOk = false) {
    const el = this._root.getElementById(id);
    if (!el) return;
    el.textContent = msg;
    el.className = "status" + (isErr ? " err" : isOk ? " ok" : "");
    if (isOk) setTimeout(() => { if (el.textContent === msg) { el.textContent = ""; el.className = "status"; } }, 3000);
  }
}

if (!customElements.get("lymow-settings-card")) {
  customElements.define("lymow-settings-card", LymowSettingsCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.find(c => c.type === "lymow-settings-card")) {
  window.customCards.push({ type: "lymow-settings-card", name: "Lymow Advanced Settings", description: "Headlight schedule, run-time config, device name, PIN, RTK bind, geofence." });
}
console.info("%c LYMOW-SETTINGS-CARD ", "background:#37474f;color:#fff;border-radius:3px");
