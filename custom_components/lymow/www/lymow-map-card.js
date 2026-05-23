/**
 * lymow-map-card  –  Lovelace card for the Lymow robotic mower integration
 *
 * Features:
 *   • Renders go-zones, no-go zones, channels, charging station, robot pose, RTK base
 *   • Mouse wheel / pinch to zoom; drag anywhere on map to pan
 *   • Expand button: fills the full browser viewport
 *   • Edit mode: tap a go-zone → drag vertex handles to reshape; tap edge
 *     midpoint (+) to insert a vertex; tap vertex ✕ to delete; Save / Cancel
 *   • North arrow + scale bar fixed to viewport corners
 *   • Legend symbols match actual map markers
 *
 * YAML config example:
 *   type: custom:lymow-map-card
 *   entity: sensor.lymow_THING_map      # required – the map sensor
 *   mower_entity: lawn_mower.lymow_THING  # required for mowing + editing
 *   title: My lawn                       # optional card title override
 */

const _ZOOM_MIN = 0.5;
const _ZOOM_MAX = 20;

class LymowMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._selectedZones = new Set();
    this._hass = null;
    this._config = null;
    this._expanded = false;

    // Edit state
    this._editing = false;
    this._editHash = null;
    this._workPoly = null;
    this._dragIdx = null;
    this._polyOverrides = {};

    // Pan/zoom state (in SVG user units)
    this._vx = 0; this._vy = 0; this._vw = 100; this._vh = 100;
    this._mapReady = false;

    // Pan gesture
    this._panning = false;
    this._panStart = null;
    this._panMoved = false;

    // Pinch zoom
    this._pinchStart = null;

    this._bounds = null;
    this._scale = 1;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("lymow-map-card: 'entity' is required");
    this._config = config;
  }

  static getStubConfig() {
    return { entity: "sensor.lymow_map", mower_entity: "lawn_mower.lymow" };
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  // ---------------------------------------------------------------------------
  // Data helpers
  // ---------------------------------------------------------------------------

  _getMapData() {
    const state = this._hass && this._hass.states[this._config.entity];
    if (!state) return null;
    const a = state.attributes;
    const goZones = (a.go_zones || []).map((z) =>
      this._polyOverrides[z.hashId] ? { ...z, polygon: this._polyOverrides[z.hashId] } : z
    );
    return {
      goZones,
      nogoZones: a.nogo_zones || [],
      channels: a.channels || [],
      gpsOrigin: a.gps_origin || null,
      chargingStation: a.charging_station || null,
      poseEastM: a.poseEastM,
      poseNorthM: a.poseNorthM,
      poseThetaRad: a.poseThetaRad,
      rtkEastM: a.rtkEastM,
      rtkNorthM: a.rtkNorthM,
    };
  }

  _computeBounds(mapData) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const acc = (x, y) => {
      if (!isFinite(x) || !isFinite(y)) return;
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    };
    const { goZones, nogoZones, channels, chargingStation, poseEastM, poseNorthM, rtkEastM, rtkNorthM } = mapData;
    for (const z of [...goZones, ...nogoZones]) for (const p of z.polygon || []) acc(p.x, p.y);
    for (const ch of channels) for (const p of ch.polygon || []) acc(p.x, p.y);
    if (chargingStation) acc(chargingStation.x, chargingStation.y);
    if (poseEastM !== undefined && poseNorthM !== undefined) acc(poseEastM, poseNorthM);
    if (rtkEastM !== undefined && rtkNorthM !== undefined) acc(rtkEastM, rtkNorthM);
    if (this._workPoly) for (const p of this._workPoly) acc(p.x, p.y);
    if (!isFinite(minX)) return null;
    const PAD = Math.max(1.5, (maxX - minX + maxY - minY) * 0.05);
    return { minX: minX - PAD, maxX: maxX + PAD, minY: minY - PAD, maxY: maxY + PAD };
  }

  // ---------------------------------------------------------------------------
  // Coordinate transforms
  // ---------------------------------------------------------------------------

  _sx(x) { return ((x - this._bounds.minX) * this._scale).toFixed(3); }
  _sy(y) { return ((this._bounds.maxY - y) * this._scale).toFixed(3); }

  _toEnu(svgX, svgY) {
    return { x: svgX / this._scale + this._bounds.minX, y: this._bounds.maxY - svgY / this._scale };
  }

  _clientToEnu(evt) {
    const svg = this.shadowRoot.querySelector("svg");
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX; pt.y = evt.clientY;
    const u = pt.matrixTransform(svg.getScreenCTM().inverse());
    return this._toEnu(u.x, u.y);
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  _render() {
    if (!this._hass || !this._config) return;
    const mapData = this._getMapData();

    if (!mapData) {
      this.shadowRoot.innerHTML = this._wrapMsg(`Map entity not found: <code>${this._config.entity}</code>`);
      return;
    }

    const { goZones, nogoZones, channels, chargingStation, poseEastM, poseNorthM, poseThetaRad, rtkEastM, rtkNorthM } = mapData;

    if ([...goZones, ...nogoZones].length === 0 && !chargingStation) {
      this.shadowRoot.innerHTML = this._wrapMsg(`No map data yet. Call <em>lymow.query_map</em> or wait for the robot to connect.`);
      return;
    }

    const newBounds = this._computeBounds(mapData);
    if (!newBounds) { this.shadowRoot.innerHTML = this._wrapMsg("Empty map."); return; }

    if (!this._mapReady) {
      this._bounds = newBounds;
      const W = newBounds.maxX - newBounds.minX;
      const H = newBounds.maxY - newBounds.minY;
      this._scale = 100 / W;
      this._vw = 100; this._vh = H * this._scale;
      this._vx = 0; this._vy = 0;
      this._mapReady = true;
    } else if (this._editing) {
      this._bounds = newBounds;
      this._scale = 100 / (newBounds.maxX - newBounds.minX);
    }

    const { _bounds: b, _scale: sc } = this;
    const sx = (x) => this._sx(x);
    const sy = (y) => this._sy(y);
    const TOTAL_W = (b.maxX - b.minX) * sc;
    const TOTAL_H = (b.maxY - b.minY) * sc;
    const fontSz = Math.max(1.2, Math.min(3, TOTAL_W / 25)).toFixed(2);
    const nodeR = Math.max(0.8, TOTAL_W / 70).toFixed(2);

    // ── Channels ─────────────────────────────────────────────────────────────
    const channelPaths = channels.map((ch) => {
      const pts = (ch.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const isDocking = ch.isDockingChannel;
      const color = isDocking ? "#1565c0" : "#6a1b9a";
      const dash = isDocking ? "1,0.6" : "0.8,0.4";
      return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="0.4" stroke-dasharray="${dash}" opacity="0.7"/>`;
    }).join("\n");

    // ── Go-zones ──────────────────────────────────────────────────────────────
    const goPaths = goZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const selected = this._selectedZones.has(z.hashId);
      const beingEdited = this._editing && this._editHash === z.hashId;
      const enabled = z.isEnabled !== false;
      const fill = beingEdited ? "#fff3e0" : selected ? "#2e7d32" : enabled ? "#a8d8a8" : "#c8e6c9";
      const stroke = beingEdited ? "#ef6c00" : selected ? "#81c784" : "#388e3c";
      return `<polygon data-hash="${z.hashId}" data-type="go" points="${pts}"
        fill="${fill}" stroke="${stroke}" stroke-width="0.4" opacity="${enabled ? 1 : 0.55}"
        style="cursor:pointer"/>`;
    }).join("\n");

    // For each go-zone: clip label to polygon so it never renders outside the zone.
    // Uses SVG clipPath so even concave polygons clip correctly.
    const goLabelDefs = goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      return `<clipPath id="lbl-clip-${z.hashId}"><polygon points="${pts}"/></clipPath>`;
    }).join("\n");

    const goLabels = goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const {x: cx, y: cy} = this._polyLabelPoint(z.polygon);
      const label = z.area != null ? `${z.area} m²` : z.hashId.slice(0, 6);
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle"
        font-size="${fontSz}" fill="#1b5e20" pointer-events="none" font-weight="bold"
        clip-path="url(#lbl-clip-${z.hashId})">${label}</text>`;
    }).join("\n");

    // ── No-go zones (on top of go-zones) ─────────────────────────────────────
    const nogoPaths = nogoZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      return `<polygon points="${pts}" fill="#ff5252" fill-opacity="0.35" stroke="#c62828" stroke-width="0.6" stroke-dasharray="1,0.5"/>`;
    }).join("\n");

    const nogoLabels = nogoZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const cx = z.polygon.reduce((s, p) => s + p.x, 0) / z.polygon.length;
      const cy = z.polygon.reduce((s, p) => s + p.y, 0) / z.polygon.length;
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle"
        font-size="${(parseFloat(fontSz) * 0.9).toFixed(2)}" fill="#c62828" pointer-events="none">⛔</text>`;
    }).join("\n");

    // ── Edit handles ──────────────────────────────────────────────────────────
    let editOverlay = "";
    if (this._editing && this._workPoly && this._workPoly.length >= 3) {
      const poly = this._workPoly;
      const workPts = poly.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const workOutline = `<polygon points="${workPts}" fill="#ef6c0022" stroke="#ef6c00" stroke-width="0.5" stroke-dasharray="1.5,0.5" pointer-events="none"/>`;
      const midpoints = poly.map((p, i) => {
        const q = poly[(i + 1) % poly.length];
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        return `<g class="midpoint" data-edge="${i}" style="cursor:copy">
          <circle cx="${sx(mx)}" cy="${sy(my)}" r="${(parseFloat(nodeR) * 0.75).toFixed(2)}" fill="white" stroke="#ef6c00" stroke-width="0.3"/>
          <text x="${sx(mx)}" y="${sy(my)}" text-anchor="middle" dominant-baseline="central"
            font-size="${(parseFloat(nodeR) * 0.9).toFixed(2)}" fill="#ef6c00" pointer-events="none">+</text>
        </g>`;
      }).join("\n");
      const verts = poly.map((p, i) => {
        const delBadge = poly.length > 3
          ? `<text class="delvert" data-idx="${i}"
              x="${(parseFloat(sx(p.x)) + parseFloat(nodeR) * 1.3).toFixed(3)}"
              y="${(parseFloat(sy(p.y)) - parseFloat(nodeR) * 1.3).toFixed(3)}"
              font-size="${(parseFloat(nodeR) * 1.1).toFixed(2)}" fill="#c62828" style="cursor:pointer">✕</text>`
          : "";
        return `<circle class="vertex" data-idx="${i}" cx="${sx(p.x)}" cy="${sy(p.y)}" r="${nodeR}"
            fill="#ef6c00" stroke="white" stroke-width="0.35" style="cursor:grab"/>${delBadge}`;
      }).join("\n");
      editOverlay = workOutline + midpoints + verts;
    }

    // ── Charging station ──────────────────────────────────────────────────────
    let csHtml = "";
    if (chargingStation) {
      const cx = sx(chargingStation.x), cy = sy(chargingStation.y);
      const r = Math.max(1.2, TOTAL_W / 55).toFixed(2);
      csHtml = `
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="#1565c0" opacity="0.9"/>
        <circle cx="${cx}" cy="${cy}" r="${(parseFloat(r) * 0.55).toFixed(2)}" fill="white"/>
        <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="middle"
          font-size="${(parseFloat(r) * 0.9).toFixed(2)}" fill="#1565c0" pointer-events="none" font-weight="bold">⚡</text>`;
    }

    // ── Robot position ────────────────────────────────────────────────────────
    let robotHtml = "";
    if (poseEastM !== undefined && poseNorthM !== undefined) {
      const rx = sx(poseEastM), ry = sy(poseNorthM);
      const r = Math.max(0.9, TOTAL_W / 70).toFixed(2);
      const theta = poseThetaRad || 0;
      const arrowLen = parseFloat(r) * 2.5;
      const ax = (parseFloat(rx) + Math.cos(theta) * arrowLen).toFixed(3);
      const ay = (parseFloat(ry) - Math.sin(theta) * arrowLen).toFixed(3);
      robotHtml = `
        <circle cx="${rx}" cy="${ry}" r="${r}" fill="#e65100" stroke="white" stroke-width="0.3"/>
        <line x1="${rx}" y1="${ry}" x2="${ax}" y2="${ay}" stroke="#e65100" stroke-width="0.7" stroke-linecap="round"/>`;
    }

    // ── RTK base station ─────────────────────────────────────────────────────
    let rtkHtml = "";
    if (rtkEastM !== undefined && rtkNorthM !== undefined) {
      const rx = sx(rtkEastM), ry = sy(rtkNorthM);
      const r = Math.max(1.0, TOTAL_W / 65).toFixed(2);
      // Triangle marker (surveying convention for base station)
      const rF = parseFloat(r);
      const pts = [
        `${parseFloat(rx).toFixed(3)},${(parseFloat(ry) - rF * 1.5).toFixed(3)}`,
        `${(parseFloat(rx) - rF * 1.2).toFixed(3)},${(parseFloat(ry) + rF * 0.8).toFixed(3)}`,
        `${(parseFloat(rx) + rF * 1.2).toFixed(3)},${(parseFloat(ry) + rF * 0.8).toFixed(3)}`,
      ].join(" ");
      rtkHtml = `
        <polygon points="${pts}" fill="#7b1fa2" stroke="white" stroke-width="0.3" opacity="0.9"/>
        <text x="${rx}" y="${(parseFloat(ry) + rF * 2.5).toFixed(3)}" text-anchor="middle"
          font-size="${(rF * 0.85).toFixed(2)}" fill="#7b1fa2" pointer-events="none">RTK</text>`;
    }

    // ── North arrow (viewport-fixed, top-right) ───────────────────────────────
    const NX = (this._vx + this._vw - 5).toFixed(2);
    const NY = (this._vy + 5).toFixed(2);
    const northHtml = `
      <g data-overlay="north" transform="translate(${NX}, ${NY})" pointer-events="none">
        <circle r="3.5" fill="white" opacity="0.85"/>
        <line x1="0" y1="2.5" x2="0" y2="-2.5" stroke="#333" stroke-width="0.5"/>
        <polygon points="0,-2.5 -0.9,-0.3 0.9,-0.3" fill="#c0392b"/>
        <text x="0" y="5.5" text-anchor="middle" font-size="2" fill="#333" font-weight="bold">N</text>
      </g>`;

    // ── Scale bar (viewport-fixed, bottom-left) ───────────────────────────────
    const viewMetres = this._vw / sc;
    const niceMetres = this._niceNumber(viewMetres * 0.18);
    const barW = (niceMetres * sc).toFixed(3);
    const bx = (this._vx + 3).toFixed(2);
    const by = (this._vy + this._vh - 3).toFixed(2);
    const byTick = (this._vy + this._vh - 2).toFixed(2);
    const byLabel = (this._vy + this._vh - 4.5).toFixed(2);
    const bx2 = (parseFloat(bx) + parseFloat(barW)).toFixed(2);
    const bxMid = (parseFloat(bx) + parseFloat(barW) / 2).toFixed(2);
    const scaleBarHtml = `
      <g pointer-events="none">
        <rect data-overlay="scalebar" x="${bx}" y="${by}" width="${barW}" height="0.9" fill="#555" opacity="0.85"/>
        <line data-overlay="scalebar" x1="${bx}" y1="${by}" x2="${bx}" y2="${byTick}" stroke="#555" stroke-width="0.4" opacity="0.85"/>
        <line data-overlay="scalebar" x1="${bx2}" y1="${by}" x2="${bx2}" y2="${byTick}" stroke="#555" stroke-width="0.4" opacity="0.85"/>
        <text data-overlay="scalebar" x="${bxMid}" y="${byLabel}" text-anchor="middle" font-size="1.8" fill="#555">${niceMetres} m</text>
      </g>`;

    // ── Toolbar ───────────────────────────────────────────────────────────────
    const host = "this.getRootNode().host";
    let toolbar;
    if (this._editing) {
      const msg = this._editHash
        ? `Editing zone — drag handles · tap + to insert · ✕ to delete`
        : `Tap a go-zone to start editing its boundary.`;
      toolbar = `
        <div class="edit-bar">${msg}</div>
        <div class="btn-row">
          ${this._editHash ? `<button class="btn save" onclick="${host}._saveEdit()">💾 Save</button>` : ""}
          <button class="btn cancel" onclick="${host}._cancelEdit()">✕ Cancel</button>
        </div>`;
    } else {
      const hasSel = this._selectedZones.size > 0;
      const canMow = hasSel && !!this._config.mower_entity;
      const mowBtn = hasSel
        ? `<button class="btn mow" ${canMow ? "" : "disabled"} onclick="${host}._mowSelected()">🌿 Mow selected (${this._selectedZones.size})</button>`
        : "";
      const editBtn = this._config.mower_entity
        ? `<button class="btn edit" onclick="${host}._enterEdit()">✏️ Edit zones</button>` : "";
      const expandBtn = `<button class="btn expand" onclick="${host}._toggleExpand()" title="${this._expanded ? "Collapse" : "Expand map"}">${this._expanded ? "⊠" : "⊞"}</button>`;
      const resetBtn = `<button class="btn reset" onclick="${host}._resetView()" title="Reset zoom">⊡</button>`;
      toolbar = `<div class="btn-row">${mowBtn}${editBtn}${expandBtn}${resetBtn}</div>`;
    }

    // ── Legend with matching SVG symbols ─────────────────────────────────────
    const _li = (svgInner, vb, label) =>
      `<div class="legend-item"><span class="lsym"><svg viewBox="${vb}" xmlns="http://www.w3.org/2000/svg">${svgInner}</svg></span>${label}</div>`;
    const legendItems = [
      _li(`<rect x="1" y="1" width="14" height="10" fill="#a8d8a8" stroke="#388e3c" stroke-width="1.5" rx="1"/>`, "0 0 16 12", "Go zone"),
      nogoZones.length ? _li(`<rect x="1" y="1" width="14" height="10" fill="#ff5252" fill-opacity="0.35" stroke="#c62828" stroke-width="1.5" rx="1" stroke-dasharray="3,2"/>`, "0 0 16 12", "No-go") : "",
      chargingStation ? _li(`<circle cx="8" cy="7" r="6" fill="#1565c0" opacity="0.9"/><circle cx="8" cy="7" r="3.5" fill="white"/><text x="8" y="8.5" text-anchor="middle" dominant-baseline="middle" font-size="5.5" fill="#1565c0" font-weight="bold">⚡</text>`, "0 0 16 14", "Station") : "",
      poseEastM !== undefined ? _li(`<circle cx="7" cy="8" r="5" fill="#e65100" stroke="white" stroke-width="1"/><line x1="7" y1="8" x2="16" y2="3" stroke="#e65100" stroke-width="1.5" stroke-linecap="round"/>`, "0 0 18 14", "Robot") : "",
      rtkEastM !== undefined ? _li(`<polygon points="8,1 2,13 14,13" fill="#7b1fa2" stroke="white" stroke-width="1"/>`, "0 0 16 14", "RTK") : "",
      channels.some(c => c.isDockingChannel) ? _li(`<line x1="1" y1="6" x2="19" y2="6" stroke="#1565c0" stroke-width="2" stroke-dasharray="4,2"/>`, "0 0 20 12", "Docking") : "",
      channels.some(c => !c.isDockingChannel) ? _li(`<line x1="1" y1="6" x2="19" y2="6" stroke="#6a1b9a" stroke-width="2" stroke-dasharray="4,2"/>`, "0 0 20 12", "Channel") : "",
    ].filter(Boolean).join("");

    const title = this._config.title ?? "Lymow Map";

    // Aspect ratio for the map area
    const mapAspect = (TOTAL_W / TOTAL_H).toFixed(4);

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        :host(.expanded) { position: fixed; inset: 0; z-index: 9999; background: var(--card-background-color, #1c1c1c); overflow: hidden; display: flex; flex-direction: column; }
        ha-card { padding: 12px 12px 8px; box-sizing: border-box; height: 100%; display: flex; flex-direction: column; }
        :host(.expanded) ha-card { border-radius: 0; flex: 1 1 0; min-height: 0; }
        .card-header { font-size: 1.05em; font-weight: 500; margin-bottom: 8px; color: var(--primary-text-color); flex-shrink: 0; }
        .map-wrap { width: 100%; flex: 1 1 0; position: relative; min-height: 0; }
        :host(:not(.expanded)) .map-wrap { aspect-ratio: ${mapAspect}; flex: none; }
        svg { width: 100%; height: 100%; border-radius: 6px; background: #e8f5e9; display: block; touch-action: none; user-select: none; cursor: grab; }
        svg.panning { cursor: grabbing; }
        .btn-row { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; flex-shrink: 0; }
        .btn { flex: 1; min-width: 80px; padding: 9px 6px; border: none; border-radius: 6px;
               font-size: 0.84em; font-weight: 600; cursor: pointer; color: white; }
        .btn.mow, .btn.edit { background: var(--primary-color, #03a9f4); }
        .btn.save { background: #2e7d32; }
        .btn.cancel { background: #757575; flex: 0; }
        .btn.reset, .btn.expand { background: #455a64; flex: 0; min-width: 36px; }
        .btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .btn:not(:disabled):hover { filter: brightness(1.1); }
        .edit-bar { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 6px; flex-shrink: 0; }
        .msg { padding: 14px; color: var(--secondary-text-color); font-size: 0.9em; line-height: 1.5; }
        code { background: var(--code-editor-background-color,#f0f0f0); padding: 1px 4px; border-radius: 3px; }
        .legend { display: flex; flex-wrap: wrap; gap: 4px 10px; margin-top: 6px; font-size: 0.75em;
                  color: var(--secondary-text-color); align-items: center; flex-shrink: 0; }
        .legend-item { display: flex; align-items: center; gap: 3px; white-space: nowrap; }
        .lsym { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 14px; flex-shrink: 0; }
        .lsym svg { width: 100%; height: 100%; display: block; }
      </style>
      <ha-card>
        <div class="card-header">${title}</div>
        <div class="map-wrap">
          <svg viewBox="${this._vx.toFixed(3)} ${this._vy.toFixed(3)} ${this._vw.toFixed(3)} ${this._vh.toFixed(3)}"
               xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
            <defs>${goLabelDefs}</defs>
            ${channelPaths}
            ${goPaths}
            ${goLabels}
            ${nogoPaths}
            ${nogoLabels}
            ${csHtml}
            ${robotHtml}
            ${rtkHtml}
            ${editOverlay}
            ${northHtml}
            ${scaleBarHtml}
          </svg>
        </div>
        ${toolbar}
        <div class="legend">${legendItems}</div>
      </ha-card>`;

    this._wireEvents();
  }

  _niceNumber(x) {
    if (x <= 0) return 5;
    const magnitude = Math.pow(10, Math.floor(Math.log10(x)));
    for (const n of [1, 2, 5, 10]) if (n * magnitude >= x) return n * magnitude;
    return 10 * magnitude;
  }

  // Approximate pole of inaccessibility: grid-sample the bounding box, keep
  // only interior points, return the one with largest min-distance to any edge.
  // Falls back to centroid if polygon is degenerate.
  _polyLabelPoint(poly) {
    if (!poly || poly.length < 3) return {x: 0, y: 0};
    const xs = poly.map(p => p.x), ys = poly.map(p => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;

    // point-in-polygon ray-cast
    const pip = (px, py) => {
      let inside = false;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
        if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi) inside = !inside;
      }
      return inside;
    };

    // min squared distance from point to any polygon edge
    const edgeDist = (px, py) => {
      let d = Infinity;
      for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        const ax = poly[j].x, ay = poly[j].y, bx = poly[i].x, by = poly[i].y;
        const dx = bx - ax, dy = by - ay;
        const len2 = dx * dx + dy * dy;
        if (len2 === 0) continue; // skip degenerate (zero-length) edges
        const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
        const ex = ax + t * dx - px, ey = ay + t * dy - py;
        d = Math.min(d, ex * ex + ey * ey);
      }
      return d;
    };

    // Also include vertex-average centroid and bbox centre as candidates
    const vcx = poly.reduce((s, p) => s + p.x, 0) / poly.length;
    const vcy = poly.reduce((s, p) => s + p.y, 0) / poly.length;
    const candidates = [{x: cx, y: cy}, {x: vcx, y: vcy}];
    const steps = 16;
    const sw = (maxX - minX) / steps, sh = (maxY - minY) / steps;
    for (let r = 0; r <= steps; r++)
      for (let c = 0; c <= steps; c++)
        candidates.push({x: minX + c * sw, y: minY + r * sh});

    let best = null, bestD = -1;
    for (const {x: px, y: py} of candidates) {
      if (pip(px, py)) {
        const d = edgeDist(px, py);
        if (d > bestD) { bestD = d; best = {x: px, y: py}; }
      }
    }
    return best || {x: vcx, y: vcy};
  }

  // ---------------------------------------------------------------------------
  // Event wiring
  // ---------------------------------------------------------------------------

  _wireEvents() {
    const svg = this.shadowRoot.querySelector("svg");
    if (!svg) return;

    svg.addEventListener("wheel", (e) => this._onWheel(e), { passive: false });
    svg.addEventListener("touchstart", (e) => this._onTouchStart(e), { passive: false });
    svg.addEventListener("touchmove", (e) => this._onTouchMove(e), { passive: false });
    svg.addEventListener("touchend", (e) => this._onTouchEnd(e));

    if (this._editing) {
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("click", () => { if (!this._panMoved) this._chooseEditZone(el.dataset.hash); });
      });
      this.shadowRoot.querySelectorAll(".midpoint").forEach((el) => {
        el.addEventListener("click", (e) => { e.stopPropagation(); this._insertVertex(+el.dataset.edge); });
      });
      this.shadowRoot.querySelectorAll(".delvert").forEach((el) => {
        el.addEventListener("click", (e) => { e.stopPropagation(); this._deleteVertex(+el.dataset.idx); });
      });
      this.shadowRoot.querySelectorAll(".vertex").forEach((el) => {
        el.addEventListener("pointerdown", (e) => { e.stopPropagation(); this._panMoved = false; this._startDrag(e, +el.dataset.idx); });
      });
      if (this._editHash) {
        svg.addEventListener("pointermove", (e) => this._onDrag(e));
        svg.addEventListener("pointerup", () => this._endDrag());
        svg.addEventListener("pointercancel", () => this._endDrag());
      }
    } else {
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("click", (e) => { if (!this._panMoved) { e.stopPropagation(); this._toggleZone(el.dataset.hash); } });
      });
    }

    // Pan: any pointer drag on SVG (vertex drags set _dragIdx which suppresses pan)
    svg.addEventListener("pointerdown", (e) => {
      if (this._dragIdx != null) return;
      this._panning = true;
      this._panMoved = false;
      this._panStart = { x: e.clientX, y: e.clientY, vx: this._vx, vy: this._vy };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add("panning");
    });
    svg.addEventListener("pointermove", (e) => this._onPan(e));
    svg.addEventListener("pointerup", () => { this._panning = false; svg.classList.remove("panning"); });
    svg.addEventListener("pointercancel", () => { this._panning = false; svg.classList.remove("panning"); });
  }

  // ---------------------------------------------------------------------------
  // Zoom
  // ---------------------------------------------------------------------------

  _onWheel(evt) {
    evt.preventDefault();
    const svg = this.shadowRoot.querySelector("svg");
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = this._vx + (evt.clientX - rect.left) / rect.width * this._vw;
    const py = this._vy + (evt.clientY - rect.top) / rect.height * this._vh;
    this._applyZoom(evt.deltaY < 0 ? 0.85 : 1 / 0.85, px, py);
  }

  _onTouchStart(e) {
    if (e.touches.length === 2) {
      e.preventDefault();
      this._pinchStart = {
        dist: this._touchDist(e), vx: this._vx, vy: this._vy, vw: this._vw, vh: this._vh,
        cx: (e.touches[0].clientX + e.touches[1].clientX) / 2,
        cy: (e.touches[0].clientY + e.touches[1].clientY) / 2,
      };
    }
  }

  _onTouchMove(e) {
    if (e.touches.length === 2 && this._pinchStart) {
      e.preventDefault();
      const svg = this.shadowRoot.querySelector("svg");
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const { cx, cy, vx, vy, vw, vh } = this._pinchStart;
      const px = vx + (cx - rect.left) / rect.width * vw;
      const py = vy + (cy - rect.top) / rect.height * vh;
      this._setViewBox(vw * this._pinchStart.dist / this._touchDist(e), vh * this._pinchStart.dist / this._touchDist(e), px, py);
      this._updateViewBox();
      this._updateOverlays();
    }
  }

  _onTouchEnd(e) { if (e.touches.length < 2) this._pinchStart = null; }

  _touchDist(e) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  _applyZoom(factor, pivotX, pivotY) {
    this._setViewBox(this._vw * factor, this._vh * factor, pivotX, pivotY);
    this._updateViewBox();
    this._updateOverlays();
  }

  _setViewBox(newW, newH, pivotX, pivotY) {
    const TOTAL_W = (this._bounds.maxX - this._bounds.minX) * this._scale;
    const TOTAL_H = (this._bounds.maxY - this._bounds.minY) * this._scale;
    newW = Math.max(TOTAL_W / _ZOOM_MAX, Math.min(TOTAL_W / _ZOOM_MIN, newW));
    newH = newW * (TOTAL_H / TOTAL_W);
    const ratioX = (pivotX - this._vx) / this._vw;
    const ratioY = (pivotY - this._vy) / this._vh;
    this._vx = pivotX - ratioX * newW;
    this._vy = pivotY - ratioY * newH;
    this._vw = newW; this._vh = newH;
    this._vx = Math.max(-TOTAL_W * 0.3, Math.min(TOTAL_W * 1.3 - newW, this._vx));
    this._vy = Math.max(-TOTAL_H * 0.3, Math.min(TOTAL_H * 1.3 - newH, this._vy));
  }

  _updateViewBox() {
    const svg = this.shadowRoot.querySelector("svg");
    if (svg) svg.setAttribute("viewBox", `${this._vx.toFixed(3)} ${this._vy.toFixed(3)} ${this._vw.toFixed(3)} ${this._vh.toFixed(3)}`);
  }

  _updateOverlays() {
    const root = this.shadowRoot;
    const sc = this._scale;

    const northG = root.querySelector("g[data-overlay='north']");
    if (northG) northG.setAttribute("transform", `translate(${(this._vx + this._vw - 5).toFixed(2)}, ${(this._vy + 5).toFixed(2)})`);

    const viewMetres = this._vw / sc;
    const niceMetres = this._niceNumber(viewMetres * 0.18);
    const barW = (niceMetres * sc).toFixed(3);
    const bx = (this._vx + 3).toFixed(2);
    const by = (this._vy + this._vh - 3).toFixed(2);
    const byTick = (this._vy + this._vh - 2).toFixed(2);
    const byLabel = (this._vy + this._vh - 4.5).toFixed(2);
    const bx2 = (parseFloat(bx) + parseFloat(barW)).toFixed(2);
    const bxMid = (parseFloat(bx) + parseFloat(barW) / 2).toFixed(2);

    const barRect = root.querySelector("rect[data-overlay='scalebar']");
    if (barRect) { barRect.setAttribute("x", bx); barRect.setAttribute("y", by); barRect.setAttribute("width", barW); }
    const barLines = root.querySelectorAll("line[data-overlay='scalebar']");
    if (barLines[0]) { barLines[0].setAttribute("x1", bx); barLines[0].setAttribute("y1", by); barLines[0].setAttribute("x2", bx); barLines[0].setAttribute("y2", byTick); }
    if (barLines[1]) { barLines[1].setAttribute("x1", bx2); barLines[1].setAttribute("y1", by); barLines[1].setAttribute("x2", bx2); barLines[1].setAttribute("y2", byTick); }
    const barText = root.querySelector("text[data-overlay='scalebar']");
    if (barText) { barText.setAttribute("x", bxMid); barText.setAttribute("y", byLabel); barText.textContent = `${niceMetres} m`; }
  }

  _resetView() { this._mapReady = false; this._render(); }

  // ---------------------------------------------------------------------------
  // Expand / collapse
  // ---------------------------------------------------------------------------

  _toggleExpand() {
    this._expanded = !this._expanded;
    if (this._expanded) {
      this.classList.add("expanded");
      // Prevent body scroll when expanded
      document.documentElement.style.overflow = "hidden";
    } else {
      this.classList.remove("expanded");
      document.documentElement.style.overflow = "";
    }
    // Reset view so map fills new container size
    this._mapReady = false;
    this._render();
  }

  // ---------------------------------------------------------------------------
  // Pan
  // ---------------------------------------------------------------------------

  _onPan(e) {
    if (!this._panning || !this._panStart || this._dragIdx != null) return;
    const dx = e.clientX - this._panStart.x;
    const dy = e.clientY - this._panStart.y;
    if (!this._panMoved && Math.sqrt(dx * dx + dy * dy) < 3) return;
    this._panMoved = true;
    const svg = this.shadowRoot.querySelector("svg");
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    this._vx = this._panStart.vx - dx / rect.width * this._vw;
    this._vy = this._panStart.vy - dy / rect.height * this._vh;
    this._updateViewBox();
    this._updateOverlays();
  }

  // ---------------------------------------------------------------------------
  // Zone selection / mow
  // ---------------------------------------------------------------------------

  _toggleZone(hashId) {
    if (this._selectedZones.has(hashId)) this._selectedZones.delete(hashId);
    else this._selectedZones.add(hashId);
    this._render();
  }

  async _mowSelected() {
    if (!this._hass || this._selectedZones.size === 0 || !this._config.mower_entity) return;
    await this._hass.callService("lymow", "start_zone", {
      entity_id: this._config.mower_entity,
      zone_hash_ids: [...this._selectedZones],
    });
    this._selectedZones.clear();
    this._render();
  }

  // ---------------------------------------------------------------------------
  // Edit mode
  // ---------------------------------------------------------------------------

  _enterEdit() {
    this._editing = true; this._editHash = null; this._workPoly = null;
    this._selectedZones.clear(); this._render();
  }

  _cancelEdit() {
    this._editing = false; this._editHash = null; this._workPoly = null;
    this._dragIdx = null; this._render();
  }

  _chooseEditZone(hashId) {
    if (this._editHash === hashId) return;
    const zone = (this._getMapData()?.goZones || []).find((z) => z.hashId === hashId);
    if (!zone || !zone.polygon) return;
    this._editHash = hashId;
    this._workPoly = this._decimatePoly(zone.polygon);
    this._render();
  }

  _decimatePoly(pts) {
    const MAX_VERTS = 32;
    if (pts.length <= MAX_VERTS) return pts.map((p) => ({ x: p.x, y: p.y }));
    let perim = 0;
    for (let i = 0; i < pts.length; i++) {
      const q = pts[(i + 1) % pts.length];
      perim += Math.sqrt((q.x - pts[i].x) ** 2 + (q.y - pts[i].y) ** 2);
    }
    const minDist = perim / MAX_VERTS;
    const out = [{ x: pts[0].x, y: pts[0].y }];
    for (let i = 1; i < pts.length; i++) {
      const prev = out[out.length - 1];
      if (Math.sqrt((pts[i].x - prev.x) ** 2 + (pts[i].y - prev.y) ** 2) >= minDist)
        out.push({ x: pts[i].x, y: pts[i].y });
    }
    if (out.length > 1) {
      const last = out[out.length - 1], first = out[0];
      if (Math.sqrt((last.x - first.x) ** 2 + (last.y - first.y) ** 2) < minDist * 0.5) out.pop();
    }
    return out;
  }

  _startDrag(evt, idx) {
    evt.preventDefault();
    this._dragIdx = idx;
    this._panning = false;
    try { evt.target.setPointerCapture(evt.pointerId); } catch (_) {}
  }

  _onDrag(evt) {
    if (this._dragIdx == null || !this._workPoly) return;
    evt.preventDefault();
    const enu = this._clientToEnu(evt);
    if (!enu) return;
    this._workPoly[this._dragIdx] = enu;
    this._updateDragHandles();
  }

  _updateDragHandles() {
    const poly = this._workPoly;
    const root = this.shadowRoot;
    const workPoly = root.querySelector("polygon[stroke='#ef6c00']");
    if (workPoly) workPoly.setAttribute("points", poly.map((p) => `${this._sx(p.x)},${this._sy(p.y)}`).join(" "));
    root.querySelectorAll(".vertex").forEach((el) => {
      const i = +el.dataset.idx;
      el.setAttribute("cx", this._sx(poly[i].x)); el.setAttribute("cy", this._sy(poly[i].y));
    });
    root.querySelectorAll(".delvert").forEach((el) => {
      const i = +el.dataset.idx;
      const r = parseFloat(root.querySelector(".vertex")?.getAttribute("r") || 1);
      el.setAttribute("x", (parseFloat(this._sx(poly[i].x)) + r * 1.3).toFixed(3));
      el.setAttribute("y", (parseFloat(this._sy(poly[i].y)) - r * 1.3).toFixed(3));
    });
    root.querySelectorAll(".midpoint").forEach((el) => {
      const edgeIdx = +el.dataset.edge;
      const p = poly[edgeIdx], q = poly[(edgeIdx + 1) % poly.length];
      const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
      const circle = el.querySelector("circle"), text = el.querySelector("text");
      if (circle) { circle.setAttribute("cx", this._sx(mx)); circle.setAttribute("cy", this._sy(my)); }
      if (text) { text.setAttribute("x", this._sx(mx)); text.setAttribute("y", this._sy(my)); }
    });
    const goPolygon = root.querySelector(`polygon[data-hash="${this._editHash}"]`);
    if (goPolygon) goPolygon.setAttribute("points", poly.map((p) => `${this._sx(p.x)},${this._sy(p.y)}`).join(" "));
  }

  _endDrag() {
    if (this._dragIdx != null) { this._dragIdx = null; this._render(); }
  }

  _insertVertex(edgeIdx) {
    if (!this._workPoly) return;
    const p = this._workPoly[edgeIdx], q = this._workPoly[(edgeIdx + 1) % this._workPoly.length];
    this._workPoly.splice(edgeIdx + 1, 0, { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 });
    this._render();
  }

  _deleteVertex(idx) {
    if (!this._workPoly || this._workPoly.length <= 3) return;
    this._workPoly.splice(idx, 1);
    this._render();
  }

  async _saveEdit() {
    if (!this._hass || !this._editHash || !this._workPoly || !this._config.mower_entity) return;
    const polygon = this._workPoly.map((p) => ({ x: +p.x.toFixed(4), y: +p.y.toFixed(4) }));
    const hashId = this._editHash;
    this._polyOverrides[hashId] = polygon;
    this._cancelEdit();
    try {
      await this._hass.callService("lymow", "update_zone_polygon", {
        entity_id: this._config.mower_entity,
        zone_hash_id: hashId,
        polygon,
      });
    } catch (err) {
      console.error("lymow-map-card: save failed", err);
      delete this._polyOverrides[hashId];
      this._render();
      const bar = this.shadowRoot.querySelector(".edit-bar");
      if (bar) bar.textContent = `⚠️ Save failed: ${err?.message || err}`;
    }
  }

  getCardSize() { return 5; }

  _wrapMsg(inner) {
    return `<style>:host{display:block}ha-card{padding:12px}.msg{padding:8px;color:var(--secondary-text-color);font-size:.9em;line-height:1.5}code{background:var(--code-editor-background-color,#f0f0f0);padding:1px 4px;border-radius:3px}</style><ha-card><div class="msg">${inner}</div></ha-card>`;
  }
}

customElements.define("lymow-map-card", LymowMapCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "lymow-map-card",
  name: "Lymow Map",
  description: "Interactive map: go/no-go zones, channels, charging station, RTK base, robot pose. Zoom, pan, expand, edit zones.",
  preview: false,
});
