/**
 * lymow-map-card  –  Lovelace card for the Lymow robotic mower integration
 *
 * Renders go-zones, no-go zones, the charging station and the live robot
 * position as an SVG map.  Tapping a go-zone selects it; a "Mow selected
 * zones" button appears once at least one zone is selected.
 *
 * YAML config example:
 *   type: custom:lymow-map-card
 *   entity: sensor.lymow_THING_map          # required – the map sensor
 *   mower_entity: lawn_mower.lymow_THING    # required for zone mowing
 *   title: My lawn                          # optional card title override
 */

class LymowMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._selectedZones = new Set();
    this._hass = null;
    this._config = null;
  }

  /** Called by the Lovelace UI with the user's card YAML config. */
  setConfig(config) {
    if (!config.entity) {
      throw new Error("lymow-map-card: 'entity' is required");
    }
    this._config = config;
  }

  /** Stub shown in the visual card picker. */
  static getStubConfig() {
    return { entity: "sensor.lymow_map", mower_entity: "lawn_mower.lymow" };
  }

  /** Called by HA on every state update. */
  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  // ---------------------------------------------------------------------------
  // Private helpers
  // ---------------------------------------------------------------------------

  _getMapData() {
    const state = this._hass.states[this._config.entity];
    if (!state) return null;
    const a = state.attributes;
    return {
      goZones: a.go_zones || [],
      nogoZones: a.nogo_zones || [],
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

    const { goZones, nogoZones, chargingStation, poseEastM, poseNorthM, poseThetaRad } = mapData;
    const allZones = [...goZones, ...nogoZones];

    if (allZones.length === 0) {
      this.shadowRoot.innerHTML = this._wrapCard(
        `<div class="msg">No map data available yet.<br>Try calling the <em>lymow.query_map</em> service.</div>`
      );
      return;
    }

    // ── Compute bounding box ──────────────────────────────────────────────────
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const z of allZones) {
      for (const p of z.polygon || []) {
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.y > maxY) maxY = p.y;
      }
    }
    if (chargingStation) {
      minX = Math.min(minX, chargingStation.x);
      maxX = Math.max(maxX, chargingStation.x);
      minY = Math.min(minY, chargingStation.y);
      maxY = Math.max(maxY, chargingStation.y);
    }
    if (poseEastM !== undefined && poseNorthM !== undefined) {
      minX = Math.min(minX, poseEastM);
      maxX = Math.max(maxX, poseEastM);
      minY = Math.min(minY, poseNorthM);
      maxY = Math.max(maxY, poseNorthM);
    }

    const PAD = Math.max(1, (maxX - minX + maxY - minY) * 0.04);
    minX -= PAD; maxX += PAD; minY -= PAD; maxY += PAD;

    const W = maxX - minX;
    const H = maxY - minY;
    const VBOX_W = 100;
    const scale = VBOX_W / W;
    const VBOX_H = H * scale;

    /**
     * Convert ENU metres → SVG user units.
     * ENU: x = east (+right), y = north (+up)
     * SVG: x = right, y = DOWN  →  flip y: svgY = (maxY - y) * scale
     */
    const sx = (x) => ((x - minX) * scale).toFixed(3);
    const sy = (y) => ((maxY - y) * scale).toFixed(3);

    const fontSz = Math.max(1.2, Math.min(3, VBOX_W / 25)).toFixed(2);

    // ── Go-zone polygons ──────────────────────────────────────────────────────
    const goPaths = goZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const selected = this._selectedZones.has(z.hashId);
      const enabled = z.isEnabled !== false;
      const fill = selected ? "#2e7d32" : (enabled ? "#a8d8a8" : "#c8e6c9");
      const stroke = selected ? "#81c784" : "#2e7d32";
      const opacity = enabled ? "1" : "0.55";
      return `<polygon
          data-hash="${z.hashId}"
          data-type="go"
          points="${pts}"
          fill="${fill}"
          stroke="${stroke}"
          stroke-width="0.35"
          opacity="${opacity}"
          style="cursor:pointer"
        />`;
    }).join("\n");

    // ── Go-zone labels ────────────────────────────────────────────────────────
    const goLabels = goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const cx = z.polygon.reduce((s, p) => s + p.x, 0) / z.polygon.length;
      const cy = z.polygon.reduce((s, p) => s + p.y, 0) / z.polygon.length;
      const label = z.area != null ? `${z.area} m²` : z.hashId.slice(0, 6);
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle" font-size="${fontSz}" fill="#1b5e20" pointer-events="none" font-weight="bold">${label}</text>`;
    }).join("\n");

    // ── No-go zone polygons ───────────────────────────────────────────────────
    const nogoPaths = nogoZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      return `<polygon points="${pts}" fill="#ffcccc" stroke="#c62828" stroke-width="0.35" opacity="0.8" />`;
    }).join("\n");

    // ── Charging station ──────────────────────────────────────────────────────
    let csHtml = "";
    if (chargingStation) {
      const cx = sx(chargingStation.x);
      const cy = sy(chargingStation.y);
      const r = Math.max(1.0, VBOX_W / 60).toFixed(2);
      const theta = chargingStation.theta || 0;
      const arrowLen = parseFloat(r) * 1.8;
      // In SVG after Y-flip: east=+x, north=-y → arrow: ax=cx+cos(θ)*l, ay=cy-sin(θ)*l
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
      const rx = sx(poseEastM);
      const ry = sy(poseNorthM);
      const r = Math.max(0.8, VBOX_W / 80).toFixed(2);
      const theta = poseThetaRad || 0;
      const arrowLen = parseFloat(r) * 2.5;
      const ax = (parseFloat(rx) + Math.cos(theta) * arrowLen).toFixed(3);
      const ay = (parseFloat(ry) - Math.sin(theta) * arrowLen).toFixed(3);
      robotHtml = `
        <circle cx="${rx}" cy="${ry}" r="${r}" fill="#e65100" stroke="white" stroke-width="0.3"/>
        <line x1="${rx}" y1="${ry}" x2="${ax}" y2="${ay}" stroke="#e65100" stroke-width="0.5" stroke-linecap="round"/>`;
    }

    // ── North arrow (decorative) ──────────────────────────────────────────────
    const northHtml = `
      <g transform="translate(${(VBOX_W - 4).toFixed(1)}, 4)">
        <circle r="3" fill="white" opacity="0.7"/>
        <line x1="0" y1="2.5" x2="0" y2="-2.5" stroke="#333" stroke-width="0.5"/>
        <polygon points="0,-2.5 -0.8,-0.5 0.8,-0.5" fill="#333"/>
        <text x="0" y="5.2" text-anchor="middle" font-size="1.8" fill="#333">N</text>
      </g>`;

    // ── "Mow" button ──────────────────────────────────────────────────────────
    const hasSelected = this._selectedZones.size > 0;
    const canMow = hasSelected && !!this._config.mower_entity;
    const btnHtml = hasSelected
      ? `<button class="mow-btn" ${canMow ? "" : "disabled title='Set mower_entity in card config'"} onclick="this.getRootNode().host._mowSelected()">
          🌿 Mow selected zone${this._selectedZones.size > 1 ? "s" : ""} (${this._selectedZones.size})
         </button>`
      : "";

    const title = this._config.title ?? "Lymow Map";

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 12px 8px; }
        .card-header { font-size: 1.05em; font-weight: 500; margin-bottom: 8px; color: var(--primary-text-color); }
        svg { width: 100%; border-radius: 6px; background: #eef6ee; display: block; }
        .mow-btn {
          margin-top: 8px; width: 100%; padding: 10px 6px;
          background: var(--primary-color, #03a9f4); color: white;
          border: none; border-radius: 6px; font-size: 0.88em; font-weight: 600;
          cursor: pointer; letter-spacing: 0.02em;
        }
        .mow-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .mow-btn:not(:disabled):hover { filter: brightness(1.1); }
        .msg { padding: 14px; color: var(--secondary-text-color); font-size: 0.9em; line-height: 1.5; }
        code { font-size: 0.85em; background: var(--code-editor-background-color, #f0f0f0); padding: 1px 4px; border-radius: 3px; }
      </style>
      <ha-card>
        <div class="card-header">${title}</div>
        <svg viewBox="0 0 ${VBOX_W.toFixed(2)} ${VBOX_H.toFixed(2)}" xmlns="http://www.w3.org/2000/svg">
          ${nogoPaths}
          ${goPaths}
          ${goLabels}
          ${csHtml}
          ${robotHtml}
          ${northHtml}
        </svg>
        ${btnHtml}
      </ha-card>`;

    // Attach tap listeners for zone selection (after innerHTML is set)
    this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
      el.addEventListener("click", () => this._toggleZone(el.dataset.hash));
    });
  }

  _toggleZone(hashId) {
    if (this._selectedZones.has(hashId)) {
      this._selectedZones.delete(hashId);
    } else {
      this._selectedZones.add(hashId);
    }
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

  // Required by Lovelace to compute the card height
  getCardSize() {
    return 4;
  }

  _wrapCard(inner) {
    return `<style>:host{display:block}ha-card{padding:12px}.msg{padding:8px;color:var(--secondary-text-color);font-size:.9em;line-height:1.5}code{background:var(--code-editor-background-color,#f0f0f0);padding:1px 4px;border-radius:3px}</style><ha-card>${inner}</ha-card>`;
  }
}

customElements.define("lymow-map-card", LymowMapCard);

// Register with the Lovelace card picker
window.customCards = window.customCards || [];
window.customCards.push({
  type: "lymow-map-card",
  name: "Lymow Map",
  description: "Displays the mowing map with go/no-go zones, charging station and live robot position.",
  preview: false,
});
