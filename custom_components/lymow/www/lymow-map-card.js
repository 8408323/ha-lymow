/**
 * lymow-map-card  –  Lovelace card for the Lymow robotic mower integration
 *
 * Renders go-zones, no-go zones, the charging station and the live robot
 * position as an SVG map. Two modes:
 *   • View mode  – tap a go-zone to select it; "Mow selected zones" appears.
 *   • Edit mode  – tap "Edit", then tap a go-zone to edit its boundary:
 *                  drag the vertex handles, tap an edge midpoint (+) to insert
 *                  a vertex, tap a vertex's ✕ badge to delete it, then "Save"
 *                  (calls lymow.update_zone_polygon) or "Cancel".
 *
 * YAML config example:
 *   type: custom:lymow-map-card
 *   entity: sensor.lymow_THING_map          # required – the map sensor
 *   mower_entity: lawn_mower.lymow_THING    # required for mowing + editing
 *   title: My lawn                          # optional card title override
 */

class LymowMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._selectedZones = new Set();
    this._hass = null;
    this._config = null;
    // Edit state
    this._editing = false;
    this._editHash = null; // hashId of the zone being edited
    this._workPoly = null; // working copy: [{x, y}, ...] in ENU metres
    this._dragIdx = null; // index of the vertex currently being dragged
    this._tx = null; // {minX, maxY, scale} for SVG↔ENU inverse transform
  }

  setConfig(config) {
    if (!config.entity) {
      throw new Error("lymow-map-card: 'entity' is required");
    }
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
  // Data
  // ---------------------------------------------------------------------------

  _getMapData() {
    const state = this._hass.states[this._config.entity];
    if (!state) return null;
    const a = state.attributes;
    return {
      goZones: a.go_zones || [],
      nogoZones: a.nogo_zones || [],
      channels: a.channels || [],
      gpsOrigin: a.gps_origin || null,
      chargingStation: a.charging_station || null,
      poseEastM: a.poseEastM,
      poseNorthM: a.poseNorthM,
      poseThetaRad: a.poseThetaRad,
    };
  }

  _render() {
    if (!this._hass || !this._config) return;

    const mapData = this._getMapData();
    if (!mapData) {
      this.shadowRoot.innerHTML = this._wrapCard(
        `<div class="msg">Map entity not found:<br><code>${this._config.entity}</code></div>`
      );
      return;
    }

    const { goZones, nogoZones, channels, chargingStation, poseEastM, poseNorthM, poseThetaRad } = mapData;
    const allZones = [...goZones, ...nogoZones];

    if (allZones.length === 0) {
      this.shadowRoot.innerHTML = this._wrapCard(
        `<div class="msg">No map data available yet.<br>Try calling the <em>lymow.query_map</em> service.</div>`
      );
      return;
    }

    // ── Bounding box (includes the working polygon while editing) ─────────────
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const acc = (x, y) => {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    };
    for (const z of allZones) for (const p of z.polygon || []) acc(p.x, p.y);
    for (const c of channels) for (const p of c.polygon || []) acc(p.x, p.y);
    if (this._workPoly) for (const p of this._workPoly) acc(p.x, p.y);
    if (chargingStation) acc(chargingStation.x, chargingStation.y);
    if (poseEastM !== undefined && poseNorthM !== undefined) acc(poseEastM, poseNorthM);

    const PAD = Math.max(1, (maxX - minX + maxY - minY) * 0.04);
    minX -= PAD; maxX += PAD; minY -= PAD; maxY += PAD;

    const W = maxX - minX;
    const VBOX_W = 100;
    const scale = VBOX_W / W;
    const VBOX_H = (maxY - minY) * scale;
    this._tx = { minX, maxY, scale };

    // ENU metres → SVG user units (flip Y: SVG y grows downward, north grows up)
    const sx = (x) => ((x - minX) * scale).toFixed(3);
    const sy = (y) => ((maxY - y) * scale).toFixed(3);
    const fontSz = Math.max(1.2, Math.min(3, VBOX_W / 25)).toFixed(2);
    const nodeR = Math.max(0.8, VBOX_W / 70).toFixed(2);

    // ── Go-zone polygons ──────────────────────────────────────────────────────
    const goPaths = goZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const selected = this._selectedZones.has(z.hashId);
      const beingEdited = this._editing && this._editHash === z.hashId;
      const enabled = z.isEnabled !== false;
      const fill = beingEdited ? "#fff3e0" : selected ? "#2e7d32" : enabled ? "#a8d8a8" : "#c8e6c9";
      const stroke = beingEdited ? "#ef6c00" : selected ? "#81c784" : "#2e7d32";
      const opacity = enabled ? "1" : "0.55";
      return `<polygon data-hash="${z.hashId}" data-type="go" points="${pts}"
          fill="${fill}" stroke="${stroke}" stroke-width="0.35" opacity="${opacity}" style="cursor:pointer" />`;
    }).join("\n");

    const goLabels = goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const cx = z.polygon.reduce((s, p) => s + p.x, 0) / z.polygon.length;
      const cy = z.polygon.reduce((s, p) => s + p.y, 0) / z.polygon.length;
      const label = z.area != null ? `${z.area} m²` : z.hashId.slice(0, 6);
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle" font-size="${fontSz}" fill="#1b5e20" pointer-events="none" font-weight="bold">${label}</text>`;
    }).join("\n");

    const nogoPaths = nogoZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      return `<polygon points="${pts}" fill="#ffcccc" stroke="#c62828" stroke-width="0.35" opacity="0.8" />`;
    }).join("\n");

    // ── Channels (path corridors connecting zones; docking channel dashed) ────
    const channelPaths = channels.map((c) => {
      const poly = c.polygon || [];
      if (poly.length < 2) return "";
      const pts = poly.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const dash = c.isDockingChannel ? ' stroke-dasharray="1,0.6"' : "";
      const stroke = c.isValid === false ? "#bdbdbd" : "#8d6e63";
      // 3+ points form a corridor polygon; a 2-point channel is just a link line.
      // pointer-events:none so these decorative overlays don't steal taps meant
      // for the go-zones beneath them (only go-zones are interactive).
      if (poly.length >= 3) {
        return `<polygon points="${pts}" fill="#d7ccc8" stroke="${stroke}" stroke-width="0.3" opacity="0.7" pointer-events="none"${dash} />`;
      }
      return `<polyline points="${pts}" fill="none" stroke="${stroke}" stroke-width="0.5" opacity="0.8" pointer-events="none"${dash} />`;
    }).join("\n");

    // ── Edit handles (vertices + edge midpoints) for the zone under edit ──────
    let editHandles = "";
    if (this._editing && this._workPoly && this._workPoly.length) {
      const poly = this._workPoly;
      const midpoints = poly.map((p, i) => {
        const q = poly[(i + 1) % poly.length];
        const mx = (p.x + q.x) / 2, my = (p.y + q.y) / 2;
        return `<circle class="midpoint" data-edge="${i}" cx="${sx(mx)}" cy="${sy(my)}" r="${(nodeR * 0.7).toFixed(2)}"
            fill="#fff" stroke="#ef6c00" stroke-width="0.25" style="cursor:copy" />
          <text x="${sx(mx)}" y="${sy(my)}" text-anchor="middle" dominant-baseline="central" font-size="${(nodeR * 0.9).toFixed(2)}" fill="#ef6c00" pointer-events="none">+</text>`;
      }).join("\n");
      const verts = poly.map((p, i) => {
        const delBadge = poly.length > 3
          ? `<text class="delvert" data-idx="${i}" x="${(parseFloat(sx(p.x)) + nodeR * 1.2).toFixed(3)}" y="${(parseFloat(sy(p.y)) - nodeR * 1.2).toFixed(3)}" font-size="${(nodeR * 1.1).toFixed(2)}" fill="#c62828" style="cursor:pointer">✕</text>`
          : "";
        return `<circle class="vertex" data-idx="${i}" cx="${sx(p.x)}" cy="${sy(p.y)}" r="${nodeR}"
            fill="#ef6c00" stroke="#fff" stroke-width="0.3" style="cursor:grab" />${delBadge}`;
      }).join("\n");
      editHandles = midpoints + verts;
    }

    // ── Charging station ──────────────────────────────────────────────────────
    let csHtml = "";
    if (chargingStation) {
      const cx = sx(chargingStation.x), cy = sy(chargingStation.y);
      const r = Math.max(1.0, VBOX_W / 60).toFixed(2);
      const theta = chargingStation.theta || 0;
      const arrowLen = parseFloat(r) * 1.8;
      const ax = (parseFloat(cx) + Math.cos(theta) * arrowLen).toFixed(3);
      const ay = (parseFloat(cy) - Math.sin(theta) * arrowLen).toFixed(3);
      csHtml = `
        <circle cx="${cx}" cy="${cy}" r="${r}" fill="#1565c0" opacity="0.9"/>
        <circle cx="${cx}" cy="${cy}" r="${(parseFloat(r) * 0.55).toFixed(2)}" fill="white"/>
        <line x1="${cx}" y1="${cy}" x2="${ax}" y2="${ay}" stroke="#1565c0" stroke-width="0.4"/>
        <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="middle" font-size="${(parseFloat(r) * 0.9).toFixed(2)}" fill="#1565c0" pointer-events="none">⚡</text>`;
    }

    // ── Robot position ────────────────────────────────────────────────────────
    let robotHtml = "";
    if (poseEastM !== undefined && poseNorthM !== undefined) {
      const rx = sx(poseEastM), ry = sy(poseNorthM);
      const r = Math.max(0.8, VBOX_W / 80).toFixed(2);
      const theta = poseThetaRad || 0;
      const arrowLen = parseFloat(r) * 2.5;
      const ax = (parseFloat(rx) + Math.cos(theta) * arrowLen).toFixed(3);
      const ay = (parseFloat(ry) - Math.sin(theta) * arrowLen).toFixed(3);
      robotHtml = `
        <circle cx="${rx}" cy="${ry}" r="${r}" fill="#e65100" stroke="white" stroke-width="0.3"/>
        <line x1="${rx}" y1="${ry}" x2="${ax}" y2="${ay}" stroke="#e65100" stroke-width="0.5" stroke-linecap="round"/>`;
    }

    const northHtml = `
      <g transform="translate(${(VBOX_W - 4).toFixed(1)}, 4)">
        <circle r="3" fill="white" opacity="0.7"/>
        <line x1="0" y1="2.5" x2="0" y2="-2.5" stroke="#333" stroke-width="0.5"/>
        <polygon points="0,-2.5 -0.8,-0.5 0.8,-0.5" fill="#333"/>
        <text x="0" y="5.2" text-anchor="middle" font-size="1.8" fill="#333">N</text>
      </g>`;

    // ── Toolbar ───────────────────────────────────────────────────────────────
    const host = "this.getRootNode().host";
    let toolbar;
    if (this._editing) {
      const editingMsg = this._editHash
        ? `Editing <b>${this._editHash.slice(0, 6)}</b> — drag handles, tap + to add, ✕ to delete.`
        : `Tap a go-zone to edit its boundary.`;
      toolbar = `
        <div class="edit-bar">${editingMsg}</div>
        <div class="btn-row">
          ${this._editHash ? `<button class="btn save" onclick="${host}._saveEdit()">💾 Save</button>` : ""}
          <button class="btn cancel" onclick="${host}._cancelEdit()">Cancel</button>
        </div>`;
    } else {
      const hasSel = this._selectedZones.size > 0;
      const canMow = hasSel && !!this._config.mower_entity;
      const mowBtn = hasSel
        ? `<button class="btn mow" ${canMow ? "" : "disabled title='Set mower_entity in card config'"} onclick="${host}._mowSelected()">🌿 Mow selected (${this._selectedZones.size})</button>`
        : "";
      const editBtn = this._config.mower_entity
        ? `<button class="btn edit" onclick="${host}._enterEdit()">✏️ Edit map</button>`
        : "";
      toolbar = `<div class="btn-row">${mowBtn}${editBtn}</div>`;
    }

    const title = this._config.title ?? "Lymow Map";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 12px 8px; }
        .card-header { font-size: 1.05em; font-weight: 500; margin-bottom: 8px; color: var(--primary-text-color); }
        svg { width: 100%; border-radius: 6px; background: #eef6ee; display: block; touch-action: none; }
        .btn-row { display: flex; gap: 8px; margin-top: 8px; }
        .btn { flex: 1; padding: 10px 6px; border: none; border-radius: 6px; font-size: 0.86em; font-weight: 600; cursor: pointer; color: white; }
        .btn.mow, .btn.edit { background: var(--primary-color, #03a9f4); }
        .btn.save { background: #2e7d32; }
        .btn.cancel { background: #757575; }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn:not(:disabled):hover { filter: brightness(1.1); }
        .edit-bar { margin-top: 8px; font-size: 0.82em; color: var(--secondary-text-color); }
        .msg { padding: 14px; color: var(--secondary-text-color); font-size: 0.9em; line-height: 1.5; }
        code { font-size: 0.85em; background: var(--code-editor-background-color, #f0f0f0); padding: 1px 4px; border-radius: 3px; }
      </style>
      <ha-card>
        <div class="card-header">${title}</div>
        <svg viewBox="0 0 ${VBOX_W.toFixed(2)} ${VBOX_H.toFixed(2)}" xmlns="http://www.w3.org/2000/svg">
          ${nogoPaths}
          ${goPaths}
          ${channelPaths}
          ${goLabels}
          ${csHtml}
          ${robotHtml}
          ${editHandles}
          ${northHtml}
        </svg>
        ${toolbar}
      </ha-card>`;

    this._wireEvents();
  }

  // ---------------------------------------------------------------------------
  // Event wiring
  // ---------------------------------------------------------------------------

  _wireEvents() {
    const svg = this.shadowRoot.querySelector("svg");
    if (this._editing) {
      // Choose a zone to edit, or interact with its handles.
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("click", () => this._chooseEditZone(el.dataset.hash));
      });
      this.shadowRoot.querySelectorAll(".midpoint").forEach((el) => {
        el.addEventListener("click", (e) => { e.stopPropagation(); this._insertVertex(+el.dataset.edge); });
      });
      this.shadowRoot.querySelectorAll(".delvert").forEach((el) => {
        el.addEventListener("click", (e) => { e.stopPropagation(); this._deleteVertex(+el.dataset.idx); });
      });
      this.shadowRoot.querySelectorAll(".vertex").forEach((el) => {
        el.addEventListener("pointerdown", (e) => this._startDrag(e, +el.dataset.idx));
      });
      if (svg && this._editHash) {
        svg.addEventListener("pointermove", this._onDragBound = (e) => this._onDrag(e));
        svg.addEventListener("pointerup", this._onDragEndBound = () => this._endDrag());
        svg.addEventListener("pointerleave", this._onDragEndBound);
      }
    } else {
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("click", () => this._toggleZone(el.dataset.hash));
      });
    }
  }

  /** Convert a pointer event's client coords to ENU metres via the SVG CTM. */
  _clientToEnu(evt) {
    const svg = this.shadowRoot.querySelector("svg");
    const pt = svg.createSVGPoint();
    pt.x = evt.clientX;
    pt.y = evt.clientY;
    const u = pt.matrixTransform(svg.getScreenCTM().inverse()); // SVG user units
    const { minX, maxY, scale } = this._tx;
    return { x: u.x / scale + minX, y: maxY - u.y / scale };
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
    const zone = this._getMapData().goZones.find((z) => z.hashId === hashId);
    if (!zone || !zone.polygon) return;
    this._editHash = hashId;
    // Deep copy so dragging never mutates the live attribute data.
    this._workPoly = zone.polygon.map((p) => ({ x: p.x, y: p.y }));
    this._render();
  }

  _startDrag(evt, idx) {
    evt.preventDefault();
    this._dragIdx = idx;
    try {
      evt.target.setPointerCapture(evt.pointerId);
    } catch (_e) {
      /* setPointerCapture unsupported — pointermove on the svg still works */
    }
  }

  _onDrag(evt) {
    if (this._dragIdx == null || !this._workPoly) return;
    evt.preventDefault();
    this._workPoly[this._dragIdx] = this._clientToEnu(evt);
    this._render();
  }

  _endDrag() {
    this._dragIdx = null;
  }

  _insertVertex(edgeIdx) {
    if (!this._workPoly) return;
    const p = this._workPoly[edgeIdx];
    const q = this._workPoly[(edgeIdx + 1) % this._workPoly.length];
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
    await this._hass.callService("lymow", "update_zone_polygon", {
      entity_id: this._config.mower_entity,
      zone_hash_id: this._editHash,
      polygon: this._workPoly.map((p) => ({ x: +p.x.toFixed(3), y: +p.y.toFixed(3) })),
    });
    this._cancelEdit();
  }

  getCardSize() {
    return 5;
  }

  _wrapCard(inner) {
    return `<style>:host{display:block}ha-card{padding:12px}.msg{padding:8px;color:var(--secondary-text-color);font-size:.9em;line-height:1.5}code{background:var(--code-editor-background-color,#f0f0f0);padding:1px 4px;border-radius:3px}</style><ha-card>${inner}</ha-card>`;
  }
}

customElements.define("lymow-map-card", LymowMapCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "lymow-map-card",
  name: "Lymow Map",
  description: "Map with go/no-go zones, charging station, live robot — and an edit mode to drag zone boundaries.",
  preview: false,
});
