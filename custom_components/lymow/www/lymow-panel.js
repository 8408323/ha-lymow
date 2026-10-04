/*
 * Lymow panel — a full-page Home Assistant custom panel for a Lymow mower.
 *
 * Registered by the integration via panel_custom (sidebar "Lymow"). Unlike the
 * old auto-created card dashboard, this is a single page we fully own: a status
 * header plus a tabbed shell that discovers the mower's entities from `hass` and
 * composes the existing Lovelace cards (control / map / schedule / settings) as
 * sections, with a diagnostics readout built here.
 *
 * HA sets `hass`, `narrow`, `route`, `panel` on the element; only `hass` is used.
 */

const BASE = new URL(".", import.meta.url);
// The panel is served at a cache-busted URL (…/lymow-panel.js?v=<version>). Import
// the sibling cards with the SAME query so we reuse the module HA already loaded
// as a Lovelace resource, instead of registering a second copy (double-define).
const VERSION_QS = new URL(import.meta.url).search;

const CARD_FILE = {
  control: "lymow-control-card.js",
  map: "lymow-map-card.js",
  schedule: "lymow-schedule-card.js",
  settings: "lymow-settings-card.js",
};

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "map", label: "Map" },
  { id: "schedule", label: "Schedule" },
  { id: "settings", label: "Settings" },
  { id: "diagnostics", label: "Diagnostics" },
];

const STATE_LABEL = {
  mowing: "Mowing",
  paused: "Paused",
  docked: "Docked",
  returning: "Returning to dock",
  error: "Error",
  unavailable: "Unavailable",
};

// Entities to hide from the Diagnostics grid: the map sensor (a huge JSON blob).
const DIAG_EXCLUDE_SUFFIX = ["_map"];

async function loadCard(file) {
  await import(new URL(file, BASE).href + VERSION_QS);
}

class LymowPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = "overview";
    this._cards = {};
    this._built = false;
    this._mowerId = undefined;
  }

  set hass(hass) {
    this._hass = hass;
    if (this._built) this._refresh();
  }

  connectedCallback() {
    this._build();
  }

  // ── entity discovery ──────────────────────────────────────────────────────

  _lymowEntities() {
    // entity_id → registry entry ({platform, device_id, …}); modern HA always
    // provides hass.entities. Without it we can't tell which entities are Lymow's,
    // so fail closed (empty) rather than adopting some other integration's mower.
    const reg = this._hass && this._hass.entities;
    if (!reg) return [];
    return Object.keys(reg).filter((eid) => reg[eid] && reg[eid].platform === "lymow");
  }

  _discover() {
    if (!this._hass) return null;
    const ents = this._lymowEntities();
    // Prefer a mower with a live state; fall back to any registry entry (so the
    // header can still show "unavailable" rather than nothing) but skip picking a
    // stale registry-only entry over a loaded one.
    const mowers = ents.filter((e) => e.startsWith("lawn_mower."));
    const mower = mowers.find((e) => this._hass.states[e]) || mowers[0];
    if (!mower) return null;
    const reg = this._hass.entities;
    const deviceId = reg && reg[mower] ? reg[mower].device_id : undefined;
    const scope = deviceId && reg ? ents.filter((e) => reg[e].device_id === deviceId) : ents;
    const st = (e) => this._hass.states[e];
    const attr = (e, k) => st(e) && st(e).attributes && st(e).attributes[k] !== undefined;
    const findSensor = (predicate, suffix) =>
      scope.find((e) => e.startsWith("sensor.") && predicate(e)) ||
      scope.find((e) => e.startsWith("sensor.") && e.endsWith(suffix));
    return {
      mower,
      scope,
      // Identify by signature (attributes / device_class) first so renamed
      // entity_ids still resolve; fall back to the unique_id-derived suffix.
      // (Battery drives the header; the control card finds progress/error itself.)
      map: findSensor((e) => attr(e, "go_zones") || attr(e, "gps_origin"), "_map"),
      battery: findSensor((e) => st(e) && st(e).attributes.device_class === "battery", "_battery"),
    };
  }

  // ── shell ─────────────────────────────────────────────────────────────────

  _build() {
    if (this._built) return;
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); }
        .wrap { max-width: 1100px; margin: 0 auto; padding: 12px 16px 32px; box-sizing: border-box; }
        header.bar {
          display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
          padding: 14px 4px; border-bottom: 1px solid var(--divider-color, #e0e0e0);
        }
        .brand { font-size: 20px; font-weight: 600; margin-right: auto; }
        .chip { display: inline-flex; align-items: center; gap: 8px; font-size: 14px;
          background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 16px; padding: 6px 12px; }
        .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--secondary-text-color); }
        .dot.mowing { background: var(--success-color, #4caf50); }
        .dot.paused { background: var(--warning-color, #ff9800); }
        .dot.error { background: var(--error-color, #f44336); }
        .dot.docked, .dot.returning { background: var(--primary-color, #03a9f4); }
        .battery { font-size: 14px; color: var(--secondary-text-color); }
        button.refresh { border: 0; background: transparent; color: var(--primary-color, #03a9f4);
          font-size: 14px; cursor: pointer; padding: 6px 8px; border-radius: 8px; }
        button.refresh:hover { background: var(--secondary-background-color, #f0f0f0); }
        nav { display: flex; gap: 4px; flex-wrap: wrap; margin: 12px 0 16px; }
        nav button { border: 0; background: transparent; color: var(--secondary-text-color);
          font-size: 14px; font-weight: 500; cursor: pointer; padding: 10px 14px;
          border-radius: 10px; }
        nav button.active { color: var(--primary-color, #03a9f4); background: var(--secondary-background-color, #eaf6fd); }
        nav button:hover:not(.active) { background: var(--secondary-background-color, #f0f0f0); }
        .content { display: block; }
        .section { display: none; }
        .section.active { display: block; }
        .empty { padding: 40px 8px; color: var(--secondary-text-color); text-align: center; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
        .stat { background: var(--card-background-color, #fff); border: 1px solid var(--divider-color, #e0e0e0);
          border-radius: 12px; padding: 12px 14px; }
        .stat .k { font-size: 12px; color: var(--secondary-text-color); margin-bottom: 4px; }
        .stat .v { font-size: 16px; font-weight: 500; word-break: break-word; }
        .note { color: var(--secondary-text-color); font-size: 13px; padding: 8px 4px; }
        @media (max-width: 600px) { .brand { font-size: 18px; } .wrap { padding: 8px 10px 24px; } }
      </style>
      <div class="wrap">
        <header class="bar">
          <span class="brand">Lymow</span>
          <span class="chip"><span class="dot"></span><span class="state">—</span></span>
          <span class="battery"></span>
          <button class="refresh" title="Refresh map & schedule">Refresh</button>
        </header>
        <nav></nav>
        <div class="content"></div>
      </div>`;
    this._root = root;
    const nav = root.querySelector("nav");
    for (const tab of TABS) {
      const b = document.createElement("button");
      b.textContent = tab.label;
      b.dataset.tab = tab.id;
      b.addEventListener("click", () => this._selectTab(tab.id));
      nav.appendChild(b);
    }
    root.querySelector(".refresh").addEventListener("click", () => this._refreshData());
    this._built = true;
    this._selectTab(this._tab);
    this._refresh();
  }

  _selectTab(id) {
    this._tab = id;
    const root = this._root;
    root.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === id));
    this._render();
  }

  // ── rendering ─────────────────────────────────────────────────────────────

  _refresh() {
    if (!this._root || !this._hass) return;
    this._renderHeader();
    // _render is idempotent: it (re)builds the active section only when it isn't
    // already a mounted card, so a panel opened before discovery — or a section
    // that fell back to a placeholder — recovers once the entities appear.
    this._render();
    this._pushHass();
  }

  _hasCard(section) {
    return !!section.querySelector("lymow-control-card, lymow-map-card, lymow-schedule-card, lymow-settings-card");
  }

  _renderHeader() {
    const d = this._discover();
    const chipState = this._root.querySelector(".state");
    const dot = this._root.querySelector(".dot");
    const battery = this._root.querySelector(".battery");
    if (!d) {
      chipState.textContent = "No mower";
      dot.className = "dot";
      battery.textContent = "";
      return;
    }
    const st = this._hass.states[d.mower];
    const state = st ? st.state : "unavailable";
    chipState.textContent = STATE_LABEL[state] || state;
    dot.className = `dot ${state}`;
    const bat = d.battery && this._hass.states[d.battery];
    battery.textContent = bat && bat.state !== "unknown" && bat.state !== "unavailable"
      ? `${Math.round(Number(bat.state))}%`
      : "";
  }

  _render() {
    if (!this._root || !this._hass) return;
    const content = this._root.querySelector(".content");
    const d = this._discover();
    if (!d) {
      content.innerHTML = `<div class="empty">No Lymow mower found. Once the integration finishes setting up, its controls appear here.</div>`;
      this._mowerId = null;
      return;
    }
    // If the discovered mower changed (or we're rendering for the first time
    // after the empty state), rebuild from scratch so cards bind to the right
    // entity ids.
    if (d.mower !== this._mowerId) {
      content.innerHTML = "";
      this._cards = {};
      this._mowerId = d.mower;
    }
    let section = content.querySelector(`.section[data-tab="${this._tab}"]`);
    if (!section) {
      section = document.createElement("div");
      section.className = "section";
      section.dataset.tab = this._tab;
      content.appendChild(section);
    }
    // Diagnostics is data-only (refresh live); card sections build once and only
    // rebuild while they're still a placeholder (no card, not mid-mount).
    if (this._tab === "diagnostics") {
      this._fillDiagnostics(section, d);
    } else if (!this._hasCard(section) && section.dataset.mounting !== "1") {
      this._fillSection(section, this._tab, d);
    }
    content.querySelectorAll(".section").forEach((s) =>
      s.classList.toggle("active", s.dataset.tab === this._tab)
    );
  }

  _fillSection(section, tab, d) {
    if (tab === "overview") {
      this._mountCard(section, "control", { mower_entity: d.mower });
      return;
    }
    if (tab === "map") {
      if (!d.map) {
        section.innerHTML = `<div class="note">Map sensor is unavailable or disabled. Enable the Lymow map sensor to see the map here.</div>`;
        return;
      }
      this._mountCard(section, "map", { entity: d.map, mower_entity: d.mower, title: "Map" });
      return;
    }
    if (tab === "schedule") {
      this._mountCard(section, "schedule", { mower_entity: d.mower });
      return;
    }
    if (tab === "settings") {
      this._mountCard(section, "settings", { mower_entity: d.mower });
      return;
    }
    if (tab === "diagnostics") {
      this._fillDiagnostics(section, d);
    }
  }

  async _mountCard(section, kind, config) {
    if (section.dataset.mounting === "1") return; // a load is already in flight
    section.dataset.mounting = "1";
    section.innerHTML = `<div class="note">Loading…</div>`;
    try {
      await loadCard(CARD_FILE[kind]);
      const el = document.createElement(`lymow-${kind}-card`);
      el.setConfig(config);
      el.hass = this._hass;
      section.innerHTML = "";
      section.appendChild(el);
      this._cards[kind] = el;
    } catch (err) {
      section.innerHTML = `<div class="note">Could not load the ${kind} view.</div>`;
    } finally {
      delete section.dataset.mounting;
    }
  }

  _fillDiagnostics(section, d) {
    const items = d.scope
      .filter((e) => /^(sensor|binary_sensor|device_tracker)\./.test(e))
      .filter((e) => !DIAG_EXCLUDE_SUFFIX.some((s) => e.endsWith(s)))
      .map((e) => this._hass.states[e])
      .filter(Boolean)
      .sort((a, b) => this._name(a).localeCompare(this._name(b)));
    if (!items.length) {
      section.innerHTML = `<div class="note">No diagnostic entities available yet.</div>`;
      return;
    }
    const cell = (st) => {
      const unit = st.attributes && st.attributes.unit_of_measurement;
      const val = st.state === undefined || st.state === "" ? "—" : st.state;
      return `<div class="stat"><div class="k">${this._esc(this._name(st))}</div><div class="v">${this._esc(String(val))}${unit ? " " + this._esc(unit) : ""}</div></div>`;
    };
    section.innerHTML = `<div class="grid">${items.map(cell).join("")}</div>`;
  }

  _pushHass() {
    for (const el of Object.values(this._cards)) {
      if (el) el.hass = this._hass;
    }
  }

  _refreshData() {
    const d = this._discover();
    if (!d || !this._hass) return;
    // Fire the integration's own query services so the map/schedule refresh.
    for (const [domain, service] of [["lymow", "query_map"], ["lymow", "query_schedules"]]) {
      this._hass.callService(domain, service, { entity_id: d.mower }).catch(() => {});
    }
  }

  _name(st) {
    return (st.attributes && st.attributes.friendly_name) || st.entity_id;
  }

  _esc(s) {
    return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
}

// Guard against a double define: an HA reload / HACS update reloads this module
// at a new ?v= URL while the previous lymow-panel element is still registered.
if (!customElements.get("lymow-panel")) {
  customElements.define("lymow-panel", LymowPanel);
}
