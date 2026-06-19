/*
 * Lymow schedule card — view and manage mowing schedules.
 *
 * Config:
 *   type: custom:lymow-schedule-card
 *   mower_entity: lawn_mower.lymow_THING   # required
 *   schedule_sensor: sensor.THING_schedules # optional — auto-derived from mower_entity if omitted
 *   title: Schedules                        # optional
 *
 * Reads: sensor.THING_schedules attributes.schedules
 * Writes: lymow.add_schedule, toggle_schedule, delete_schedule, clear_schedules
 */

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

class LymowScheduleCard extends HTMLElement {
  setConfig(config) {
    if (!config || !(config.mower_entity || config.mower)) {
      throw new Error("lymow-schedule-card: 'mower_entity' is required");
    }
    this._config = {
      ...config,
      mower_entity: config.mower_entity || config.mower,
    };
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._built) this._refresh();
  }

  connectedCallback() { this._build(); }
  getCardSize() { return 4; }

  _sensorId() {
    if (this._config.schedule_sensor) return this._config.schedule_sensor;
    // Search hass.states for a sensor with a 'schedules' attribute belonging to this device
    const base = this._config.mower_entity.split(".")[1];
    if (!this._hass) return `sensor.${base}_schedules`;
    const found = Object.keys(this._hass.states).find(
      id => id.startsWith("sensor.") && id.includes(base) &&
            Array.isArray(this._hass.states[id].attributes?.schedules)
    );
    return found || `sensor.${base}_schedules`;
  }

  _schedules() {
    const st = this._hass?.states[this._sensorId()];
    return (st?.attributes?.schedules) || [];
  }

  _zones() {
    // Collect zone switch entities for this device to offer zone selection
    if (!this._hass) return [];
    const base = this._config.mower_entity.split(".")[1];
    return Object.entries(this._hass.states)
      .filter(([id]) => id.startsWith("switch.") && id.includes(base) && id.endsWith("_enabled"))
      .map(([id, st]) => ({
        id: st.attributes?.zone_hash_id || id.split("_").slice(-2, -1)[0],
        name: st.attributes?.friendly_name || id,
      }));
  }

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        ha-card { overflow: hidden; }
        .header { display:flex; align-items:center; justify-content:space-between; padding:12px 16px 0; }
        .title { font-weight:600; font-size:16px; }
        .add-btn { border:0; background:var(--primary-color,#03a9f4); color:#fff; padding:4px 12px; border-radius:16px; cursor:pointer; font-size:13px; }
        .list { padding:8px 0; }
        .row { display:flex; align-items:center; gap:8px; padding:8px 16px; border-bottom:1px solid var(--divider-color,#e0e0e0); }
        .row:last-child { border-bottom:0; }
        .row-info { flex:1; min-width:0; }
        .row-days { font-size:12px; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
        .row-time { font-weight:500; font-size:14px; }
        .row-zones { font-size:11px; color:var(--secondary-text-color); }
        .toggle { width:36px; height:20px; flex-shrink:0; }
        .del-btn { border:0; background:transparent; color:var(--error-color,#f44336); font-size:18px; cursor:pointer; padding:2px 4px; }
        .empty { padding:24px 16px; text-align:center; color:var(--secondary-text-color); font-size:13px; }
        /* Add form */
        .form { padding:12px 16px; border-top:1px solid var(--divider-color,#e0e0e0); }
        .form.hidden { display:none; }
        .form-title { font-weight:600; margin-bottom:8px; }
        .days-row { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px; }
        .day-chip { padding:4px 8px; border-radius:12px; font-size:12px; cursor:pointer; border:1px solid var(--divider-color,#e0e0e0); background:transparent; color:var(--primary-text-color); }
        .day-chip.on { background:var(--primary-color,#03a9f4); color:#fff; border-color:var(--primary-color,#03a9f4); }
        .zone-row { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:8px; }
        .zone-chip { padding:3px 8px; border-radius:12px; font-size:11px; cursor:pointer; border:1px solid var(--divider-color,#e0e0e0); background:transparent; color:var(--primary-text-color); }
        .zone-chip.on { background:var(--secondary-color,#4caf50); color:#fff; border-color:var(--secondary-color,#4caf50); }
        .field-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
        .field-label { font-size:13px; min-width:80px; color:var(--secondary-text-color); }
        input[type=time], input[type=text] { background:var(--card-background-color,#fff); color:var(--primary-text-color); border:1px solid var(--divider-color,#e0e0e0); border-radius:4px; padding:4px 8px; font-size:14px; }
        .repeat-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; font-size:13px; }
        .form-btns { display:flex; gap:8px; }
        .save-btn { border:0; background:var(--primary-color,#03a9f4); color:#fff; padding:6px 16px; border-radius:16px; cursor:pointer; font-size:13px; }
        .cancel-btn { border:0; background:transparent; color:var(--secondary-text-color); padding:6px 16px; border-radius:16px; cursor:pointer; font-size:13px; }
        .status { padding:4px 16px 8px; font-size:12px; color:var(--secondary-text-color); min-height:18px; }
        .status.err { color:var(--error-color,#f44336); }
        .clear-btn { border:0; background:transparent; color:var(--error-color,#f44336); font-size:12px; cursor:pointer; padding:2px 8px; }
      </style>
      <ha-card>
        <div class="header">
          <span class="title"></span>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="clear-btn">Clear all</button>
            <button class="add-btn">+ Add</button>
          </div>
        </div>
        <div class="list"></div>
        <div class="form hidden">
          <div class="form-title">New schedule</div>
          <div class="days-row" id="day-chips"></div>
          <div class="field-row">
            <span class="field-label">Time (local)</span>
            <input type="time" id="time-input" value="08:00">
          </div>
          <div class="field-row">
            <span class="field-label">Zones</span>
            <div class="zone-row" id="zone-chips"><span style="font-size:11px;color:var(--secondary-text-color)">Loading…</span></div>
          </div>
          <div class="repeat-row">
            <input type="checkbox" id="repeat-chk" checked>
            <label for="repeat-chk">Repeat weekly</label>
          </div>
          <div class="form-btns">
            <button class="save-btn">Save</button>
            <button class="cancel-btn">Cancel</button>
          </div>
        </div>
        <div class="status"></div>
      </ha-card>`;

    const root2 = root;
    this._root = root;
    root.querySelector(".title").textContent = this._config.title || "Schedules";
    root.querySelector(".add-btn").addEventListener("click", () => this._toggleForm(true));
    root.querySelector(".cancel-btn").addEventListener("click", () => this._toggleForm(false));
    root.querySelector(".clear-btn").addEventListener("click", () => this._clearAll());
    root.querySelector(".save-btn").addEventListener("click", () => this._saveSchedule());

    // Day chips
    const dayChips = root.getElementById("day-chips");
    DAYS.forEach((d, i) => {
      const btn = document.createElement("button");
      btn.className = "day-chip";
      btn.textContent = d;
      btn.dataset.day = i;
      btn.addEventListener("click", () => btn.classList.toggle("on"));
      dayChips.appendChild(btn);
    });

    this._built = true;
    this._refresh();
  }

  _refresh() {
    if (!this._root) return;
    const schedules = this._schedules();
    const list = this._root.querySelector(".list");
    if (!schedules.length) {
      list.innerHTML = `<div class="empty">No mowing schedules. Tap + Add to create one.</div>`;
      return;
    }
    list.innerHTML = "";
    schedules.forEach(s => {
      const row = document.createElement("div");
      row.className = "row";

      const days = (s.dayOfWeek || []).map(d => DAYS[d] ?? `d${d}`).join(" ");
      const utcH = s.hour ?? 0;
      const utcM = s.minute ?? 0;
      // Robot stores UTC time; timeZone field is hours offset (e.g. 2 = UTC+2)
      const tzOffset = (s.timeZone ?? 0) * 60; // minutes
      const localMins = utcH * 60 + utcM + tzOffset;
      const lh = ((localMins / 60) % 24 + 24) % 24 | 0;
      const lm = ((localMins % 60) + 60) % 60;
      const timeStr = `${String(lh).padStart(2, "0")}:${String(lm).padStart(2, "0")}`;
      const zoneStr = (s.zones && s.zones.length) ? `Zones: ${s.zones.join(", ")}` : "All zones";

      row.innerHTML = `
        <div class="row-info">
          <div class="row-time">${timeStr}${s.isRepeated !== false ? "" : " (once)"}</div>
          <div class="row-days">${days || "Every day"}</div>
          <div class="row-zones">${zoneStr}</div>
        </div>
        <ha-switch class="toggle" ${s.isDisabled ? "" : "checked"}></ha-switch>
        <button class="del-btn" title="Delete">✕</button>`;

      const tog = row.querySelector(".toggle");
      tog.addEventListener("change", () => {
        this._callService("toggle_schedule", { id: s.id, disabled: !tog.checked })
          .catch(e => this._setStatus(String(e), true));
      });
      row.querySelector(".del-btn").addEventListener("click", () => {
        this._callService("delete_schedule", { id: s.id })
          .catch(e => this._setStatus(String(e), true));
      });
      list.appendChild(row);
    });
  }

  _toggleForm(show) {
    this._root.querySelector(".form").classList.toggle("hidden", !show);
    if (show) this._populateZoneChips();
  }

  _populateZoneChips() {
    const container = this._root.getElementById("zone-chips");
    if (!container || !this._hass) return;
    const zones = this._zones();
    if (!zones.length) {
      container.innerHTML = `<span style="font-size:11px;color:var(--secondary-text-color)">All zones (no zone entities found)</span>`;
      return;
    }
    container.innerHTML = "";
    // "All zones" chip
    const allBtn = document.createElement("button");
    allBtn.className = "zone-chip on";
    allBtn.textContent = "All zones";
    allBtn.dataset.zoneId = "__all__";
    allBtn.addEventListener("click", () => {
      // Toggle all-zones: if clicking all, deselect others; if clicking specific, deselect all
      const isAll = !allBtn.classList.contains("on");
      allBtn.classList.toggle("on", isAll);
      if (isAll) container.querySelectorAll(".zone-chip:not([data-zone-id='__all__'])").forEach(c => c.classList.remove("on"));
    });
    container.appendChild(allBtn);
    zones.forEach(z => {
      const btn = document.createElement("button");
      btn.className = "zone-chip";
      btn.textContent = z.name.replace(/^Zone\s+/i, "");
      btn.dataset.zoneId = z.id;
      btn.addEventListener("click", () => {
        btn.classList.toggle("on");
        // If any specific zone selected, deselect "All zones"
        const anyOn = [...container.querySelectorAll(".zone-chip:not([data-zone-id='__all__'])")].some(c => c.classList.contains("on"));
        allBtn.classList.toggle("on", !anyOn);
      });
      container.appendChild(btn);
    });
  }

  _saveSchedule() {
    const chips = this._root.querySelectorAll(".day-chip.on");
    const days = [...chips].map(c => parseInt(c.dataset.day));
    const timeVal = this._root.getElementById("time-input").value;
    const repeat = this._root.getElementById("repeat-chk").checked;

    if (!timeVal) { this._setStatus("Please set a time.", true); return; }

    // Collect selected zone IDs (empty = all zones)
    const allOn = this._root.querySelector(".zone-chip[data-zone-id='__all__']")?.classList.contains("on");
    const zoneChips = [...this._root.querySelectorAll(".zone-chip.on")].filter(c => c.dataset.zoneId !== "__all__");
    const zones = (allOn || !zoneChips.length) ? [] : zoneChips.map(c => c.dataset.zoneId);

    // Convert local time to UTC
    const [lh, lm] = timeVal.split(":").map(Number);
    const d = new Date();
    d.setHours(lh, lm, 0, 0);
    const utcH = d.getUTCHours();
    const utcM = d.getUTCMinutes();

    const payload = {
      hour: utcH,
      minute: utcM,
      day_of_week: days.length ? days : [0, 1, 2, 3, 4, 5, 6],
      repeated: repeat,
      disabled: false,
    };
    if (zones.length) payload.zones = zones;

    this._callService("add_schedule", payload).then(() => {
      this._toggleForm(false);
      this._setStatus("Schedule added.");
    }).catch(e => this._setStatus(String(e), true));
  }

  _clearAll() {
    if (!confirm("Delete all mowing schedules?")) return;
    this._callService("clear_schedules", {})
      .then(() => this._setStatus("All schedules cleared."))
      .catch(e => this._setStatus(String(e), true));
  }

  _callService(service, data) {
    return this._hass.callService("lymow", service, data, { entity_id: this._config.mower_entity });
  }

  _setStatus(msg, isErr = false) {
    const s = this._root.querySelector(".status");
    s.textContent = msg;
    s.classList.toggle("err", isErr);
    if (msg && !isErr) setTimeout(() => { if (s.textContent === msg) s.textContent = ""; }, 3000);
  }
}

if (!customElements.get("lymow-schedule-card")) {
  customElements.define("lymow-schedule-card", LymowScheduleCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.find(c => c.type === "lymow-schedule-card")) {
  window.customCards.push({ type: "lymow-schedule-card", name: "Lymow Schedules", description: "Manage mowing schedules." });
}
console.info("%c LYMOW-SCHEDULE-CARD ", "background:#7b1fa2;color:#fff;border-radius:3px");
