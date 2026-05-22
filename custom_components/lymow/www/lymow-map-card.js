/**
 * lymow-map-card  –  Lovelace card for the Lymow robotic mower integration
 *
 * Features:
 *   • Renders go-zones, no-go zones, channels, charging station, robot pose
 *   • Mouse wheel / pinch to zoom; drag anywhere on map to pan
 *   • Edit mode: tap a go-zone → drag vertex handles to reshape; tap edge
 *     midpoint (+) to insert a vertex; tap vertex ✕ to delete; Save / Cancel
 *   • North arrow + scale bar overlay (fixed to viewport corners via SVG foreignObject)
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

    // Edit state
    this._editing = false;
    this._editHash = null;
    this._workPoly = null;
    this._dragIdx = null;
    // Optimistic polygon overrides: hashId → [{x,y}] applied until next hass update
    this._polyOverrides = {};

    // Pan/zoom state (in SVG user units)
    this._vx = 0; this._vy = 0; this._vw = 100; this._vh = 100;
    this._mapReady = false;

    // Pan gesture (mouse/pointer)
    this._panning = false;
    this._panStart = null; // {x, y, vx, vy}
    this._panMoved = false; // distinguish pan from click

    // Pinch zoom
    this._pinchStart = null;

    // Bounds (set on first render with data)
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
    // Apply any optimistic polygon overrides (applied after a successful save)
    const goZones = (a.go_zones || []).map((z) =>
      this._polyOverrides[z.hashId]
        ? { ...z, polygon: this._polyOverrides[z.hashId] }
        : z
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
    };
  }

  _computeBounds(mapData) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const acc = (x, y) => {
      if (!isFinite(x) || !isFinite(y)) return;
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    };
    const { goZones, nogoZones, channels, chargingStation, poseEastM, poseNorthM } = mapData;
    for (const z of [...goZones, ...nogoZones]) for (const p of z.polygon || []) acc(p.x, p.y);
    for (const ch of channels) for (const p of ch.polygon || []) acc(p.x, p.y);
    if (chargingStation) acc(chargingStation.x, chargingStation.y);
    if (poseEastM !== undefined && poseNorthM !== undefined) acc(poseEastM, poseNorthM);
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
    return {
      x: svgX / this._scale + this._bounds.minX,
      y: this._bounds.maxY - svgY / this._scale,
    };
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
      this.shadowRoot.innerHTML = this._wrapMsg(
        `Map entity not found: <code>${this._config.entity}</code>`
      );
      return;
    }

    const { goZones, nogoZones, channels, chargingStation, poseEastM, poseNorthM, poseThetaRad } = mapData;
    const allZones = [...goZones, ...nogoZones];

    if (allZones.length === 0 && !chargingStation) {
      this.shadowRoot.innerHTML = this._wrapMsg(
        `No map data yet. Call <em>lymow.query_map</em> or wait for the robot to connect.`
      );
      return;
    }

    const newBounds = this._computeBounds(mapData);
    if (!newBounds) { this.shadowRoot.innerHTML = this._wrapMsg("Empty map."); return; }

    if (!this._mapReady) {
      this._bounds = newBounds;
      const W = newBounds.maxX - newBounds.minX;
      const H = newBounds.maxY - newBounds.minY;
      this._scale = 100 / W;
      this._vw = 100;
      this._vh = H * this._scale;
      this._vx = 0;
      this._vy = 0;
      this._mapReady = true;
    } else if (this._editing) {
      // Recompute bounds to include workPoly extent but don't reset viewport
      this._bounds = newBounds;
      const W = newBounds.maxX - newBounds.minX;
      this._scale = 100 / W;
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

    // ── Go-zones (rendered BEFORE no-go so nogo appears on top) ──────────────
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

    const goLabels = goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const cx = z.polygon.reduce((s, p) => s + p.x, 0) / z.polygon.length;
      const cy = z.polygon.reduce((s, p) => s + p.y, 0) / z.polygon.length;
      const label = z.area != null ? `${z.area} m²` : z.hashId.slice(0, 6);
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle"
        font-size="${fontSz}" fill="#1b5e20" pointer-events="none" font-weight="bold">${label}</text>`;
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
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle" font-size="${(parseFloat(fontSz)*0.85).toFixed(2)}" fill="#c62828" pointer-events="none">⛔</text>`;
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
          <circle cx="${sx(mx)}" cy="${sy(my)}" r="${(parseFloat(nodeR)*0.75).toFixed(2)}"
            fill="white" stroke="#ef6c00" stroke-width="0.3"/>
          <text x="${sx(mx)}" y="${sy(my)}" text-anchor="middle" dominant-baseline="central"
            font-size="${(parseFloat(nodeR)*0.9).toFixed(2)}" fill="#ef6c00" pointer-events="none">+</text>
        </g>`;
      }).join("\n");

      const verts = poly.map((p, i) => {
        const delBadge = poly.length > 3
          ? `<text class="delvert" data-idx="${i}"
              x="${(parseFloat(sx(p.x)) + parseFloat(nodeR) * 1.3).toFixed(3)}"
              y="${(parseFloat(sy(p.y)) - parseFloat(nodeR) * 1.3).toFixed(3)}"
              font-size="${(parseFloat(nodeR)*1.1).toFixed(2)}" fill="#c62828" style="cursor:pointer">✕</text>`
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
      const theta = chargingStation.theta || 0;
      const arrowLen = parseFloat(r) * 2;
      const ax = (parseFloat(cx) + Math.cos(theta) * arrowLen).toFixed(3);
      const ay = (parseFloat(cy) - Math.sin(theta) * arrowLen).toFixed(3);
      csHtml = `
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="#1565c0" opacity="0.9"/>
        <circle cx="${cx}" cy="${cy}" r="${(parseFloat(r)*0.5).toFixed(2)}" fill="white"/>
        <line x1="${cx}" y1="${cy}" x2="${ax}" y2="${ay}" stroke="#1565c0" stroke-width="0.5"/>
        <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="middle"
          font-size="${(parseFloat(r)*0.85).toFixed(2)}" fill="white" pointer-events="none" font-weight="bold">⚡</text>`;
    }

    // ── Robot position ────────────────────────────────────────────────────────
    let robotHtml = "";
    if (poseEastM !== undefined && poseNorthM !== undefined) {
      const rx = sx(poseEastM), ry = sy(poseNorthM);
      const r = Math.max(0.9, TOTAL_W / 75).toFixed(2);
      const theta = poseThetaRad || 0;
      const arrowLen = parseFloat(r) * 2.8;
      const ax = (parseFloat(rx) + Math.cos(theta) * arrowLen).toFixed(3);
      const ay = (parseFloat(ry) - Math.sin(theta) * arrowLen).toFixed(3);
      robotHtml = `
        <circle cx="${rx}" cy="${ry}" r="${r}" fill="#e65100" stroke="white" stroke-width="0.35"/>
        <line x1="${rx}" y1="${ry}" x2="${ax}" y2="${ay}" stroke="#e65100" stroke-width="0.6" stroke-linecap="round"/>`;
    }

    // ── North arrow (fixed to top-right of viewport) ──────────────────────────
    // Uses viewBox-relative coordinates so it stays in corner regardless of zoom/pan.
    const NX = (this._vx + this._vw - 5).toFixed(2);
    const NY = (this._vy + 5).toFixed(2);
    const northHtml = `
      <g data-overlay="north" transform="translate(${NX}, ${NY})" pointer-events="none">
        <circle r="3.5" fill="white" opacity="0.85"/>
        <line x1="0" y1="2.5" x2="0" y2="-2.5" stroke="#333" stroke-width="0.5"/>
        <polygon points="0,-2.5 -0.9,-0.3 0.9,-0.3" fill="#c0392b"/>
        <text x="0" y="5.5" text-anchor="middle" font-size="2" fill="#333" font-weight="bold">N</text>
      </g>`;

    // ── Scale bar (fixed to bottom-left of viewport) ──────────────────────────
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
        ? `<button class="btn mow" ${canMow ? "" : "disabled title='Set mower_entity in card config'"} onclick="${host}._mowSelected()">🌿 Mow selected (${this._selectedZones.size})</button>`
        : "";
      const editBtn = this._config.mower_entity
        ? `<button class="btn edit" onclick="${host}._enterEdit()">✏️ Edit zones</button>`
        : "";
      const resetBtn = `<button class="btn reset" onclick="${host}._resetView()" title="Reset zoom/pan">⊡</button>`;
      toolbar = `<div class="btn-row">${mowBtn}${editBtn}${resetBtn}</div>`;
    }

    const title = this._config.title ?? "Lymow Map";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 12px 8px; box-sizing: border-box; }
        .card-header { font-size: 1.05em; font-weight: 500; margin-bottom: 8px; color: var(--primary-text-color); }
        .map-wrap { width: 100%; aspect-ratio: ${(TOTAL_W / TOTAL_H).toFixed(4)}; position: relative; }
        svg { width: 100%; height: 100%; border-radius: 6px; background: #e8f5e9; display: block; touch-action: none; user-select: none; cursor: grab; }
        svg.panning { cursor: grabbing; }
        .btn-row { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
        .btn { flex: 1; min-width: 80px; padding: 9px 6px; border: none; border-radius: 6px;
               font-size: 0.84em; font-weight: 600; cursor: pointer; color: white; }
        .btn.mow, .btn.edit { background: var(--primary-color, #03a9f4); }
        .btn.save { background: #2e7d32; }
        .btn.cancel { background: #757575; flex: 0; }
        .btn.reset { background: #455a64; flex: 0; min-width: 36px; }
        .btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .btn:not(:disabled):hover { filter: brightness(1.1); }
        .edit-bar { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 6px; }
        .msg { padding: 14px; color: var(--secondary-text-color); font-size: 0.9em; line-height: 1.5; }
        code { background: var(--code-editor-background-color,#f0f0f0); padding: 1px 4px; border-radius: 3px; }
        .legend { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; font-size: 0.76em; color: var(--secondary-text-color); }
        .legend-item { display: flex; align-items: center; gap: 4px; }
        .legend-dot { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
      </style>
      <ha-card>
        <div class="card-header">${title}</div>
        <div class="map-wrap">
          <svg viewBox="${this._vx.toFixed(3)} ${this._vy.toFixed(3)} ${this._vw.toFixed(3)} ${this._vh.toFixed(3)}"
               xmlns="http://www.w3.org/2000/svg"
               preserveAspectRatio="xMidYMid meet">
            ${channelPaths}
            ${goPaths}
            ${goLabels}
            ${nogoPaths}
            ${nogoLabels}
            ${csHtml}
            ${robotHtml}
            ${editOverlay}
            ${northHtml}
            ${scaleBarHtml}
          </svg>
        </div>
        ${toolbar}
        <div class="legend">
          <div class="legend-item"><div class="legend-dot" style="background:#a8d8a8;border:1px solid #388e3c"></div>Go zone</div>
          ${nogoZones.length ? '<div class="legend-item"><div class="legend-dot" style="background:#ff5252;opacity:0.5;border:1px solid #c62828"></div>No-go zone</div>' : ""}
          ${chargingStation ? '<div class="legend-item"><div class="legend-dot" style="background:#1565c0"></div>Charging station ⚡</div>' : ""}
          ${poseEastM !== undefined ? '<div class="legend-item"><div class="legend-dot" style="background:#e65100"></div>Robot</div>' : ""}
          ${channels.some(c => c.isDockingChannel) ? '<div class="legend-item"><div class="legend-dot" style="background:none;border-bottom:2px dashed #1565c0;border-radius:0;width:16px"></div>Docking channel</div>' : ""}
          ${channels.some(c => !c.isDockingChannel) ? '<div class="legend-item"><div class="legend-dot" style="background:none;border-bottom:2px dashed #6a1b9a;border-radius:0;width:16px"></div>Mow channel</div>' : ""}
        </div>
      </ha-card>`;

    this._wireEvents();
  }

  _niceNumber(x) {
    if (x <= 0) return 5;
    const magnitude = Math.pow(10, Math.floor(Math.log10(x)));
    for (const n of [1, 2, 5, 10]) if (n * magnitude >= x) return n * magnitude;
    return 10 * magnitude;
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
      // Zone selection (click without drag)
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("click", (e) => { if (!this._panMoved) this._chooseEditZone(el.dataset.hash); });
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
        svg.addEventListener("pointerup", (e) => this._endDrag(e));
        svg.addEventListener("pointercancel", () => this._endDrag(null));
      }
    } else {
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("click", (e) => { if (!this._panMoved) { e.stopPropagation(); this._toggleZone(el.dataset.hash); } });
      });
    }

    // Pan: works on any pointerdown on the SVG (not on vertex handles in edit mode)
    svg.addEventListener("pointerdown", (e) => {
      if (this._dragIdx != null) return; // vertex drag takes priority
      this._panning = true;
      this._panMoved = false;
      this._panStart = { x: e.clientX, y: e.clientY, vx: this._vx, vy: this._vy };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add("panning");
    });
    svg.addEventListener("pointermove", (e) => this._onPan(e));
    svg.addEventListener("pointerup", () => {
      this._panning = false;
      svg.classList.remove("panning");
    });
    svg.addEventListener("pointercancel", () => {
      this._panning = false;
      svg.classList.remove("panning");
    });
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
    const factor = evt.deltaY < 0 ? 0.85 : 1 / 0.85;
    this._applyZoom(factor, px, py);
  }

  _onTouchStart(e) {
    if (e.touches.length === 2) {
      e.preventDefault();
      this._pinchStart = {
        dist: this._touchDist(e),
        vx: this._vx, vy: this._vy, vw: this._vw, vh: this._vh,
        cx: (e.touches[0].clientX + e.touches[1].clientX) / 2,
        cy: (e.touches[0].clientY + e.touches[1].clientY) / 2,
      };
    }
  }

  _onTouchMove(e) {
    if (e.touches.length === 2 && this._pinchStart) {
      e.preventDefault();
      const newDist = this._touchDist(e);
      const factor = this._pinchStart.dist / newDist;
      const svg = this.shadowRoot.querySelector("svg");
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const { cx, cy, vx, vy, vw, vh } = this._pinchStart;
      const px = vx + (cx - rect.left) / rect.width * vw;
      const py = vy + (cy - rect.top) / rect.height * vh;
      this._setViewBox(vw * factor, vh * factor, px, py);
      this._updateViewBox();
    }
  }

  _onTouchEnd(e) {
    if (e.touches.length < 2) this._pinchStart = null;
  }

  _touchDist(e) {
    const dx = e.touches[0].clientX - e.touches[1].clientX;
    const dy = e.touches[0].clientY - e.touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  _applyZoom(factor, pivotX, pivotY) {
    this._setViewBox(this._vw * factor, this._vh * factor, pivotX, pivotY);
    this._updateViewBox();
    // Reposition fixed overlays (north arrow, scale bar) after zoom/pan
    this._updateOverlays();
  }

  _setViewBox(newW, newH, pivotX, pivotY) {
    const TOTAL_W = (this._bounds.maxX - this._bounds.minX) * this._scale;
    const TOTAL_H = (this._bounds.maxY - this._bounds.minY) * this._scale;
    const minW = TOTAL_W / _ZOOM_MAX;
    const maxW = TOTAL_W / _ZOOM_MIN;
    newW = Math.max(minW, Math.min(maxW, newW));
    newH = newW * (TOTAL_H / TOTAL_W);

    const ratioX = (pivotX - this._vx) / this._vw;
    const ratioY = (pivotY - this._vy) / this._vh;
    this._vx = pivotX - ratioX * newW;
    this._vy = pivotY - ratioY * newH;
    this._vw = newW;
    this._vh = newH;

    this._vx = Math.max(-TOTAL_W * 0.3, Math.min(TOTAL_W * 1.3 - newW, this._vx));
    this._vy = Math.max(-TOTAL_H * 0.3, Math.min(TOTAL_H * 1.3 - newH, this._vy));
  }

  _updateViewBox() {
    const svg = this.shadowRoot.querySelector("svg");
    if (svg) svg.setAttribute("viewBox", `${this._vx.toFixed(3)} ${this._vy.toFixed(3)} ${this._vw.toFixed(3)} ${this._vh.toFixed(3)}`);
  }

  /** Move north arrow and scale bar to stay at viewport corners after zoom/pan. */
  _updateOverlays() {
    const root = this.shadowRoot;
    const sc = this._scale;

    // North arrow: top-right
    const NX = (this._vx + this._vw - 5).toFixed(2);
    const NY = (this._vy + 5).toFixed(2);
    const northG = root.querySelector("g[data-overlay='north']");
    if (northG) northG.setAttribute("transform", `translate(${NX}, ${NY})`);

    // Scale bar: bottom-left
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
    if (barRect) {
      barRect.setAttribute("x", bx); barRect.setAttribute("y", by); barRect.setAttribute("width", barW);
    }
    const barLines = root.querySelectorAll("line[data-overlay='scalebar']");
    if (barLines[0]) { barLines[0].setAttribute("x1", bx); barLines[0].setAttribute("y1", by); barLines[0].setAttribute("x2", bx); barLines[0].setAttribute("y2", byTick); }
    if (barLines[1]) { barLines[1].setAttribute("x1", bx2); barLines[1].setAttribute("y1", by); barLines[1].setAttribute("x2", bx2); barLines[1].setAttribute("y2", byTick); }
    const barText = root.querySelector("text[data-overlay='scalebar']");
    if (barText) { barText.setAttribute("x", bxMid); barText.setAttribute("y", byLabel); barText.textContent = `${niceMetres} m`; }
  }

  _resetView() {
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
    // Only start panning after 3px movement to preserve click detection
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
  // View-mode selection / mow
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
    this._editing = true;
    this._editHash = null;
    this._workPoly = null;
    this._selectedZones.clear();
    this._render();
  }

  _cancelEdit() {
    this._editing = false;
    this._editHash = null;
    this._workPoly = null;
    this._dragIdx = null;
    this._render();
  }

  _chooseEditZone(hashId) {
    if (this._editHash === hashId) return;
    const zone = (this._getMapData()?.goZones || []).find((z) => z.hashId === hashId);
    if (!zone || !zone.polygon) return;
    this._editHash = hashId;
    this._workPoly = this._decimatePoly(zone.polygon);
    this._render();
  }

  /** Reduce dense protobuf vertices to at most MAX_VERTS edit handles. */
  _decimatePoly(pts) {
    const MAX_VERTS = 32;
    if (pts.length <= MAX_VERTS) return pts.map((p) => ({ x: p.x, y: p.y }));
    let perim = 0;
    for (let i = 0; i < pts.length; i++) {
      const q = pts[(i + 1) % pts.length];
      const dx = q.x - pts[i].x, dy = q.y - pts[i].y;
      perim += Math.sqrt(dx * dx + dy * dy);
    }
    const minDist = perim / MAX_VERTS;
    const out = [{ x: pts[0].x, y: pts[0].y }];
    for (let i = 1; i < pts.length; i++) {
      const prev = out[out.length - 1];
      const dx = pts[i].x - prev.x, dy = pts[i].y - prev.y;
      if (Math.sqrt(dx * dx + dy * dy) >= minDist) out.push({ x: pts[i].x, y: pts[i].y });
    }
    if (out.length > 1) {
      const last = out[out.length - 1], first = out[0];
      const dx = last.x - first.x, dy = last.y - first.y;
      if (Math.sqrt(dx * dx + dy * dy) < minDist * 0.5) out.pop();
    }
    return out;
  }

  _startDrag(evt, idx) {
    evt.preventDefault();
    this._dragIdx = idx;
    this._panning = false; // cancel any pan in progress
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
    if (workPoly) {
      workPoly.setAttribute("points", poly.map((p) => `${this._sx(p.x)},${this._sy(p.y)}`).join(" "));
    }

    root.querySelectorAll(".vertex").forEach((el) => {
      const i = +el.dataset.idx;
      el.setAttribute("cx", this._sx(poly[i].x));
      el.setAttribute("cy", this._sy(poly[i].y));
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
      const circle = el.querySelector("circle");
      const text = el.querySelector("text");
      if (circle) { circle.setAttribute("cx", this._sx(mx)); circle.setAttribute("cy", this._sy(my)); }
      if (text) { text.setAttribute("x", this._sx(mx)); text.setAttribute("y", this._sy(my)); }
    });

    // Also update the go-zone polygon so it tracks the edit in real time
    const goPolygon = root.querySelector(`polygon[data-hash="${this._editHash}"]`);
    if (goPolygon) {
      goPolygon.setAttribute("points", poly.map((p) => `${this._sx(p.x)},${this._sy(p.y)}`).join(" "));
    }
  }

  _endDrag(evt) {
    if (this._dragIdx != null) {
      this._dragIdx = null;
      this._render();
    }
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
    // Optimistic update: apply the new polygon immediately so the map updates
    // before HA state propagates back (which may take several seconds).
    this._polyOverrides[hashId] = polygon;
    this._cancelEdit(); // exits edit mode and re-renders with the override applied
    try {
      await this._hass.callService("lymow", "update_zone_polygon", {
        entity_id: this._config.mower_entity,
        zone_hash_id: hashId,
        polygon,
      });
    } catch (err) {
      console.error("lymow-map-card: save failed", err);
      // Roll back optimistic update and show error
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
  description: "Interactive map: go/no-go zones, channels, charging station, robot pose. Zoom, pan, and edit zone boundaries by dragging.",
  preview: false,
});
