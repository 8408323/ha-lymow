/*
 * Lymow control card — status + Mow / Pause / Resume / Dock for a Lymow mower.
 *
 * Config:
 *   type: custom:lymow-control-card
 *   mower_entity: lawn_mower.lymow_THING   # required
 *   title: Mower                            # optional
 *
 * Reads the lawn_mower entity plus the device's battery / mow-progress / error
 * sensors (auto-discovered by the device base name). Buttons call the standard
 * lawn_mower services plus lymow.resume.
 */

const STATE_LABEL = {
  mowing: "Mowing",
  paused: "Paused",
  docked: "Docked",
  returning: "Returning to dock",
  error: "Error",
};

class LymowControlCard extends HTMLElement {
  setConfig(config) {
    if (!config || !(config.mower_entity || config.mower)) {
      throw new Error("lymow-control-card: 'mower_entity' is required");
    }
    this._config = { ...config, mower_entity: config.mower_entity || config.mower };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._built) this._refresh();
  }

  connectedCallback() {
    this._build();
  }
  getCardSize() {
    return 3;
  }

  _base() {
    return this._config.mower_entity.split(".")[1];
  }

  _sensor(suffix) {
    // Find sensor.<...base...>_<suffix> for this device, tolerating name-based slugs.
    if (!this._hass) return undefined;
    const base = this._base();
    const id = Object.keys(this._hass.states).find(
      (e) => e.startsWith("sensor.") && e.includes(base) && e.endsWith(`_${suffix}`)
    );
    return id ? this._hass.states[id] : undefined;
  }

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { padding: 16px; }
        .title { font-weight: 600; font-size: 16px; margin-bottom: 12px; }
        .status-row { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 8px; }
        .status { font-size: 15px; font-weight: 500; }
        .battery { font-size: 13px; color: var(--secondary-text-color); }
        .bar { height: 6px; border-radius: 3px; background: var(--divider-color, #e0e0e0); overflow: hidden; margin-bottom: 6px; }
        .bar > span { display: block; height: 100%; background: var(--primary-color, #03a9f4); width: 0%; transition: width 0.3s; }
        .sub { font-size: 12px; color: var(--secondary-text-color); min-height: 16px; margin-bottom: 12px; }
        .sub.err { color: var(--error-color, #f44336); }
        .btns { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        button { border: 0; border-radius: 18px; padding: 10px; font-size: 14px; font-weight: 500; cursor: pointer; color: #fff; }
        button:disabled { opacity: 0.45; cursor: default; }
        .mow { background: var(--primary-color, #03a9f4); }
        .pause { background: var(--warning-color, #ff9800); }
        .resume { background: var(--success-color, #4caf50); }
        .dock { background: var(--secondary-text-color, #607d8b); }
      </style>
      <ha-card>
        <div class="title"></div>
        <div class="status-row"><span class="status"></span><span class="battery"></span></div>
        <div class="bar"><span></span></div>
        <div class="sub"></div>
        <div class="btns">
          <button class="mow">Mow</button>
          <button class="pause">Pause</button>
          <button class="resume">Resume</button>
          <button class="dock">Dock</button>
        </div>
      </ha-card>`;
    this._root = root;
    root.querySelector(".title").textContent = this._config.title || "Mower";
    root.querySelector(".mow").addEventListener("click", () => this._call("lawn_mower", "start_mowing"));
    root.querySelector(".pause").addEventListener("click", () => this._call("lawn_mower", "pause"));
    root.querySelector(".resume").addEventListener("click", () => this._call("lymow", "resume"));
    root.querySelector(".dock").addEventListener("click", () => this._call("lawn_mower", "dock"));
    this._built = true;
    this._refresh();
  }

  _refresh() {
    if (!this._root || !this._hass) return;
    const mower = this._hass.states[this._config.mower_entity];
    const state = mower ? mower.state : "unavailable";
    this._root.querySelector(".status").textContent = STATE_LABEL[state] || state;

    const battery = this._sensor("battery");
    this._root.querySelector(".battery").textContent =
      battery && battery.state !== "unknown" ? `${Math.round(Number(battery.state))}%` : "";

    const progress = this._sensor("mow_progress");
    const pct = progress ? Math.max(0, Math.min(100, Number(progress.state) || 0)) : 0;
    this._root.querySelector(".bar > span").style.width = `${pct}%`;

    const error = this._sensor("error_code");
    const errCode = error ? Number(error.state) : 0;
    const sub = this._root.querySelector(".sub");
    if (errCode && errCode !== 0) {
      sub.textContent = `Error ${errCode}`;
      sub.classList.add("err");
    } else {
      sub.textContent = state === "mowing" ? `${Math.round(pct)}% mowed` : "";
      sub.classList.remove("err");
    }

    // Enable/disable buttons by state so the card only offers valid actions.
    const b = (cls) => this._root.querySelector(`.${cls}`);
    b("mow").disabled = state === "mowing" || state === "unavailable";
    b("pause").disabled = state !== "mowing" && state !== "returning";
    b("resume").disabled = state !== "paused";
    b("dock").disabled = state === "docked" || state === "unavailable";
  }

  _call(domain, service) {
    if (!this._hass) return;
    this._hass.callService(domain, service, { entity_id: this._config.mower_entity });
  }
}

customElements.define("lymow-control-card", LymowControlCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "lymow-control-card",
  name: "Lymow Control Card",
  description: "Status + Mow / Pause / Resume / Dock for a Lymow mower.",
});
