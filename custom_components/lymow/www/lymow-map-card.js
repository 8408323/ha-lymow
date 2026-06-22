/**
 * lymow-map-card  –  Lovelace card for the Lymow robotic mower integration
 *
 * Features:
 *   • Renders go-zones, no-go zones, channels, charging station, robot pose, RTK base
 *   • Mouse wheel / pinch to zoom; drag anywhere on map to pan
 *   • Expand button: fills the full browser viewport
 *   • Status bar: work status, battery, mow progress, RTK fix badge
 *   • Edit mode: tap a go-zone or no-go zone → drag vertices; tap edge + to insert;
 *     tap ✕ to delete vertex; Save / Delete zone / Cancel; rename zone in edit mode
 *   • Zone enable/disable: long-press a go-zone to toggle enabled state (calls set_zone_enabled)
 *   • Map rotation: right-click drag to rotate; click north arrow to reset to north-up
 *   • Pin-and-go: double-tap anywhere on map to send robot to that point
 *   • North arrow + scale bar fixed to viewport corners (pixel-space, no zoom scaling)
 *   • Markers (robot, RTK, station) fixed pixel size via inverse-zoom SVG transform
 *   • Mowing settings panel: speed, spacing, laps, direction, obstacle avoidance
 *   • Schedule viewer: shows next mowing schedule
 *   • RTK auto-pause: optional config to pause when fix quality degrades
 *
 * YAML config example:
 *   type: custom:lymow-map-card
 *   entity: sensor.lymow_THING_map        # required – the map sensor
 *   mower_entity: lawn_mower.lymow_THING  # required for controls + editing
 *   schedule_entity: sensor.lymow_THING_mow_schedules  # optional
 *   title: My lawn                        # optional
 *   rtk_autopause: true                   # optional – auto-pause on fix loss
 *   rtk_autopause_min_fix: 2              # optional – 0-3, default 2
 *   show_camera: true                     # optional – adds a 📹 button that opens
 *                                         #   the camera overlay (off by default)
 *   camera_entity: camera.lymow_THING_camera  # optional – LAN view in the overlay
 *   camera_default_source: lan | cloud    # optional – overlay's initial source
 */

const _ZOOM_MIN = 0.5;
const _ZOOM_MAX = 20;

// Fixed pixel sizes for overlays (independent of zoom level)
const _MARKER_PX = 18;   // robot / RTK / station marker diameter in px
const _NORTH_PX  = 44;   // north arrow circle diameter in px
const _SCALEBAR_PX_W = 80; // target scale bar width in px

// Graham scan convex hull for mowed-area overlay. Points are {x, y} in ENU metres.
// Returns the hull in CCW order, or [] if fewer than 3 distinct points.
function _convexHull(pts) {
  if (!pts || pts.length < 3) return pts || [];
  const p = pts.slice().sort((a, b) => a.x !== b.x ? a.x - b.x : a.y - b.y);
  const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower = [], upper = [];
  for (const pt of p) {
    while (lower.length >= 2 && cross(lower[lower.length-2], lower[lower.length-1], pt) <= 0) lower.pop();
    lower.push(pt);
  }
  for (let i = p.length - 1; i >= 0; i--) {
    const pt = p[i];
    while (upper.length >= 2 && cross(upper[upper.length-2], upper[upper.length-1], pt) <= 0) upper.pop();
    upper.push(pt);
  }
  upper.pop(); lower.pop();
  return lower.concat(upper);
}

class LymowMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._selectedZones = new Set();
    this._hass = null;
    this._config = null;
    this._expanded = false;

    // Edit state
    this._lastZoneCount = 0;
    this._settingsOpen = false;
    this._scheduleOpen = false;
    this._backupOpen = false;
    this._backupRenaming = null; // file key being renamed inline
    this._advancedOpen = false; // tracks <details class="sp-advanced"> open state across renders
    this._settingsValues = null;
    // 0=name, 1=area, 2=both, 3=none — persisted in localStorage across reloads
    this._goLabelMode = parseInt(localStorage.getItem("lymow_go_label_mode") ?? "0", 10);
    this._nogoLabelMode = parseInt(localStorage.getItem("lymow_nogo_label_mode") ?? "3", 10);
    this._chLabelMode = parseInt(localStorage.getItem("lymow_ch_label_mode") ?? "3", 10);
    this._editing = false;
    this._editHash = null;
    this._editType = null; // "go" | "nogo" | "channel"
    this._editRename = false; // rename mode within edit
    this._workPoly = null;
    this._dragIdx = null;
    this._dragStation = false;
    this._polyOverrides = {};
    this._nogoOverrides = {};
    this._nameOverrides = {}; // optimistic rename for go/nogo until next MQTT update
    // Channel names have no protobuf field — store client-side keyed by hashId.
    this._channelNameOverrides = JSON.parse(localStorage.getItem("lymow_channel_names") || "{}");

    // Draw new zone/channel state
    this._drawingZone = null; // "go" | "nogo" | "channel" | null
    this._drawPoly = null;    // array of {x,y} ENU points being drawn
    this._drawNameStep = false; // waiting for user to confirm name before saving
    this._pendingDrawPolygon = null; // polygon captured at save-draw click, held during name step
    this._pendingDrawType = null;

    // Split zone state
    this._splitMode = false;  // drawing a 2-point cut line across a go-zone
    this._splitPoly = null;   // [{x,y}, {x,y}] cut line points

    this._longPressTimer = null; // for zone enable/disable long-press
    this._pinAndGoMode = false; // double-click sends robot to point

    // Live mow trail — circular buffer of {x,y} ENU positions recorded during active mow.
    // Max 2000 points (~33 min at 1 Hz). Reset when mowing stops.
    this._mowTrail = [];
    this._mowTrailMaxPts = 2000;
    this._mowTrailActive = false; // true while workStatus is in mowing group

    // Pan/zoom state (in SVG user units)
    this._vx = 0; this._vy = 0; this._vw = 100; this._vh = 100;
    this._mapReady = false;

    // Map rotation (degrees, 0 = north up, clockwise positive)
    this._mapRotation = 0;

    // Pan gesture
    this._panning = false;
    this._panStart = null;
    this._panMoved = false;

    // Rotate gesture (right-click drag)
    this._rotating = false;
    this._rotateStart = null;

    // Pinch zoom
    this._pinchStart = null;

    this._bounds = null;
    this._scale = 1;
  }

  connectedCallback() {
    this._boundKeyDown = (e) => this._onKeyDown(e);
    window.addEventListener('keydown', this._boundKeyDown);
  }

  disconnectedCallback() {
    if (this._boundKeyDown) window.removeEventListener('keydown', this._boundKeyDown);
    this._closeCamera();
  }

  // Camera overlay — opt-in via `show_camera: true`. Hosted in document.body
  // (not the shadow root) so the map's _render() can't tear down the live
  // <video>/peer connection inside the embedded <lymow-camera-card>.
  _openCamera() {
    if (!this._cameraOverlay) {
      const ov = document.createElement("div");
      ov.style.cssText =
        "position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;";
      ov.addEventListener("click", (e) => { if (e.target === ov) this._closeCamera(); });
      const wrap = document.createElement("div");
      wrap.style.cssText = "width:min(92vw,720px);max-height:92vh;position:relative;";
      const close = document.createElement("button");
      close.textContent = "✕";
      close.title = "Close camera (Esc)";
      close.style.cssText =
        "position:absolute;top:-14px;right:-14px;z-index:1;width:36px;height:36px;border-radius:50%;border:0;background:#fff;color:#000;font:18px/1 sans-serif;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.4);";
      close.addEventListener("click", () => this._closeCamera());
      const card = document.createElement("lymow-camera-card");
      if (typeof card.setConfig !== "function") {
        // lymow-camera-card.js not loaded — fail soft.
        wrap.textContent = "Camera card not available (lymow-camera-card.js missing).";
      } else {
        try {
          card.setConfig({
            mower_entity: this._config.mower_entity,
            camera_entity: this._config.camera_entity,
            default_source: this._config.camera_default_source,
            title: "Lymow Camera",
          });
          this._cameraCard = card;
          wrap.appendChild(card);
        } catch (err) {
          wrap.textContent = `Camera unavailable: ${err.message || err}`;
        }
      }
      wrap.appendChild(close);
      ov.appendChild(wrap);
      this._cameraOverlay = ov;
    }
    if (this._cameraCard) this._cameraCard.hass = this._hass;
    document.body.appendChild(this._cameraOverlay);
  }

  _closeCamera() {
    if (this._cameraOverlay && this._cameraOverlay.parentNode) {
      // Removing from the DOM fires the camera card's disconnectedCallback,
      // which stops the cloud peer connection / clears the stream.
      this._cameraOverlay.parentNode.removeChild(this._cameraOverlay);
    }
  }

  _onKeyDown(e) {
    // Don't steal keys when an input is focused (check shadow DOM too)
    const active = this.shadowRoot?.activeElement || document.activeElement;
    const tag = active?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || active?.isContentEditable) return;
    switch (e.key) {
      case 'Escape':
        if (this._cameraOverlay?.parentNode) { this._closeCamera(); break; }
        if (this._expanded) { this._toggleExpand(); break; }
        if (this._splitMode) { this._cancelSplit(); break; }
        if (this._drawingZone || this._drawNameStep) { this._cancelDraw(); break; }
        if (this._editing) { this._cancelEdit(); break; }
        if (this._pinAndGoMode) { this._togglePinAndGo(); break; }
        if (this._settingsOpen || this._scheduleOpen || this._backupOpen) {
          this._settingsOpen = false; this._scheduleOpen = false; this._backupOpen = false; this._render();
        }
        break;
      case 'f': case 'F':
        if (!e.ctrlKey && !e.metaKey) { this._toggleExpand(); e.preventDefault(); }
        break;
      case 'e': case 'E':
        if (!e.ctrlKey && !e.metaKey && !this._editing && this._config?.mower_entity) {
          this._enterEdit(); e.preventDefault();
        }
        break;
      case 'r': case 'R':
        if (!e.ctrlKey && !e.metaKey) { this._resetView(); e.preventDefault(); }
        break;
    }
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
    if (this._cameraCard) this._cameraCard.hass = hass; // keep the camera overlay live

    // Record live robot position into the mow trail while actively mowing
    const mapState = hass?.states[this._config?.entity];
    if (mapState) {
      const a = mapState.attributes;
      const ws = a.workStatus !== undefined ? parseInt(a.workStatus) : -1;
      const MOWING = new Set([2, 8, 9]); // MOWING, RESUME, ZONE_PARTITION
      const isMowing = MOWING.has(ws);
      if (isMowing) {
        if (!this._mowTrailActive) {
          // New mow session started — clear the old trail
          this._mowTrail = [];
          this._mowTrailActive = true;
        }
        const x = a.poseEastM, y = a.poseNorthM;
        if (x !== undefined && y !== undefined) {
          const last = this._mowTrail[this._mowTrail.length - 1];
          // Only append if robot moved more than 0.05 m (skip GPS jitter while stationary)
          if (!last || Math.hypot(x - last.x, y - last.y) > 0.05) {
            this._mowTrail.push({ x, y });
            if (this._mowTrail.length > this._mowTrailMaxPts)
              this._mowTrail.shift();
          }
        }
      } else {
        this._mowTrailActive = false;
      }
    }

    // Don't re-render while the user is actively interacting with the UI:
    // - typing in a rename/draw-name input
    // - has focus inside any settings panel input or select (keeps dropdowns open)
    // - is dragging a slider (pointerdown on a range input)
    if (this._sliderActive) return;
    const active = this.shadowRoot?.activeElement;
    if (active && (active.tagName === 'INPUT' || active.tagName === 'SELECT')) return;
    this._render();
  }

  // ---------------------------------------------------------------------------
  // Data helpers
  // ---------------------------------------------------------------------------

  _getMapData() {
    const state = this._hass && this._hass.states[this._config.entity];
    if (!state) return null;
    const a = state.attributes;
    const goZones = (a.go_zones || []).map((z) => {
      const overrides = {};
      if (this._polyOverrides[z.hashId]) overrides.polygon = this._polyOverrides[z.hashId];
      if (this._nameOverrides[z.hashId] !== undefined) overrides.name = this._nameOverrides[z.hashId];
      return Object.keys(overrides).length ? { ...z, ...overrides } : z;
    });
    const nogoZones = (a.nogo_zones || []).map((z) => {
      const overrides = {};
      if (this._nogoOverrides[z.hashId]) overrides.polygon = this._nogoOverrides[z.hashId];
      if (this._nameOverrides[z.hashId] !== undefined) overrides.name = this._nameOverrides[z.hashId];
      return Object.keys(overrides).length ? { ...z, ...overrides } : z;
    });
    const channels = (a.channels || []).map((ch) => {
      const override = this._channelNameOverrides[ch.hashId];
      const poly = ch.polygon || [];
      let length = 0;
      for (let i = 1; i < poly.length; i++) {
        const dx = poly[i].x - poly[i - 1].x, dy = poly[i].y - poly[i - 1].y;
        length += Math.sqrt(dx * dx + dy * dy);
      }
      const base = override !== undefined ? { ...ch, name: override } : ch;
      return { ...base, length: Math.round(length) };
    });
    return {
      goZones,
      nogoZones,
      channels,
      gpsOrigin: a.gps_origin || null,
      chargingStation: a.charging_station || null,
      mowingSettings: a.mowing_settings || null,
      mowPath: a.mow_path || null,
      poseEastM: a.poseEastM,
      poseNorthM: a.poseNorthM,
      poseThetaRad: a.poseThetaRad,
      rtkEastM: a.rtkEastM,
      rtkNorthM: a.rtkNorthM,
      rtkStatus: a.rtkStatus,
      rtkLabel: a.rtkLabel || null,
      workStatus: a.workStatus,
      mowProgress: a.mowProgress ?? null,
      // Schedule data from optional schedule_entity
      schedules: (() => {
        const se = this._config.schedule_entity && this._hass?.states[this._config.schedule_entity];
        return se?.attributes?.schedules || null;
      })(),
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
    // Use the inner rotation group CTM so vertex drags are correct on a rotated map
    const rotG = svg.querySelector("g[transform*=rotate]");
    const ctm = rotG ? rotG.getScreenCTM() : svg.getScreenCTM();
    const u = pt.matrixTransform(ctm.inverse());
    return this._toEnu(u.x, u.y);
  }

  // ---------------------------------------------------------------------------
  // Zoom factor (initial viewport width / current viewport width)
  // ---------------------------------------------------------------------------

  _zoomFactor() {
    const TOTAL_W = (this._bounds.maxX - this._bounds.minX) * this._scale;
    return TOTAL_W / this._vw;
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

    const { goZones, nogoZones, channels, chargingStation, mowPath, poseEastM, poseNorthM, poseThetaRad, rtkEastM, rtkNorthM, rtkStatus, rtkLabel, workStatus, schedules } = mapData;

    // RTK auto-pause: if enabled, pause mowing when fix quality drops below threshold
    if (this._config.rtk_autopause && this._config.mower_entity) {
      this._checkRtkAutopause(rtkStatus, workStatus);
    }

    // Work-status label and mow progress from mower entity
    const mowerState = this._config.mower_entity && this._hass?.states[this._config.mower_entity];
    // mowProgress comes from the map sensor (mowProgress attr) or the mower entity
    const mowProgress = mapData.mowProgress ?? mowerState?.attributes?.mow_progress ?? null;
    const battery = mowerState?.attributes?.battery_level ?? null;

    if ([...goZones, ...nogoZones].length === 0 && !chargingStation) {
      this.shadowRoot.innerHTML = this._wrapMsg(`No map data yet. Call <em>lymow.query_map</em> or wait for the robot to connect.`);
      return;
    }

    const newBounds = this._computeBounds(mapData);
    if (!newBounds) { this.shadowRoot.innerHTML = this._wrapMsg("Empty map."); return; }

    // Reset view if zone count changed since we last fitted — handles the case
    // where the card first renders with only robot/channel data (no zones) and
    // later receives full map data with zones (much larger bounds).
    const zoneCount = goZones.length + nogoZones.length;
    if (this._mapReady && zoneCount !== (this._lastZoneCount || 0) && zoneCount > 0) {
      this._mapReady = false;
    }
    if (!this._mapReady) {
      this._bounds = newBounds;
      const W = newBounds.maxX - newBounds.minX;
      const H = newBounds.maxY - newBounds.minY;
      this._scale = 100 / W;
      this._vw = 100; this._vh = H * this._scale;
      this._vx = 0; this._vy = 0;
      this._mapReady = true;
      this._lastZoneCount = zoneCount;
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
    const nodeR = Math.max(1.2, TOTAL_W / 50).toFixed(2); // larger for touch friendliness

    // Zoom factor: >1 means zoomed in, <1 means zoomed out.
    // We use 1/zf as SVG scale for fixed-pixel markers so they appear constant size.
    const zf = this._zoomFactor();
    const invZf = (1 / zf).toFixed(6);

    // Update cached px-per-SVG-unit from the live SVG element (if already in DOM).
    // Fallback: assume SVG fills ~280px so 1 unit ≈ 2.8px at initial zoom.
    {
      const existingSvg = this.shadowRoot.querySelector("svg");
      if (existingSvg) {
        const r = existingSvg.getBoundingClientRect();
        if (r.width) this._pxPerUnit = r.width / this._vw;
      }
      if (!this._pxPerUnit) this._pxPerUnit = 2.8;
    }
    // Convert desired pixel size to SVG units inside scale(invZf) marker groups.
    // Rendered px = child_units × invZf × pxPerUnit, so:
    // child_units = desiredPx × zf / pxPerUnit  (the zf and invZf cancel at render time)
    const pu = this._pxPerUnit;
    const mPx = (px) => (px * zf / pu).toFixed(4);

    // ── Channels ─────────────────────────────────────────────────────────────
    const channelPaths = channels.map((ch) => {
      const pts = (ch.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const isDocking = ch.isDockingChannel;
      const selected = this._editing && this._editHash === ch.hashId && this._editType === "channel";
      const color = selected ? "#f57f17" : isDocking ? "#1565c0" : "#6a1b9a";
      const dash = isDocking ? "1,0.6" : "0.8,0.4";
      const cursor = this._editing ? " cursor:pointer" : "";
      return `<polyline data-hash="${ch.hashId}" data-type="channel" points="${pts}" fill="none" stroke="${color}" stroke-width="${selected ? "0.7" : "0.4"}" stroke-dasharray="${dash}" opacity="${selected ? 1 : 0.7}" style="${cursor}"/>`;
    }).join("\n");

    const channelLabels = this._chLabelMode === 3 ? "" : channels.map((ch) => {
      if (!ch.polygon || ch.polygon.length < 2) return "";
      const mid = ch.polygon[Math.floor(ch.polygon.length / 2)];
      const name = ch.name || (ch.isDockingChannel ? "Docking" : "Channel");
      const lenStr = `${ch.length ?? 0} m`;
      const m = this._chLabelMode;
      const line1 = m === 1 ? lenStr : name;
      const line2 = m === 2 ? lenStr : null;
      const fs = (parseFloat(fontSz) * 0.8).toFixed(2);
      const dy = line2 ? `-${(parseFloat(fs) * 0.6).toFixed(2)}` : "0";
      return `<text text-anchor="middle" fill="#6a1b9a" pointer-events="none" font-size="${fs}">
        <tspan x="${sx(mid.x)}" y="${sy(mid.y)}" dy="${dy}">${line1}</tspan>
        ${line2 ? `<tspan x="${sx(mid.x)}" dy="${(parseFloat(fs) * 1.2).toFixed(2)}">${line2}</tspan>` : ""}
      </text>`;
    }).join("\n");

    // ── Mow track overlay ────────────────────────────────────────────────────
    // During an active mow: show the live position trail (breadcrumb polyline).
    // After mowing ends: show the session's mowed-area overlay from QUERY_PATH.
    // QUERY_PATH returns the swept path as flat polyline segments. Approximate the
    // mowed area as the convex hull of all path points, clipped per zone — the hull
    // avoids the self-intersections of raw recording-order points. Shown whether or
    // not the card was open during the mow (path is server-polled every 30 s).
    const isMowingNow = this._mowTrailActive;
    const mowPts = [];
    for (const seg of (mowPath?.segments || [])) for (const p of seg) mowPts.push(p);
    const mowHull = mowPts.length >= 3 ? _convexHull(mowPts) : [];
    const hasMowData = mowHull.length >= 3;
    const mowHullPts = mowHull.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");

    // ── Live mow trail (breadcrumb during active mow) ─────────────────────────
    let liveTrail = "";
    if (isMowingNow && this._mowTrail.length >= 2) {
      const pts = this._mowTrail.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      // Bright vivid green trail — clearly visible against the zone fills
      liveTrail = `<polyline points="${pts}" fill="none" stroke="#00e676" stroke-width="0.9" stroke-linecap="round" stroke-linejoin="round" opacity="0.95" pointer-events="none"/>`;
    }

    // ── Persistent mow trail (server-side, from QUERY_PATH polls) ──────────────
    // Drawn whether or not the card was open during the mow: the coordinator polls
    // QUERY_PATH every 30 s, so the full swept path lives in mow_path.segments.
    let serverTrail = "";
    for (const seg of (mowPath?.segments || [])) {
      if (seg.length < 2) continue;
      const pts = seg.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      serverTrail += `<polyline points="${pts}" fill="none" stroke="#1b5e20" stroke-width="0.7" stroke-linecap="round" stroke-linejoin="round" opacity="0.85" pointer-events="none"/>`;
    }

    // ── Go-zones ──────────────────────────────────────────────────────────────
    const goPaths = goZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const selected = this._selectedZones.has(z.hashId);
      const beingEdited = this._editing && this._editHash === z.hashId;
      const enabled = z.isEnabled !== false;

      // Zone base fill is always medium green — the mowed overlay sits on top.
      const baseFill = beingEdited ? "#fff3e0"
        : selected ? "#1b5e20"
        : !enabled ? "#e0e0e0"
        : "#43a047";               // always medium green background
      const stroke = beingEdited ? "#ef6c00" : selected ? "#a5d6a7" : enabled ? "#2e7d32" : "#9e9e9e";
      const dash = enabled ? "" : `stroke-dasharray="2,1"`;

      // Mowed-area overlay: convex hull of the swept path, clipped to this zone
      // polygon, filled dark forest green. The hull covers the swept area without
      // the self-intersections of raw recording-order GPS fixes.
      let mowedOverlay = "";
      if (hasMowData && !beingEdited) {
        const clipId = `mow-clip-${z.hashId}`;
        mowedOverlay = `
          <defs><clipPath id="${clipId}"><polygon points="${pts}"/></clipPath></defs>
          <polygon points="${mowHullPts}" fill="#1b5e20" fill-opacity="0.72" stroke="none"
            clip-path="url(#${clipId})" pointer-events="none"/>`;
      }

      return `<polygon data-hash="${z.hashId}" data-type="go" points="${pts}"
        fill="${baseFill}" stroke="${stroke}" stroke-width="0.4" opacity="${enabled ? 1 : 0.6}" ${dash}
        style="cursor:pointer"/>${mowedOverlay}`;
    }).join("\n");

    // For each go-zone: clip label to polygon so it never renders outside the zone.
    const goLabelDefs = goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      return `<clipPath id="lbl-clip-${z.hashId}"><polygon points="${pts}"/></clipPath>`;
    }).join("\n");

    const goLabels = this._goLabelMode === 3 ? "" : goZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const {x: cx, y: cy} = this._polyLabelPoint(z.polygon);
      const m = this._goLabelMode;
      const namePart = z.name || z.hashId.slice(0, 6);
      const areaPart = z.area != null ? `${z.area} m²` : "";
      // Scale font to fit the zone: use shortest bbox dimension * 15%, capped at global fontSz.
      const xs = z.polygon.map(p => p.x), ys = z.polygon.map(p => p.y);
      const bboxW = (Math.max(...xs) - Math.min(...xs)) * sc;
      const bboxH = (Math.max(...ys) - Math.min(...ys)) * sc;
      const zoneFontSz = Math.max(0.8, Math.min(parseFloat(fontSz), Math.min(bboxW, bboxH) * 0.15)).toFixed(2);
      const clip = `clip-path="url(#lbl-clip-${z.hashId})"`;
      const textAttrs = `x="${sx(cx)}" text-anchor="middle" font-weight="bold" fill="white" pointer-events="none" font-size="${zoneFontSz}" ${clip}`;
      const lineH = (parseFloat(zoneFontSz) * 1.3).toFixed(2);

      // Per-zone strip count isn't carried by the flat QUERY_PATH path; overall
      // mow progress is shown in the card header instead.
      const progressPart = "";

      // Mode 2 (both): two stacked lines; modes 0/1 single line.
      if (m === 2 && areaPart) {
        const lines = [namePart, areaPart, progressPart].filter(Boolean);
        const startDy = -((lines.length - 1) * 0.5 * parseFloat(lineH)).toFixed(2);
        return `<text ${textAttrs} dominant-baseline="middle" y="${sy(cy)}">` +
          lines.map((l, i) => `<tspan x="${sx(cx)}" dy="${i === 0 ? startDy : lineH}">${l}</tspan>`).join("") +
          `</text>`;
      }
      // Modes 0/1: name or area + optional progress on second line
      const line1 = m === 0 ? namePart : m === 1 ? (areaPart || namePart) : namePart;
      if (progressPart) {
        return `<text ${textAttrs} dominant-baseline="middle" y="${sy(cy)}">` +
          `<tspan x="${sx(cx)}" dy="-${(parseFloat(lineH) * 0.5).toFixed(2)}">${line1}</tspan>` +
          `<tspan x="${sx(cx)}" dy="${lineH}" font-weight="normal" opacity="0.9">${progressPart}</tspan>` +
          `</text>`;
      }
      return `<text ${textAttrs} dominant-baseline="middle" y="${sy(cy)}">${line1}</text>`;
    }).join("\n");

    // ── No-go zones (on top of go-zones) ─────────────────────────────────────
    const nogoPaths = nogoZones.map((z) => {
      const pts = (z.polygon || []).map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const beingEdited = this._editing && this._editType === "nogo" && this._editHash === z.hashId;
      const stroke = beingEdited ? "#ef6c00" : "#c62828";
      const fill = beingEdited ? "#fff3e0" : "#ff5252";
      const fillOpacity = beingEdited ? "0.5" : "0.35";
      const cursor = this._editing ? "pointer" : "default";
      return `<polygon data-hash="${z.hashId}" data-type="nogo" points="${pts}"
        fill="${fill}" fill-opacity="${fillOpacity}" stroke="${stroke}" stroke-width="0.6" stroke-dasharray="1,0.5"
        style="cursor:${cursor}"/>`;
    }).join("\n");

    const nogoLabels = nogoZones.map((z) => {
      if (!z.polygon || z.polygon.length < 3) return "";
      const cx = z.polygon.reduce((s, p) => s + p.x, 0) / z.polygon.length;
      const cy = z.polygon.reduce((s, p) => s + p.y, 0) / z.polygon.length;
      const nm = this._nogoLabelMode;
      const namePart = z.name || "⛔";
      const areaPart = z.area != null ? `${z.area} m²` : "";
      const label = nm === 3 ? "⛔" : nm === 0 ? namePart : nm === 1 ? (areaPart || namePart) : (areaPart ? `${namePart} · ${areaPart}` : namePart);
      const nxs = z.polygon.map(p => p.x), nys = z.polygon.map(p => p.y);
      const nbboxW = (Math.max(...nxs) - Math.min(...nxs)) * sc;
      const nbboxH = (Math.max(...nys) - Math.min(...nys)) * sc;
      const nogoFontSz = Math.max(0.5, Math.min(parseFloat(fontSz) * 0.9, Math.min(nbboxW, nbboxH) * 0.3)).toFixed(2);
      return `<text x="${sx(cx)}" y="${sy(cy)}" text-anchor="middle" dominant-baseline="middle"
        font-size="${nogoFontSz}" fill="#c62828" pointer-events="none">${label}</text>`;
    }).join("\n");

    // ── Edit handles ──────────────────────────────────────────────────────────
    // ── Draw-new-zone overlay ─────────────────────────────────────────────────
    let drawOverlay = "";
    if (this._drawingZone && this._drawPoly && this._drawPoly.length > 0) {
      const dp = this._drawPoly;
      const pts = dp.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ");
      const isChannel = this._drawingZone === "channel";
      const drawColor = this._drawingZone === "nogo" ? "#ff5252" : isChannel ? "#1565c0" : "#66bb6a";
      const polyEl = isChannel
        ? `<polyline points="${pts}" fill="none" stroke="${drawColor}" stroke-width="0.5" stroke-dasharray="3,1.5" pointer-events="none"/>`
        : dp.length >= 3
          ? `<polygon points="${pts}" fill="${drawColor}33" stroke="${drawColor}" stroke-width="0.5" stroke-dasharray="2,1" pointer-events="none"/>`
          : `<polyline points="${pts}" fill="none" stroke="${drawColor}" stroke-width="0.5" stroke-dasharray="2,1" pointer-events="none"/>`;
      const dots = dp.map((p, i) => {
        const r = i === 0 && !isChannel ? (parseFloat(nodeR) * 1.4).toFixed(2) : nodeR;
        const fill = i === 0 && !isChannel ? drawColor : "white";
        return `<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="${r}" fill="${fill}" stroke="${drawColor}" stroke-width="0.4" pointer-events="none"/>`;
      }).join("");
      drawOverlay = polyEl + dots;
    }

    // Split-line overlay: show placed point(s) during split mode
    let splitOverlay = "";
    if (this._splitMode && this._splitPoly && this._splitPoly.length > 0) {
      const [p1, p2] = this._splitPoly;
      splitOverlay = `<circle cx="${sx(p1.x)}" cy="${sy(p1.y)}" r="${nodeR}" fill="#7b1fa2" stroke="white" stroke-width="0.4" pointer-events="none"/>`;
      if (p2) splitOverlay += `<line x1="${sx(p1.x)}" y1="${sy(p1.y)}" x2="${sx(p2.x)}" y2="${sy(p2.y)}" stroke="#7b1fa2" stroke-width="0.6" stroke-dasharray="2,1" pointer-events="none"/>
        <circle cx="${sx(p2.x)}" cy="${sy(p2.y)}" r="${nodeR}" fill="#7b1fa2" stroke="white" stroke-width="0.4" pointer-events="none"/>`;
    }

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

    // ── Charging station (fixed pixel size via inverse-zoom scale) ────────────
    // Each marker is translated to its map position, then scaled by 1/zf so the
    // rendered pixel size stays constant regardless of zoom level.
    let csHtml = "";
    if (chargingStation) {
      const cx = sx(chargingStation.x), cy = sy(chargingStation.y);
      const csDrag = this._editing && !this._editHash;
      csHtml = `
        <g data-marker="cs" data-enu-x="${chargingStation.x}" data-enu-y="${chargingStation.y}" transform="translate(${cx},${cy}) scale(${invZf})" pointer-events="${csDrag ? "all" : "none"}" style="${csDrag ? "cursor:move" : ""}">
          <circle r="${mPx(12)}" fill="#1565c0" opacity="0.9"/>
          <circle r="${mPx(7)}" fill="white"/>
          <text text-anchor="middle" dominant-baseline="middle" font-size="${mPx(10)}" fill="#1565c0" font-weight="bold">⚡</text>
          ${csDrag ? `<circle r="${mPx(12)}" fill="none" stroke="#90caf9" stroke-width="${mPx(2)}" stroke-dasharray="${mPx(4)} ${mPx(2)}"/>` : ""}
        </g>`;
    }

    // ── Robot position (fixed pixel size) ────────────────────────────────────
    let robotHtml = "";
    if (poseEastM !== undefined && poseNorthM !== undefined) {
      const rx = sx(poseEastM), ry = sy(poseNorthM);
      const theta = poseThetaRad || 0;
      const headLen = mPx(20);
      const arrowX = (Math.cos(theta) * headLen).toFixed(3);
      const arrowY = (-Math.sin(theta) * headLen).toFixed(3);
      robotHtml = `
        <g data-marker="robot" data-cx="${rx}" data-cy="${ry}" transform="translate(${rx},${ry}) scale(${invZf})" pointer-events="none">
          <circle r="${mPx(11)}" fill="#e65100" stroke="white" stroke-width="${mPx(2.5)}"/>
          <line x1="0" y1="0" x2="${arrowX}" y2="${arrowY}" stroke="#e65100" stroke-width="${mPx(4)}" stroke-linecap="round"/>
        </g>`;
    }

    // ── RTK base station (fixed pixel size) ───────────────────────────────────
    let rtkHtml = "";
    if (rtkEastM !== undefined && rtkNorthM !== undefined) {
      const rx = sx(rtkEastM), ry = sy(rtkNorthM);
      // Triangle: tip up, base down; centered at (0,0)
      rtkHtml = `
        <g data-marker="rtk" data-cx="${rx}" data-cy="${ry}" transform="translate(${rx},${ry}) scale(${invZf})" pointer-events="none">
          <polygon points="0,${-mPx(14)} ${-mPx(12)},${mPx(9)} ${mPx(12)},${mPx(9)}" fill="#7b1fa2" stroke="white" stroke-width="${mPx(2)}" opacity="0.9"/>
          <text y="${mPx(22)}" text-anchor="middle" font-size="${mPx(10)}" fill="#7b1fa2">RTK</text>
        </g>`;
    }

    // ── Status bar ────────────────────────────────────────────────────────────
    const _wsLabels = { 0:"Idle", 1:"Waiting", 2:"Mowing", 3:"Paused", 4:"Docking",
      5:"Charging", 6:"Remote", 7:"Error", 8:"Resuming", 9:"Mowing", 10:"Paused",
      11:"Updating", 12:"Charged", 13:"E-Stop", 14:"Escaping", 15:"Testing" };
    const _wsColors = { 2:"#2e7d32", 8:"#2e7d32", 9:"#2e7d32", 3:"#ef6c00",
      10:"#ef6c00", 4:"#1565c0", 5:"#1565c0", 12:"#1565c0", 7:"#c62828", 13:"#c62828" };
    const wsNum = workStatus !== undefined ? parseInt(workStatus) : null;
    const wsLabel = wsNum !== null ? (_wsLabels[wsNum] ?? `Status ${wsNum}`) : null;
    const wsColor = wsNum !== null ? (_wsColors[wsNum] ?? "#757575") : null;

    const rtkNum = rtkStatus !== undefined ? parseInt(rtkStatus) : null;
    const rtkColors = { 0:"#c62828", 1:"#ef6c00", 2:"#2e7d32", 3:"#1565c0" };

    const statusBar = (wsLabel || battery !== null || rtkNum !== null) ? `
      <div class="status-bar">
        ${wsLabel ? `<span class="status-chip" style="background:${wsColor}">${wsLabel}</span>` : ""}
        ${mowProgress !== null ? `<span class="status-chip" style="background:#455a64">🌿 ${Math.round(mowProgress)}%</span>` : ""}
        ${battery !== null ? `<span class="status-chip" style="background:#455a64">🔋 ${Math.round(battery)}%</span>` : ""}
        ${rtkNum !== null ? `<span class="status-chip" style="background:${rtkColors[rtkNum] ?? '#757575'}" title="${this._config.rtk_autopause ? 'auto-pause on' : ''}">📡 ${rtkLabel ?? 'RTK'}</span>` : ""}
      </div>` : "";

    // ── Toolbar ───────────────────────────────────────────────────────────────
    let toolbar;
    if (this._editing) {
      let editMsg, editActions;
      if (this._editRename && this._editHash) {
        const isChannel = this._editType === "channel";
        const obj = isChannel
          ? channels.find(c => c.hashId === this._editHash)
          : goZones.find(z => z.hashId === this._editHash) || nogoZones.find(z => z.hashId === this._editHash);
        const currentName = obj?.name || "";
        const label = isChannel ? "channel" : "zone";
        editMsg = `Rename ${label}:`;
        editActions = `
          <input class="rename-input" id="rename-input" type="text" value="${currentName}" placeholder="${isChannel ? "Channel" : "Zone"} name" maxlength="40"/>
          <button class="btn save" data-action="save-rename">✓ OK</button>
          <button class="btn cancel" data-action="cancel-rename">✕</button>`;
      } else if (this._drawNameStep) {
        const typeLabel = this._pendingDrawType === "nogo" ? "no-go zone" : this._pendingDrawType === "channel" ? "channel" : "go-zone";
        editMsg = `Name your new ${typeLabel} (optional):`;
        editActions = `
          <input class="rename-input" id="draw-name-input" type="text" placeholder="e.g. Front lawn" maxlength="40" autofocus/>
          <button class="btn save" data-action="confirm-draw">✓ Save</button>
          <button class="btn cancel" data-action="cancel-draw">✕</button>`;
      } else if (this._splitMode) {
        const pts = this._splitPoly?.length ?? 0;
        editMsg = pts < 1 ? "Split: click first cut point on the map" : "Split: click second cut point to cut the zone";
        editActions = `<button class="btn cancel" data-action="cancel-split">✕ Cancel</button>`;
      } else if (this._drawingZone) {
        const drawPts = this._drawPoly?.length ?? 0;
        const isChannel = this._drawingZone === "channel";
        const minPts = isChannel ? 2 : 3;
        const hint = isChannel
          ? `Drawing channel — click to add points (${drawPts} so far). Press Save when done.`
          : `Drawing ${this._drawingZone} zone — click to add points (${drawPts} so far). Click first point to close.`;
        editMsg = hint;
        editActions = `
          ${drawPts >= minPts ? `<button class="btn save" data-action="save-draw">💾 Save</button>` : ""}
          <button class="btn cancel" data-action="cancel-draw">✕ Cancel</button>`;
      } else {
        const isChannel = this._editType === "channel";
        const msg = this._editHash
          ? isChannel
            ? `Channel selected — rename or delete`
            : `Editing ${this._editType === "nogo" ? "no-go" : "go"} zone — drag handles · + insert · ✕ delete`
          : `Tap a zone or channel to select · or draw below`;
        editMsg = msg;
        editActions = `
          ${this._editHash && !isChannel ? `<button class="btn save" data-action="save-edit">💾 Save</button>` : ""}
          ${this._editHash ? `<button class="btn rename" data-action="enter-rename">🏷 Rename</button>` : ""}
          ${this._editHash && this._editType === "go" ? `<button class="btn pin" disabled style="background:#6a1b9a;opacity:.4;cursor:not-allowed" title="Split isn't supported by the robot firmware yet (app gates it 'coming soon') — see GitHub issue #220">✂ Split</button>` : ""}
          ${this._editHash ? `<button class="btn cancel" style="background:#b71c1c" data-action="delete-zone" title="Delete permanently">🗑 Delete</button>` : ""}
          ${!this._editHash ? `<button class="btn pin" disabled style="opacity:.4;cursor:not-allowed" title="Adding a go-zone isn't supported over WiFi — the robot creates zones by driving (see GitHub issue #220)">＋ Go-zone</button>` : ""}
          ${!this._editHash ? `<button class="btn cancel" disabled style="opacity:.4;cursor:not-allowed" title="Adding a no-go zone needs BLE drive-record — not available over WiFi (see GitHub issue #220)">＋ No-go</button>` : ""}
          ${!this._editHash ? `<button class="btn pin" disabled style="background:#1565c0;opacity:.4;cursor:not-allowed" title="Adding a channel needs BLE drive-record — not available over WiFi (see GitHub issue #220)">＋ Channel</button>` : ""}
          <button class="btn cancel" data-action="cancel-edit">✕ Cancel</button>`;
      }
      // Per-zone cut-height row — only when a go-zone is selected and not in
      // rename / draw / split sub-modes (those replace editActions above).
      let extraRow = "";
      if (
        this._editHash &&
        this._editType === "go" &&
        !this._editRename &&
        !this._drawNameStep &&
        !this._splitMode &&
        !this._drawingZone
      ) {
        const z = goZones.find(g => g.hashId === this._editHash);
        const zc = z?.zoneConfig || {};
        const chV   = z?.cutHeight   ?? zc.cutHeight   ?? 40;
        const msV   = zc.moveSpeed   ?? 0.6;
        const psV   = z?.pathSpacing ?? zc.pathSpacing ?? 25;
        const plV   = zc.perimeterMowLaps ?? 1;
        const smV   = zc.safeMarginMode   != null ? (zc.safeMarginMode ? 1 : 0) : 1;
        const omV   = zc.turnOffOuterMotor ? 1 : 0;
        const selStyle = "width:100%;padding:2px 4px;font-size:0.9em;background:var(--input-fill-color,#2a2a2e);border:1px solid var(--divider-color,#444);border-radius:4px;color:inherit";
        extraRow = `
          <div style="margin-top:6px;padding:6px 8px;background:var(--card-background-color,#1c1c1e);border-radius:8px;border:1px solid var(--divider-color,#333)">
            <div style="font-size:0.75em;font-weight:600;letter-spacing:0.05em;color:var(--secondary-text-color);margin-bottom:6px">ZONE SETTINGS</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;align-items:center;font-size:0.82em">
              <span style="color:var(--secondary-text-color)">Cut height (mm)</span>
              <input id="zs-cut-height" type="number" min="20" max="100" step="5" value="${chV}"
                     style="width:64px;padding:2px 4px;font-size:0.9em;background:var(--input-fill-color,#2a2a2e);border:1px solid var(--divider-color,#444);border-radius:4px;color:inherit" />
              <span style="color:var(--secondary-text-color)">Move speed (m/s)</span>
              <input id="zs-move-speed" type="number" min="0.1" max="1.5" step="0.05" value="${msV.toFixed(2)}"
                     style="width:64px;padding:2px 4px;font-size:0.9em;background:var(--input-fill-color,#2a2a2e);border:1px solid var(--divider-color,#444);border-radius:4px;color:inherit" />
              <span style="color:var(--secondary-text-color)">Path spacing (cm)</span>
              <input id="zs-path-spacing" type="number" min="0" max="100" step="1" value="${psV}"
                     style="width:64px;padding:2px 4px;font-size:0.9em;background:var(--input-fill-color,#2a2a2e);border:1px solid var(--divider-color,#444);border-radius:4px;color:inherit" />
              <span style="color:var(--secondary-text-color)">Perimeter laps</span>
              <input id="zs-perimeter-laps" type="number" min="0" max="5" step="1" value="${plV}"
                     style="width:64px;padding:2px 4px;font-size:0.9em;background:var(--input-fill-color,#2a2a2e);border:1px solid var(--divider-color,#444);border-radius:4px;color:inherit" />
              <span style="color:var(--secondary-text-color)">Safe margin</span>
              <select id="zs-safe-margin" style="${selStyle}">
                <option value="1" ${smV === 1 ? "selected" : ""}>Offset edge</option>
                <option value="0" ${smV === 0 ? "selected" : ""}>Precise edge</option>
              </select>
              <span style="color:var(--secondary-text-color)">Outer motor</span>
              <select id="zs-outer-motor" style="${selStyle}">
                <option value="0" ${omV === 0 ? "selected" : ""}>On</option>
                <option value="1" ${omV === 1 ? "selected" : ""}>Off</option>
              </select>
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-top:6px">
              <button class="btn save" data-action="apply-zone-config" style="flex:1">✓ Apply zone settings</button>
              <span class="zone-ch-status" style="font-size:0.78em;color:var(--secondary-text-color)"></span>
            </div>
          </div>`;
      }
      toolbar = `
        <div class="edit-bar">${editMsg}</div>
        <div class="btn-row">${editActions}</div>
        ${extraRow}`;
    } else {
      const hasSel = this._selectedZones.size > 0;
      const canMow = hasSel && !!this._config.mower_entity;
      const canMerge = this._selectedZones.size >= 2 && !!this._config.mower_entity;
      const mowBtn = hasSel
        ? `<button class="btn mow" ${canMow ? "" : "disabled"} data-action="mow">🌿 Mow (${this._selectedZones.size})</button>`
        : "";
      const mergeBtn = canMerge
        ? `<button class="btn pin" style="background:#6a1b9a" data-action="merge" title="Merge selected zones into one">⊕ Merge</button>`
        : "";
      const editBtn = this._config.mower_entity
        ? `<button class="btn edit" data-action="edit" title="Edit zones [E]">✏️ Edit</button>` : "";
      const pinBtn = this._config.mower_entity
        ? `<button class="btn pin" disabled style="opacity:.4;cursor:not-allowed" title="Pin-and-go needs to create a temporary zone, which isn't supported over WiFi (see GitHub issue #220)">📍</button>` : "";
      const schedBtn = this._config.schedule_entity
        ? `<button class="btn sched${this._scheduleOpen ? " settings-active" : ""}" data-action="sched" title="Mowing schedules">📅</button>` : "";
      const backupBtn = this._config.mower_entity
        ? `<button class="btn backup${this._backupOpen ? " settings-active" : ""}" data-action="backup" title="Map backups">📦</button>` : "";
      const settingsBtn = this._config.mower_entity
        ? `<button class="btn settings${this._settingsOpen ? " settings-active" : ""}" data-action="settings" title="Mowing settings">⚙</button>` : "";
      const cameraBtn = this._config.show_camera
        ? `<button class="btn camera" data-action="camera" title="Onboard camera (LAN / Cloud)">📹</button>` : "";
      const expandBtn = `<button class="btn expand" data-action="expand" title="${this._expanded ? "Collapse [F]" : "Expand [F]"}">${this._expanded ? "⊠" : "⊞"}</button>`;
      const resetBtn = `<button class="btn reset" data-action="reset" title="Reset zoom [R]">⊡</button>`;
      toolbar = `<div class="btn-row">${mowBtn}${mergeBtn}${editBtn}${pinBtn}${schedBtn}${backupBtn}${settingsBtn}${cameraBtn}${expandBtn}${resetBtn}</div>`;
    }

    // ── Legend with matching SVG symbols ─────────────────────────────────────
    const _li = (svgInner, vb, label) =>
      `<div class="legend-item"><span class="lsym"><svg viewBox="${vb}" xmlns="http://www.w3.org/2000/svg">${svgInner}</svg></span>${label}</div>`;
    const legendItems = [
      isMowingNow
        ? _li(`<rect x="1" y="1" width="14" height="10" fill="#43a047" stroke="#2e7d32" stroke-width="1.5" rx="1"/>`, "0 0 16 12", "Go zone")
        : hasMowData
          ? _li(`<rect x="1" y="1" width="7" height="10" fill="#43a047" stroke="#2e7d32" stroke-width="1.5" rx="1"/><rect x="8" y="1" width="7" height="10" fill="#a5d6a7" stroke="#2e7d32" stroke-width="1.5" rx="1"/>`, "0 0 16 12", "Mowed / Left")
          : _li(`<rect x="1" y="1" width="14" height="10" fill="#43a047" stroke="#2e7d32" stroke-width="1.5" rx="1"/>`, "0 0 16 12", "Go zone"),
      isMowingNow && this._mowTrail.length >= 2
        ? _li(`<polyline points="1,11 6,7 11,4 19,2" fill="none" stroke="#00e676" stroke-width="2" stroke-linecap="round"/>`, "0 0 20 12", "Mow trail")
        : "",
      nogoZones.length ? _li(`<rect x="1" y="1" width="14" height="10" fill="#ff5252" fill-opacity="0.35" stroke="#c62828" stroke-width="1.5" rx="1" stroke-dasharray="3,2"/>`, "0 0 16 12", "No-go") : "",
      chargingStation ? _li(`<circle cx="8" cy="7" r="6" fill="#1565c0" opacity="0.9"/><circle cx="8" cy="7" r="3.5" fill="white"/><text x="8" y="8.5" text-anchor="middle" dominant-baseline="middle" font-size="5.5" fill="#1565c0" font-weight="bold">⚡</text>`, "0 0 16 14", "Station") : "",
      poseEastM !== undefined ? _li(`<circle cx="7" cy="8" r="5" fill="#e65100" stroke="white" stroke-width="1"/><line x1="7" y1="8" x2="16" y2="3" stroke="#e65100" stroke-width="1.5" stroke-linecap="round"/>`, "0 0 18 14", "Robot") : "",
      rtkEastM !== undefined ? _li(`<polygon points="8,1 2,13 14,13" fill="#7b1fa2" stroke="white" stroke-width="1"/>`, "0 0 16 14", "RTK") : "",
      channels.length ? _li(`<line x1="1" y1="6" x2="19" y2="6" stroke="#1565c0" stroke-width="2" stroke-dasharray="4,2"/>`, "0 0 20 12", "Channel") : "",
    ].filter(Boolean).join("");

    // ── Settings panel (hidden during edit mode) ──────────────────────────────
    const sv = this._settingsValues || {};
    const settingsPanel = (this._settingsOpen && !this._editing) ? `
      <div class="settings-panel">
        <div class="sp-title">Mowing settings</div>
        <div class="sp-row">
          <label>Speed (m/s)</label>
          <input type="range" class="sp-input" data-field="move_speed" data-type="float"
            min="0.1" max="1.0" step="0.1" value="${sv.move_speed ?? 0.6}"
            oninput="this.nextElementSibling.textContent=parseFloat(this.value).toFixed(1)"/>
          <span class="sp-val">${(sv.move_speed ?? 0.6).toFixed(1)}</span>
        </div>
        <div class="sp-row">
          <label>Cut speed</label>
          <input type="range" class="sp-input" data-field="cut_speed" data-type="int"
            min="1" max="10" step="1" value="${sv.cut_speed ?? 5}"
            oninput="this.nextElementSibling.textContent=parseInt(this.value)"/>
          <span class="sp-val">${sv.cut_speed ?? 5}</span>
        </div>
        <div class="sp-row">
          <label>Path spacing (mm)</label>
          <input type="range" class="sp-input" data-field="path_spacing" data-type="int"
            min="50" max="350" step="10" value="${sv.path_spacing ?? 90}"
            oninput="this.nextElementSibling.textContent=this.value"/>
          <span class="sp-val">${sv.path_spacing ?? 90}</span>
        </div>
        <div class="sp-row">
          <label>Perimeter laps</label>
          <input type="range" class="sp-input" data-field="perimeter_mow_laps" data-type="int"
            min="0" max="5" step="1" value="${sv.perimeter_mow_laps ?? 1}"
            oninput="this.nextElementSibling.textContent=this.value"/>
          <span class="sp-val">${sv.perimeter_mow_laps ?? 1}</span>
        </div>
        <div class="sp-row">
          <label>No-go laps</label>
          <input type="range" class="sp-input" data-field="nogo_mow_laps" data-type="int"
            min="0" max="5" step="1" value="${sv.nogo_mow_laps ?? 1}"
            oninput="this.nextElementSibling.textContent=this.value"/>
          <span class="sp-val">${sv.nogo_mow_laps ?? 1}</span>
        </div>
        <div class="sp-row">
          <label>Cut direction</label>
          <select class="sp-input sp-select" data-field="perimeter_mow_dir" data-type="int">
            <option value="0" ${(sv.perimeter_mow_dir ?? 0) === 0 ? "selected" : ""}>Clockwise</option>
            <option value="1" ${(sv.perimeter_mow_dir ?? 0) === 1 ? "selected" : ""}>Counter-clockwise</option>
            <option value="2" ${(sv.perimeter_mow_dir ?? 0) === 2 ? "selected" : ""}>Random</option>
          </select>
          <span class="sp-val"></span>
        </div>
        <div class="sp-row">
          <label>Obstacle avoidance</label>
          <select class="sp-input sp-select" data-field="obs_dec_mode" data-type="int">
            <option value="0" ${(sv.obs_dec_mode ?? 0) === 0 ? "selected" : ""}>Off</option>
            <option value="1" ${(sv.obs_dec_mode ?? 0) === 1 ? "selected" : ""}>Slow down</option>
            <option value="2" ${(sv.obs_dec_mode ?? 0) === 2 ? "selected" : ""}>Detour</option>
          </select>
          <span class="sp-val"></span>
        </div>
        <details class="sp-advanced"${this._advancedOpen ? " open" : ""}>
          <summary>Advanced</summary>
          <div class="sp-row">
            <label>Mowing pattern</label>
            <select class="sp-input sp-select" data-field="clean_mode" data-type="int">
              <option value="0" ${(sv.clean_mode ?? 0) === 0 ? "selected" : ""}>Parallel</option>
              <option value="1" ${(sv.clean_mode ?? 0) === 1 ? "selected" : ""}>Spiral</option>
              <option value="2" ${(sv.clean_mode ?? 0) === 2 ? "selected" : ""}>Random</option>
            </select>
            <span class="sp-val"></span>
          </div>
          <div class="sp-row">
            <label>Path order</label>
            <select class="sp-input sp-select" data-field="path_order" data-type="int">
              <option value="0" ${!(sv.path_order) ? "selected" : ""}>Normal</option>
              <option value="1" ${sv.path_order ? "selected" : ""}>Reverse</option>
            </select>
            <span class="sp-val"></span>
          </div>
          <div class="sp-row">
            <label>Safe margin</label>
            <select class="sp-input sp-select" data-field="safe_margin_mode" data-type="int">
              <option value="1" ${(sv.safe_margin_mode ?? 1) === 1 ? "selected" : ""}>Offset edge</option>
              <option value="0" ${(sv.safe_margin_mode ?? 1) === 0 ? "selected" : ""}>Precise edge</option>
            </select>
            <span class="sp-val"></span>
          </div>
          <div class="sp-row">
            <label>Outer motor</label>
            <select class="sp-input sp-select" data-field="turn_off_outer_motor" data-type="int">
              <option value="0" ${!(sv.turn_off_outer_motor) ? "selected" : ""}>On</option>
              <option value="1" ${sv.turn_off_outer_motor ? "selected" : ""}>Off</option>
            </select>
            <span class="sp-val"></span>
          </div>
          <div class="sp-row" style="margin-top:6px;border-top:1px solid var(--divider-color,#444);padding-top:6px">
            <label>Go-zone labels</label>
            <select class="sp-input sp-select" data-field="go_label_mode" data-type="int">
              <option value="0" ${this._goLabelMode === 0 ? "selected" : ""}>Name</option>
              <option value="1" ${this._goLabelMode === 1 ? "selected" : ""}>Area</option>
              <option value="2" ${this._goLabelMode === 2 ? "selected" : ""}>Both</option>
              <option value="3" ${this._goLabelMode === 3 ? "selected" : ""}>None</option>
            </select>
            <span class="sp-val"></span>
          </div>
          <div class="sp-row">
            <label>No-go labels</label>
            <select class="sp-input sp-select" data-field="nogo_label_mode" data-type="int">
              <option value="0" ${this._nogoLabelMode === 0 ? "selected" : ""}>Name</option>
              <option value="1" ${this._nogoLabelMode === 1 ? "selected" : ""}>Area</option>
              <option value="2" ${this._nogoLabelMode === 2 ? "selected" : ""}>Both</option>
              <option value="3" ${this._nogoLabelMode === 3 ? "selected" : ""}>None</option>
            </select>
            <span class="sp-val"></span>
          </div>
          <div class="sp-row">
            <label>Channel labels</label>
            <select class="sp-input sp-select" data-field="ch_label_mode" data-type="int">
              <option value="0" ${this._chLabelMode === 0 ? "selected" : ""}>Name</option>
              <option value="1" ${this._chLabelMode === 1 ? "selected" : ""}>Length (m)</option>
              <option value="2" ${this._chLabelMode === 2 ? "selected" : ""}>Name + Length</option>
              <option value="3" ${this._chLabelMode === 3 ? "selected" : ""}>None</option>
            </select>
            <span class="sp-val"></span>
          </div>
        </details>
        <div class="sp-row" style="margin-top:4px">
          <label>Cut height</label>
          <div style="display:flex;gap:6px">
            <button class="sp-cut-btn" data-action="cut-height-raise">▲ Raise</button>
            <button class="sp-cut-btn" data-action="cut-height-lower">▼ Lower</button>
          </div>
          <span class="sp-val"></span>
        </div>
        <button class="sp-apply" data-action="apply-settings">Apply settings</button>
        <div class="sp-status"></div>
      </div>` : "";

    // ── Schedule panel ────────────────────────────────────────────────────────
    const schedulePanel = (this._scheduleOpen && schedules && !this._editing) ? (() => {
      const dayNames = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
      const rows = schedules.map((s) => {
        const days = (s.days || []).map(d => dayNames[d] ?? d).join(", ");
        const h = String(s.hour ?? 0).padStart(2, "0");
        const m = String(s.minute ?? 0).padStart(2, "0");
        const disabled = s.isDisabled ? " (off)" : "";
        const zones = s.zoneHashIds?.length ? ` · ${s.zoneHashIds.length} zone(s)` : " · all zones";
        return `<div class="sched-row${s.isDisabled ? " sched-disabled" : ""}">
          <span class="sched-time">${h}:${m}</span>
          <span class="sched-days">${days}${zones}</span>
          <span class="sched-status">${disabled || (s.repeat ? "↻" : "1×")}</span>
        </div>`;
      }).join("");
      return `<div class="settings-panel">
        <div class="sp-title">Mowing schedules (${schedules.length})</div>
        ${rows || "<div style='font-size:0.8em;color:var(--secondary-text-color)'>No schedules</div>"}
      </div>`;
    })() : "";

    // ── Backup panel ──────────────────────────────────────────────────────────
    const backupSensorState = this._config.backup_entity
      ? this._hass?.states[this._config.backup_entity]
      : (() => {
          // Auto-detect: find the first backup_maps sensor for this mower's device
          if (!this._hass) return null;
          const mid = this._config.mower_entity;
          const devId = mid ? Object.values(this._hass.entities || {}).find(e => e.entity_id === mid)?.device_id : null;
          if (!devId) return null;
          const sensorEid = Object.values(this._hass.entities || {}).find(
            e => e.device_id === devId && e.entity_id?.includes("backup_maps")
          )?.entity_id;
          return sensorEid ? this._hass.states[sensorEid] : null;
        })();
    const backupList = backupSensorState?.attributes?.backups || [];
    const backupPanel = (this._backupOpen && !this._editing) ? (() => {
      const fmtTs = (ts) => {
        if (!ts) return "";
        try { return new Date(parseInt(ts) * 1000).toLocaleString(); } catch { return ""; }
      };
      const rows = backupList.map((b, i) => {
        const label = b.name || fmtTs(b.backupTime) || `Backup ${i + 1}`;
        const isRenaming = this._backupRenaming === b.file;
        return `<div class="backup-row" data-file="${b.file || ""}">
          ${isRenaming
            ? `<input class="backup-rename-input" id="backup-rename-${i}" type="text" value="${label}"
                 style="flex:1;padding:2px 5px;background:var(--input-fill-color,#2a2a2e);border:1px solid var(--divider-color,#444);border-radius:4px;color:inherit;font-size:0.85em"/>
               <button class="backup-action-btn" data-backup-action="rename-ok" data-file="${b.file || ""}" data-idx="${i}" title="Save">✓</button>
               <button class="backup-action-btn" data-backup-action="rename-cancel" title="Cancel">✕</button>`
            : `<span class="backup-name" title="${b.file || ""}">${label}</span>
               <span class="backup-date">${fmtTs(b.backupTime)}</span>
               <button class="backup-action-btn" data-backup-action="restore" data-file="${b.file || ""}" title="Restore this backup">⟳</button>
               <button class="backup-action-btn" data-backup-action="rename-start" data-file="${b.file || ""}" title="Rename">✏</button>
               <button class="backup-action-btn backup-delete" data-backup-action="delete" data-file="${b.file || ""}" title="Delete">🗑</button>`}
        </div>`;
      }).join("");
      return `<div class="settings-panel">
        <div class="sp-title">Map backups (${backupList.length})</div>
        <button class="sp-apply backup-create-btn" data-action="backup-create">+ Create backup</button>
        <div class="backup-status"></div>
        ${rows || "<div style='font-size:0.8em;color:var(--secondary-text-color);margin-top:4px'>No backups yet</div>"}
      </div>`;
    })() : "";

    const title = this._config.title ?? "Lymow Map";

    // Aspect ratio for the map area
    const mapAspect = (TOTAL_W / TOTAL_H).toFixed(4);

    // Preserve scroll position and <details> state across full DOM replace.
    const prevAdvanced = this.shadowRoot.querySelector(".sp-advanced");
    if (prevAdvanced) this._advancedOpen = prevAdvanced.open;
    const prevPanel = this.shadowRoot.querySelector(".settings-panel");
    const prevScrollTop = prevPanel?.scrollTop ?? 0;
    // Page-level scroll jumps when the card's height changes briefly during innerHTML swap.
    const savedScrollY = window.scrollY;

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
        svg.rotating { cursor: ew-resize; }
        svg.pin-mode { cursor: crosshair; }
        /* Fixed-pixel overlays sit on top of the SVG in pixel space */
        .map-overlay { position: absolute; inset: 0; pointer-events: none; overflow: hidden; border-radius: 6px; }
        .north-arrow { position: absolute; top: 8px; right: 8px; width: ${_NORTH_PX}px; height: ${_NORTH_PX}px; }
        .scale-bar-wrap { position: absolute; bottom: 8px; left: 8px; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
        .scale-bar { height: 4px; background: #555; opacity: 0.85; border-left: 2px solid #555; border-right: 2px solid #555; min-width: 20px; }
        .scale-bar-label { font-size: 10px; color: #333; background: rgba(255,255,255,0.7); padding: 0 2px; border-radius: 2px; white-space: nowrap; }
        .status-bar { display: flex; gap: 5px; flex-wrap: wrap; margin-top: 6px; flex-shrink: 0; }
        .status-chip { padding: 3px 7px; border-radius: 12px; font-size: 0.75em; font-weight: 600; color: white; white-space: nowrap; }
        .btn-row { display: flex; gap: 4px; margin-top: 6px; flex-wrap: wrap; flex-shrink: 0; }
        .btn { flex: 1; min-width: 0; padding: 6px 8px; border: none; border-radius: 6px;
               font-size: clamp(0.65em, 2vw, 0.83em); font-weight: 600; cursor: pointer; color: white; white-space: nowrap; }
        .btn.mow, .btn.edit { background: var(--primary-color, #03a9f4); }
        .btn.save { background: #2e7d32; }
        .btn.rename { background: #6a1b9a; flex: 1; min-width: 72px; }
        .btn.cancel { background: #757575; flex: 1; min-width: 60px; }
        .btn.reset, .btn.expand, .btn.settings, .btn.sched, .btn.backup, .btn.camera { background: #455a64; flex: 0; min-width: 36px; }
        .btn.pin { background: #455a64; flex: 1; min-width: 72px; }
        .btn.settings-active { background: #ef6c00; }
        .rename-input { flex: 1; padding: 7px 8px; border: 1px solid var(--divider-color,#444); border-radius: 6px; background: var(--card-background-color,#1c1c1c); color: var(--primary-text-color); font-size: 0.85em; }
        .settings-panel { margin-top: 8px; padding: 10px 12px; background: var(--card-background-color, #1c1c1c);
          border: 1px solid var(--divider-color, #444); border-radius: 8px; flex-shrink: 0; overflow-y: auto; max-height: 60vh; }
        .settings-panel .sp-title { font-size: 0.8em; font-weight: 600; color: var(--secondary-text-color);
          text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
        .sp-row { display: grid; grid-template-columns: 120px 1fr 42px; align-items: center; gap: 6px; margin-bottom: 6px; }
        .sp-row label { font-size: 0.8em; color: var(--primary-text-color); }
        .sp-row input[type=range] { width: 100%; accent-color: var(--primary-color, #03a9f4); }
        .sp-select { width: 100%; background: var(--card-background-color, #1c1c1c); color: var(--primary-text-color); border: 1px solid var(--divider-color, #444); border-radius: 4px; padding: 2px 4px; font-size: 0.8em; }
        .sp-row .sp-val { font-size: 0.8em; color: var(--secondary-text-color); text-align: right; }
        .sp-apply { margin-top: 6px; width: 100%; padding: 7px; border: none; border-radius: 6px;
          background: var(--primary-color, #03a9f4); color: white; font-size: 0.85em; font-weight: 600; cursor: pointer; }
        .sp-apply:hover { filter: brightness(1.1); }
        .sp-status { font-size: 0.75em; color: var(--secondary-text-color); margin-top: 4px; min-height: 1.2em; }
        .sp-cut-btn { flex: 1; padding: 4px 8px; border: 1px solid var(--divider-color,#444); border-radius: 4px; background: var(--secondary-background-color,#2a2a2a); color: var(--primary-text-color); font-size: 0.8em; cursor: pointer; }
        .sp-cut-btn:hover { background: var(--primary-color,#03a9f4); color: white; }
        .sp-advanced { margin-top: 8px; border-top: 1px solid var(--divider-color, #444); padding-top: 6px; }
        .sp-advanced summary { font-size: 0.78em; font-weight: 600; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: 0.05em; cursor: pointer; user-select: none; margin-bottom: 6px; list-style: none; }
        .sp-advanced summary::before { content: "▶ "; font-size: 0.7em; }
        .sp-advanced[open] summary::before { content: "▼ "; }
        .btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .btn:not(:disabled):hover { filter: brightness(1.1); }
        .edit-bar { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 6px; flex-shrink: 0; }
        .sched-row { display: flex; gap: 8px; align-items: baseline; padding: 3px 0; border-bottom: 1px solid var(--divider-color,#333); font-size: 0.82em; }
        .sched-row:last-child { border-bottom: none; }
        .sched-disabled { opacity: 0.45; }
        .sched-time { font-weight: 700; color: var(--primary-text-color); min-width: 38px; }
        .sched-days { flex: 1; color: var(--secondary-text-color); }
        .sched-status { font-size: 0.9em; color: var(--secondary-text-color); }
        .backup-row { display: flex; gap: 5px; align-items: center; padding: 4px 0; border-bottom: 1px solid var(--divider-color,#333); font-size: 0.82em; }
        .backup-row:last-child { border-bottom: none; }
        .backup-name { flex: 1; font-weight: 600; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .backup-date { font-size: 0.85em; color: var(--secondary-text-color); white-space: nowrap; }
        .backup-action-btn { padding: 2px 6px; border: none; border-radius: 4px; cursor: pointer; background: #455a64; color: white; font-size: 0.82em; flex-shrink: 0; }
        .backup-delete { background: #c62828; }
        .backup-create-btn { margin-bottom: 6px; }
        .backup-status { font-size: 0.78em; color: var(--secondary-text-color); min-height: 1.2em; }
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
               xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet"
               class="${this._pinAndGoMode ? 'pin-mode' : ''}">
            <defs>${goLabelDefs}</defs>
            <g transform="rotate(${this._mapRotation.toFixed(2)}, ${(this._vx + this._vw/2).toFixed(3)}, ${(this._vy + this._vh/2).toFixed(3)})">
            ${channelPaths}
            ${goPaths}
            ${serverTrail}
            ${liveTrail}
            ${goLabels}
            ${nogoPaths}
            ${nogoLabels}
            ${channelLabels}
            ${csHtml}
            ${robotHtml}
            ${rtkHtml}
            ${editOverlay}
            ${splitOverlay}
            ${drawOverlay}
            </g>
          </svg>
          <div class="map-overlay" id="map-overlay">
            <svg class="north-arrow" viewBox="0 0 44 44" xmlns="http://www.w3.org/2000/svg"
                 data-action="rotate-north" style="cursor:pointer" title="${this._mapRotation !== 0 ? 'Click to reset to north' : 'North up'}">
              <circle cx="22" cy="22" r="20" fill="white" opacity="0.85"/>
              <g transform="rotate(${(-this._mapRotation).toFixed(2)}, 22, 22)">
                <line x1="22" y1="30" x2="22" y2="12" stroke="#333" stroke-width="2"/>
                <polygon points="22,10 17,20 27,20" fill="#c0392b"/>
                <line x1="22" y1="14" x2="22" y2="34" stroke="#aaa" stroke-width="1.5"/>
              </g>
              <text x="22" y="40" text-anchor="middle" font-size="9" fill="#333" font-weight="bold">N</text>
              ${this._mapRotation !== 0 ? `<circle cx="22" cy="22" r="20" fill="none" stroke="#03a9f4" stroke-width="2" stroke-dasharray="4,2"/>` : ''}
            </svg>
            <div class="scale-bar-wrap" id="scale-bar-wrap">
              <span class="scale-bar-label" id="scale-bar-label">…</span>
              <div class="scale-bar" id="scale-bar"></div>
            </div>
          </div>
        </div>
        ${statusBar}
        ${toolbar}
        ${settingsPanel}
        ${schedulePanel}
        ${backupPanel}
        <div class="legend">${legendItems}</div>
      </ha-card>`;

    this._updateScaleBar();
    this._wireEvents();

    // Defer both scroll restores to after reflow — synchronous restore fires before
    // the browser finishes laying out the new DOM, causing visible jumps.
    if (prevScrollTop > 0 || savedScrollY > 0) {
      const sr = this.shadowRoot;
      requestAnimationFrame(() => {
        if (prevScrollTop > 0) {
          const newPanel = sr.querySelector(".settings-panel");
          if (newPanel) newPanel.scrollTop = prevScrollTop;
        }
        if (savedScrollY > 0) window.scrollTo(0, savedScrollY);
      });
    }

    // Persist <details> open/close toggle into component state so next render restores it.
    const advEl = this.shadowRoot.querySelector(".sp-advanced");
    if (advEl) advEl.addEventListener("toggle", () => { this._advancedOpen = advEl.open; });

    // Re-render once after layout if _pxPerUnit was a fallback estimate.
    // Guard: only fire when pxPerUnit is still the default (2.8) so this
    // doesn't loop on every hass update.
    if (this._pxPerUnit === 2.8) {
      requestAnimationFrame(() => {
        const svg = this.shadowRoot?.querySelector("svg");
        if (!svg) return;
        const r = svg.getBoundingClientRect();
        if (!r.width) return;
        const truePpu = r.width / this._vw;
        if (Math.abs(truePpu - this._pxPerUnit) > 0.2) {
          this._pxPerUnit = truePpu;
          this._render();
        }
      });
    }
  }

  _niceNumber(x) {
    if (x <= 0) return 5;
    const magnitude = Math.pow(10, Math.floor(Math.log10(x)));
    for (const n of [1, 2, 5, 10]) if (n * magnitude >= x) return n * magnitude;
    return 10 * magnitude;
  }

  // Update the pixel-space scale bar to reflect current zoom without re-render.
  _updateScaleBar() {
    const wrap = this.shadowRoot.getElementById("scale-bar-wrap");
    const bar = this.shadowRoot.getElementById("scale-bar");
    const label = this.shadowRoot.getElementById("scale-bar-label");
    if (!wrap || !bar || !label || !this._bounds) return;

    const svg = this.shadowRoot.querySelector("svg");
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;

    // px per SVG user unit at current zoom
    const pxPerUnit = rect.width / this._vw;
    // px per metre
    const pxPerMetre = pxPerUnit * this._scale;
    // target pixel width → metres → round nicely
    const targetMetres = _SCALEBAR_PX_W / pxPerMetre;
    const niceMetres = this._niceNumber(targetMetres);
    const barPx = Math.round(niceMetres * pxPerMetre);

    bar.style.width = `${barPx}px`;
    label.textContent = niceMetres >= 1000 ? `${niceMetres / 1000} km` : `${niceMetres} m`;
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

    // Wire toolbar buttons via data-action (more reliable than inline onclick in Shadow DOM)
    this.shadowRoot.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        switch (btn.dataset.action) {
          case "edit":           this._enterEdit(); break;
          case "pin":            this._togglePinAndGo(); break;
          case "sched":          this._toggleSchedule(); break;
          case "settings":       this._toggleSettings(); break;
          case "camera":         this._openCamera(); break;
          case "expand":         this._toggleExpand(); break;
          case "reset":          this._resetView(); break;
          case "mow":            this._mowSelected(); break;
          case "save-edit":      this._saveEdit(); break;
          case "cancel-edit":    this._cancelEdit(); break;
          case "enter-rename":   this._enterRename(); break;
          case "save-rename":    this._saveRename(); break;
          case "cancel-rename":  this._cancelRename(); break;
          case "apply-settings":    this._applySettings(); break;
          case "cut-height-raise":  this._adjustCutHeight(true); break;
          case "cut-height-lower":  this._adjustCutHeight(false); break;
          case "rotate-north":      this._resetRotation(); break;
          case "delete-zone":       this._deleteEditZone(); break;
          case "draw-go":           this._startDraw("go"); break;
          case "draw-nogo":         this._startDraw("nogo"); break;
          case "draw-channel":      this._startDraw("channel"); break;
          case "save-draw":         this._saveDraw(); break;
          case "confirm-draw":      this._confirmDraw(); break;
          case "cancel-draw":       this._cancelDraw(); break;
          case "start-split":       this._startSplit(); break;
          case "cancel-split":      this._cancelSplit(); break;
          case "merge":             this._mergeSelected(); break;
          case "apply-zone-cut-height": this._applyZoneConfig(); break;
          case "apply-zone-config":    this._applyZoneConfig(); break;
          case "backup":            this._toggleBackup(); break;
          case "backup-create":     this._createBackup(); break;
        }
      });
    });

    // Backup panel actions (restore / rename / delete)
    this.shadowRoot.querySelectorAll("[data-backup-action]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const file = btn.dataset.file;
        switch (btn.dataset.backupAction) {
          case "restore":
            if (file) this._restoreBackup(file);
            break;
          case "rename-start":
            this._backupRenaming = file || null;
            this._render();
            setTimeout(() => {
              const idx = btn.dataset.idx;
              const inp = this.shadowRoot.querySelector(`#backup-rename-${btn.closest(".backup-row")?.dataset?.idx ?? ""}`);
              if (!inp) {
                // fallback: find any backup-rename-input
                const anyInp = this.shadowRoot.querySelector(".backup-rename-input");
                if (anyInp) { anyInp.select(); anyInp.focus(); }
              } else { inp.select(); inp.focus(); }
            }, 30);
            break;
          case "rename-ok": {
            const idx = btn.dataset.idx;
            const inp = this.shadowRoot.querySelector(`#backup-rename-${idx}`);
            const newName = inp?.value?.trim() || "";
            if (file && newName) this._renameBackup(file, newName);
            this._backupRenaming = null;
            this._render();
            break;
          }
          case "rename-cancel":
            this._backupRenaming = null;
            this._render();
            break;
          case "delete":
            if (file) this._deleteBackup(file);
            break;
        }
      });
    });

    svg.addEventListener("wheel", (e) => this._onWheel(e), { passive: false });
    svg.addEventListener("touchstart", (e) => this._onTouchStart(e), { passive: false });
    svg.addEventListener("touchmove", (e) => this._onTouchMove(e), { passive: false });
    svg.addEventListener("touchend", (e) => this._onTouchEnd(e));

    // Block re-renders while a range slider is being dragged.
    this.shadowRoot.querySelectorAll('input[type="range"]').forEach((el) => {
      el.addEventListener("pointerdown", () => { this._sliderActive = true; });
      el.addEventListener("pointerup", () => { this._sliderActive = false; });
      el.addEventListener("pointercancel", () => { this._sliderActive = false; });
    });

    if (this._editing) {
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        el.addEventListener("pointerdown", () => { this._panMoved = false; });
        el.addEventListener("click", () => { if (!this._panMoved && !this._drawingZone) this._chooseEditZone(el.dataset.hash, "go"); });
      });
      this.shadowRoot.querySelectorAll('polygon[data-type="nogo"]').forEach((el) => {
        el.addEventListener("pointerdown", () => { this._panMoved = false; });
        el.addEventListener("click", () => { if (!this._panMoved && !this._drawingZone) this._chooseEditZone(el.dataset.hash, "nogo"); });
      });
      this.shadowRoot.querySelectorAll('polyline[data-type="channel"]').forEach((el) => {
        el.addEventListener("pointerdown", () => { this._panMoved = false; });
        el.addEventListener("click", () => { if (!this._panMoved && !this._drawingZone) this._chooseEditZone(el.dataset.hash, "channel"); });
      });
      this.shadowRoot.querySelectorAll(".midpoint").forEach((el) => {
        // Stop pointerdown reaching the SVG pan handler (it would setPointerCapture and swallow the click).
        el.addEventListener("pointerdown", (e) => { e.stopPropagation(); });
        el.addEventListener("click", (e) => { e.stopPropagation(); this._insertVertex(+el.dataset.edge); });
      });
      this.shadowRoot.querySelectorAll(".delvert").forEach((el) => {
        el.addEventListener("pointerdown", (e) => { e.stopPropagation(); });
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
      const csMarker = this.shadowRoot.querySelector('[data-marker="cs"]');
      if (csMarker && !this._editHash) {
        csMarker.addEventListener("pointerdown", (e) => {
          e.stopPropagation();
          this._panMoved = false;
          this._dragStation = true;
          this._panning = false;
          try { csMarker.setPointerCapture(e.pointerId); } catch (_) {}
        });
        csMarker.addEventListener("pointermove", (e) => {
          if (!this._dragStation) return;
          e.preventDefault();
          const enu = this._clientToEnu(e);
          if (!enu) return;
          this._panMoved = true;
          csMarker.setAttribute("transform", `translate(${this._sx(enu.x)},${this._sy(enu.y)}) scale(${1 / this._zf})`);
          csMarker.dataset.enuX = enu.x;
          csMarker.dataset.enuY = enu.y;
        });
        csMarker.addEventListener("pointerup", async () => {
          if (!this._dragStation) return;
          this._dragStation = false;
          const x = parseFloat(csMarker.dataset.enuX);
          const y = parseFloat(csMarker.dataset.enuY);
          if (this._panMoved && this._config?.mower_entity && !isNaN(x) && !isNaN(y)) {
            try {
              await this._hass.callService("lymow", "move_charging_station", {
                entity_id: this._config.mower_entity, x, y,
              });
            } catch (err) {
              console.error("move_charging_station failed:", err);
            }
          }
          this._render();
        });
        csMarker.addEventListener("pointercancel", () => { this._dragStation = false; this._render(); });
      }
    } else {
      this.shadowRoot.querySelectorAll('polygon[data-type="go"]').forEach((el) => {
        // Single click: select/deselect zone
        el.addEventListener("click", (e) => { if (!this._panMoved) { e.stopPropagation(); this._toggleZone(el.dataset.hash); } });
        // Long press: toggle zone enabled/disabled; also reset panMoved so click fires
        el.addEventListener("pointerdown", () => {
          this._panMoved = false;
          this._longPressTimer = setTimeout(() => { this._longPressTimer = null; this._toggleZoneEnabled(el.dataset.hash); }, 700);
        });
        el.addEventListener("pointerup", () => { if (this._longPressTimer) { clearTimeout(this._longPressTimer); this._longPressTimer = null; } });
        el.addEventListener("pointercancel", () => { if (this._longPressTimer) { clearTimeout(this._longPressTimer); this._longPressTimer = null; } });
      });
    }

    // Double-click on map for pin-and-go
    if (this._pinAndGoMode) {
      svg.addEventListener("dblclick", (e) => { e.stopPropagation(); this._onPinAndGo(e); });
    }

    // Split mode: 2 clicks define the cut line, then auto-submit
    if (this._splitMode) {
      svg.style.cursor = "crosshair";
      svg.addEventListener("click", (e) => {
        if (this._panMoved) return;
        const enu = this._clientToEnu(e);
        if (!enu) return;
        if (!this._splitPoly) this._splitPoly = [];
        this._splitPoly.push(enu);
        if (this._splitPoly.length >= 2) {
          this._executeSplit();
        } else {
          this._render();
        }
      });
    }

    // Draw mode: left-click adds points; click first point closes polygon (not for channels)
    if (this._drawingZone) {
      svg.style.cursor = "crosshair";
      svg.addEventListener("click", (e) => {
        if (this._panMoved) return;
        const enu = this._clientToEnu(e);
        if (!enu) return;
        // For zones: clicking near first point closes and saves
        if (this._drawingZone !== "channel" && this._drawPoly && this._drawPoly.length >= 3) {
          const first = this._drawPoly[0];
          const dx = parseFloat(this._sx(enu.x)) - parseFloat(this._sx(first.x));
          const dy = parseFloat(this._sy(enu.y)) - parseFloat(this._sy(first.y));
          if (Math.sqrt(dx*dx + dy*dy) < 3) {
            this._saveDraw();
            return;
          }
        }
        if (!this._drawPoly) this._drawPoly = [];
        this._drawPoly.push(enu);
        this._render();
      });
    }

    // Pan: any pointer drag on SVG background (not on zone polygons or markers)
    // Right-click drag = rotate map
    svg.addEventListener("contextmenu", (e) => e.preventDefault());
    svg.addEventListener("pointerdown", (e) => {
      if (this._dragIdx != null) return;
      if (e.target?.dataset?.hash || e.target?.dataset?.type) return;
      if (e.button === 2) {
        // Right-click drag → rotate
        this._rotating = true;
        this._panMoved = false;
        this._rotateStart = { x: e.clientX, rotation: this._mapRotation };
        svg.setPointerCapture(e.pointerId);
        svg.classList.add("rotating");
        return;
      }
      if (this._drawingZone) return; // clicks handled by the draw click listener
      this._panning = true;
      this._panMoved = false;
      this._panStart = { x: e.clientX, y: e.clientY, vx: this._vx, vy: this._vy };
      svg.setPointerCapture(e.pointerId);
      svg.classList.add("panning");
    });
    svg.addEventListener("pointermove", (e) => {
      if (this._rotating && this._rotateStart) {
        const dx = e.clientX - this._rotateStart.x;
        this._mapRotation = (this._rotateStart.rotation + dx * 0.4 + 360) % 360;
        this._panMoved = true;
        this._updateSvgRotation();
        return;
      }
      this._onPan(e);
    });
    svg.addEventListener("pointerup", () => {
      this._panning = false;
      this._rotating = false;
      this._rotateStart = null;
      svg.classList.remove("panning");
      svg.classList.remove("rotating");
    });
    svg.addEventListener("pointercancel", () => {
      this._panning = false;
      this._rotating = false;
      this._rotateStart = null;
      svg.classList.remove("panning");
      svg.classList.remove("rotating");
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

  _updateSvgRotation() {
    const svg = this.shadowRoot?.querySelector("svg");
    if (!svg) return;
    const g = svg.querySelector("g[transform*=rotate]");
    if (!g) return;
    const cx = (this._vx + this._vw / 2).toFixed(3);
    const cy = (this._vy + this._vh / 2).toFixed(3);
    g.setAttribute("transform", `rotate(${this._mapRotation.toFixed(2)}, ${cx}, ${cy})`);
    // Update north arrow counter-rotation
    const northG = this.shadowRoot.querySelector(".north-arrow g[transform*=rotate]");
    if (northG) northG.setAttribute("transform", `rotate(${(-this._mapRotation).toFixed(2)}, 22, 22)`);
    // Show/hide the rotation indicator ring
    const ring = this.shadowRoot.querySelector(".north-arrow circle[stroke='#03a9f4']");
    const northSvg = this.shadowRoot.querySelector(".north-arrow");
    if (northSvg) northSvg.title = this._mapRotation !== 0 ? 'Click to reset to north' : 'North up';
  }

  _resetRotation() {
    this._mapRotation = 0;
    this._render();
  }

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
    // Scale bar is in pixel space — just recompute its width from current zoom
    this._updateScaleBar();
    // Update fixed-pixel marker scales (robot/RTK/station use SVG scale transform)
    this._updateMarkerScales();
  }

  _updateMarkerScales() {
    if (!this._bounds) return;
    const zf = this._zoomFactor();
    const invZf = (1 / zf).toFixed(6);
    // Keep _pxPerUnit current so next _render() computes correct sizes
    const svg = this.shadowRoot.querySelector("svg");
    if (svg) {
      const r = svg.getBoundingClientRect();
      if (r.width) this._pxPerUnit = r.width / this._vw;
    }
    this.shadowRoot.querySelectorAll("g[data-marker]").forEach((g) => {
      const cx = g.dataset.cx, cy = g.dataset.cy;
      g.setAttribute("transform", `translate(${cx},${cy}) scale(${invZf})`);
    });
  }

  _resetView() { this._mapReady = false; this._render(); }

  // ---------------------------------------------------------------------------
  // Expand / collapse
  // ---------------------------------------------------------------------------

  _toggleExpand() {
    this._expanded = !this._expanded;
    if (this._expanded) {
      this.classList.add("expanded");
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
  // RTK auto-pause
  // ---------------------------------------------------------------------------

  // Called every render when rtk_autopause is enabled in config.
  // Pauses the mower if fix quality drops to float or worse (rtkStatus < 2)
  // while mowing. Won't re-pause more than once per degraded episode.
  _checkRtkAutopause(rtkStatus, workStatus) {
    if (rtkStatus === undefined || rtkStatus === null) return;
    const fix = parseInt(rtkStatus);
    // workStatus 2=Mowing, 8=Resuming, 9=Zone-partition mowing
    const ws = workStatus !== undefined ? parseInt(workStatus) : -1;
    const isMowing = ws === 2 || ws === 8 || ws === 9;
    const minFix = this._config.rtk_autopause_min_fix ?? 2; // default: require Fixed or RTK
    const fixLow = fix < minFix;

    if (fixLow && isMowing && !this._rtkPauseSent) {
      this._rtkPauseSent = true;
      this._hass.callService("lawn_mower", "pause", { entity_id: this._config.mower_entity })
        .catch((err) => console.warn("lymow-map-card: RTK auto-pause failed:", err));
    }
    // Reset once fix recovers so next degradation episode can pause again
    if (!fixLow) this._rtkPauseSent = false;
  }

  // ---------------------------------------------------------------------------
  // Settings panel
  // ---------------------------------------------------------------------------

  _toggleSettings() {
    this._settingsOpen = !this._settingsOpen;
    this._scheduleOpen = false;
    this._backupOpen = false;
    if (this._settingsOpen && !this._settingsValues) {
      const hardDefaults = {
        move_speed: 0.6, cut_speed: 4,
        path_spacing: 35, perimeter_mow_laps: 1, nogo_mow_laps: 1,
        perimeter_mow_dir: 2, obs_dec_mode: 2,
        clean_mode: 1, path_order: 0, safe_margin_mode: 1, turn_off_outer_motor: 0,
      };
      const saved = localStorage.getItem("lymow_settings_values");
      let storedVals = {};
      if (saved) {
        try { storedVals = JSON.parse(saved); } catch { storedVals = {}; }
      }
      // Sanity-check integer fields: cut_speed must be 1–10 int; reject floats
      if (typeof storedVals.cut_speed === 'number' && (storedVals.cut_speed < 1 || storedVals.cut_speed > 10 || !Number.isInteger(storedVals.cut_speed))) {
        delete storedVals.cut_speed;
      }
      const defaults = { ...hardDefaults, ...storedVals };
      // Overlay live robot state (globalZoneConfig echo) when available so the
      // panel reflects what the robot actually has, not just what HA last sent.
      const ms = this._getMapData()?.mowingSettings;
      if (ms) {
        const fromRobot = {
          move_speed: ms.moveSpeed,
          path_spacing: ms.pathSpacing,
          perimeter_mow_laps: ms.perimeterMowLaps,
          nogo_mow_laps: ms.noGoMowLaps,
          perimeter_mow_dir: ms.perimeterMowDir,
          obs_dec_mode: ms.obsDecMode,
          clean_mode: ms.cleanMode,
          path_order: ms.pathOrder ? 1 : 0,
          safe_margin_mode: ms.safeMarginMode != null ? (ms.safeMarginMode ? 1 : 0) : null,
          turn_off_outer_motor: ms.turnOffOuterMotor ? 1 : 0,
        };
        Object.entries(fromRobot).forEach(([k, v]) => { if (v != null) defaults[k] = v; });
      }
      this._settingsValues = defaults;
    }
    this._render();
  }

  _toggleSchedule() {
    this._scheduleOpen = !this._scheduleOpen;
    this._settingsOpen = false;
    this._backupOpen = false;
    this._render();
  }

  _toggleBackup() {
    this._backupOpen = !this._backupOpen;
    this._settingsOpen = false;
    this._scheduleOpen = false;
    this._backupRenaming = null;
    this._render();
  }

  _backupStatus(msg) {
    const el = this.shadowRoot.querySelector(".backup-status");
    if (el) el.textContent = msg;
  }

  async _createBackup() {
    if (!this._hass || !this._config.mower_entity) return;
    this._backupStatus("Creating backup…");
    try {
      await this._hass.callService("lymow", "backup_map", { entity_id: this._config.mower_entity });
      this._backupStatus("✓ Backup created (list updates in ~5 min)");
      setTimeout(() => this._backupStatus(""), 5000);
    } catch (err) {
      this._backupStatus(`⚠️ ${err?.message || err}`);
    }
  }

  async _restoreBackup(file) {
    if (!this._hass || !this._config.mower_entity || !file) return;
    this._backupStatus("Restoring…");
    try {
      await this._hass.callService("lymow", "restore_backup_map", {
        entity_id: this._config.mower_entity,
        object_key: file,
      });
      this._backupStatus("✓ Restored — map will update shortly");
      setTimeout(() => this._backupStatus(""), 5000);
    } catch (err) {
      this._backupStatus(`⚠️ ${err?.message || err}`);
    }
  }

  async _renameBackup(file, name) {
    if (!this._hass || !this._config.mower_entity || !file || !name) return;
    this._backupStatus("Renaming…");
    try {
      await this._hass.callService("lymow", "rename_backup_map", {
        entity_id: this._config.mower_entity,
        object_key: file,
        name,
      });
      this._backupStatus("✓ Renamed");
      setTimeout(() => this._backupStatus(""), 3000);
    } catch (err) {
      this._backupStatus(`⚠️ ${err?.message || err}`);
    }
  }

  async _deleteBackup(file) {
    if (!this._hass || !this._config.mower_entity || !file) return;
    this._backupStatus("Deleting…");
    try {
      await this._hass.callService("lymow", "delete_backup_map", {
        entity_id: this._config.mower_entity,
        object_key: file,
      });
      this._backupStatus("✓ Deleted");
      setTimeout(() => this._backupStatus(""), 3000);
    } catch (err) {
      this._backupStatus(`⚠️ ${err?.message || err}`);
    }
  }

  _togglePinAndGo() {
    this._pinAndGoMode = !this._pinAndGoMode;
    this._render();
  }

  async _applySettings() {
    if (!this._hass || !this._config.mower_entity) return;
    const inputs = this.shadowRoot.querySelectorAll(".sp-input");
    const payload = { entity_id: this._config.mower_entity };
    const localFields = new Set(["go_label_mode", "nogo_label_mode", "ch_label_mode"]);
    inputs.forEach((el) => {
      const v = el.dataset.type === "float" ? parseFloat(el.value) : parseInt(el.value, 10);
      if (!this._settingsValues) this._settingsValues = {};
      this._settingsValues[el.dataset.field] = v;
      if (localFields.has(el.dataset.field)) return;
      payload[el.dataset.field] = v;
    });
    this._goLabelMode = this._settingsValues.go_label_mode ?? this._goLabelMode;
    this._nogoLabelMode = this._settingsValues.nogo_label_mode ?? this._nogoLabelMode;
    this._chLabelMode = this._settingsValues.ch_label_mode ?? this._chLabelMode;
    localStorage.setItem("lymow_go_label_mode", this._goLabelMode);
    localStorage.setItem("lymow_nogo_label_mode", this._nogoLabelMode);
    localStorage.setItem("lymow_ch_label_mode", this._chLabelMode);
    const status = this.shadowRoot.querySelector(".sp-status");
    if (status) status.textContent = "Sending…";
    try {
      await this._hass.callService("lymow", "set_task_config", payload);
      // Persist applied values so they survive page reloads (robot doesn't echo them back).
      localStorage.setItem("lymow_settings_values", JSON.stringify(this._settingsValues));
      if (status) status.textContent = "✓ Applied";
      setTimeout(() => { if (status) status.textContent = ""; }, 3000);
    } catch (err) {
      if (status) status.textContent = `⚠️ ${err?.message || err}`;
    }
  }

  async _adjustCutHeight(raise) {
    if (!this._hass || !this._config.mower_entity) return;
    const status = this.shadowRoot.querySelector(".sp-status");
    if (status) status.textContent = "Sending…";
    try {
      await this._hass.callService("lymow", "set_task_config", {
        entity_id: this._config.mower_entity,
        ...(raise ? { raise_cut_height: true } : { lower_cut_height: true }),
      });
      if (status) status.textContent = `✓ Cut height ${raise ? "raised" : "lowered"}`;
      setTimeout(() => { if (status) status.textContent = ""; }, 3000);
    } catch (err) {
      if (status) status.textContent = `⚠️ ${err?.message || err}`;
    }
  }

  async _applyZoneConfig() {
    if (!this._hass || !this._config.mower_entity || !this._editHash || this._editType !== "go") return;
    const status = this.shadowRoot.querySelector(".zone-ch-status");
    const ch  = parseInt(this.shadowRoot.getElementById("zs-cut-height")?.value, 10);
    const ms  = parseFloat(this.shadowRoot.getElementById("zs-move-speed")?.value);
    const ps  = parseInt(this.shadowRoot.getElementById("zs-path-spacing")?.value, 10);
    const pl  = parseInt(this.shadowRoot.getElementById("zs-perimeter-laps")?.value, 10);
    const sm  = parseInt(this.shadowRoot.getElementById("zs-safe-margin")?.value, 10);
    const om  = parseInt(this.shadowRoot.getElementById("zs-outer-motor")?.value, 10);
    if (!Number.isFinite(ch) || ch < 20 || ch > 100) { if (status) status.textContent = "⚠️ cut height 20–100"; return; }
    if (!Number.isFinite(ms) || ms < 0.1 || ms > 1.5) { if (status) status.textContent = "⚠️ speed 0.1–1.5"; return; }
    if (status) status.textContent = "Sending…";
    const data = {
      entity_id: this._config.mower_entity,
      zone_hash_id: this._editHash,
      cut_height: ch,
      move_speed: ms,
    };
    if (Number.isFinite(ps) && ps >= 0) data.path_spacing = ps;
    if (Number.isFinite(pl) && pl >= 0) data.perimeter_mow_laps = pl;
    if (Number.isFinite(sm)) data.safe_margin_mode = sm;
    if (Number.isFinite(om)) data.turn_off_outer_motor = om;
    try {
      await this._hass.callService("lymow", "set_zone_config", data);
      if (status) status.textContent = `✓ Applied`;
      setTimeout(() => { if (status) status.textContent = ""; }, 3000);
    } catch (err) {
      if (status) status.textContent = `⚠️ ${err?.message || err}`;
    }
  }

  // ---------------------------------------------------------------------------
  // Zone enable / disable (long-press a go-zone when not editing)
  // ---------------------------------------------------------------------------

  async _toggleZoneEnabled(hashId) {
    if (!this._hass || !this._config.mower_entity) return;
    const mapData = this._getMapData();
    const zone = mapData?.goZones?.find(z => z.hashId === hashId);
    if (!zone) return;
    const nowEnabled = zone.isEnabled !== false;
    const newEnabled = !nowEnabled;
    // Optimistic UI: flip isEnabled locally
    const goZoneState = this._hass.states[this._config.entity];
    if (goZoneState?.attributes?.go_zones) {
      const z = goZoneState.attributes.go_zones.find(z => z.hashId === hashId);
      if (z) z.isEnabled = newEnabled;
    }
    this._render();
    try {
      await this._hass.callService("lymow", "set_zone_enabled", {
        entity_id: this._config.mower_entity,
        zone_hash_id: hashId,
        is_enabled: newEnabled,
      });
    } catch (err) {
      console.warn("lymow-map-card: zone enable toggle failed", err);
      // Revert optimistic change
      if (goZoneState?.attributes?.go_zones) {
        const z = goZoneState.attributes.go_zones.find(z => z.hashId === hashId);
        if (z) z.isEnabled = nowEnabled;
      }
      this._render();
    }
  }

  // ---------------------------------------------------------------------------
  // Draw new zone
  // ---------------------------------------------------------------------------

  _startDraw(type) {
    this._drawingZone = type;
    this._drawPoly = [];
    this._editHash = null;
    this._workPoly = null;
    this._render();
  }

  _cancelDraw() {
    this._drawingZone = null;
    this._drawPoly = null;
    this._drawNameStep = false;
    this._pendingDrawPolygon = null;
    this._pendingDrawType = null;
    this._render();
  }

  _saveDraw() {
    const minPts = this._drawingZone === "channel" ? 2 : 3;
    if (!this._drawPoly || this._drawPoly.length < minPts) return;
    // Capture polygon and switch to the name-confirmation step
    this._pendingDrawPolygon = this._drawPoly.map((p) => ({ x: +p.x.toFixed(4), y: +p.y.toFixed(4) }));
    this._pendingDrawType = this._drawingZone;
    this._drawingZone = null;
    this._drawPoly = null;
    this._drawNameStep = true;
    this._render();
    setTimeout(() => { this.shadowRoot.querySelector('#draw-name-input')?.focus(); }, 50);
  }

  async _confirmDraw() {
    if (!this._hass || !this._config.mower_entity || !this._pendingDrawPolygon) return;
    const polygon = this._pendingDrawPolygon;
    const type = this._pendingDrawType;
    const nameInput = this.shadowRoot.querySelector('#draw-name-input');
    const name = nameInput?.value?.trim() || "";
    this._drawNameStep = false;
    this._pendingDrawPolygon = null;
    this._pendingDrawType = null;
    const bar = this.shadowRoot.querySelector(".edit-bar");
    if (bar) bar.textContent = "Saving…";
    try {
      if (type === "nogo") {
        await this._hass.callService("lymow", "add_nogo_zone", {
          entity_id: this._config.mower_entity,
          polygon,
        });
      } else if (type === "channel") {
        await this._hass.callService("lymow", "add_channel", {
          entity_id: this._config.mower_entity,
          polygon,
          cut_height_mm: 40,
        });
      } else {
        await this._hass.callService("lymow", "add_zone", {
          entity_id: this._config.mower_entity,
          polygon,
          cut_height_mm: 40,
          ...(name ? { name } : {}),
        });
      }
    } catch (err) {
      console.error("lymow-map-card: add zone/channel failed", err);
      this._render();
      const b = this.shadowRoot.querySelector(".edit-bar");
      if (b) b.textContent = `⚠️ ${err?.message || err}`;
    }
  }

  _startSplit() {
    if (!this._editHash || this._editType !== "go") return;
    this._splitMode = true;
    this._splitPoly = [];
    this._render();
  }

  _cancelSplit() {
    this._splitMode = false;
    this._splitPoly = null;
    this._render();
  }

  async _executeSplit() {
    if (!this._hass || !this._config.mower_entity || !this._editHash || !this._splitPoly || this._splitPoly.length < 2) return;
    const hashId = this._editHash;
    const [p1, p2] = this._splitPoly;
    this._splitMode = false;
    this._splitPoly = null;
    this._cancelEdit();
    const bar = this.shadowRoot.querySelector(".edit-bar");
    if (bar) bar.textContent = "Splitting…";
    try {
      await this._hass.callService("lymow", "split_zone", {
        entity_id: this._config.mower_entity,
        zone_hash_id: hashId,
        cut_p1: { x: +p1.x.toFixed(4), y: +p1.y.toFixed(4) },
        cut_p2: { x: +p2.x.toFixed(4), y: +p2.y.toFixed(4) },
        names: ["", ""],
      });
    } catch (err) {
      console.error("lymow-map-card: split zone failed", err);
      this._render();
      const b = this.shadowRoot.querySelector(".edit-bar");
      if (b) b.textContent = `⚠️ Split failed: ${err?.message || err}`;
    }
  }

  async _mergeSelected() {
    if (!this._hass || !this._config.mower_entity || this._selectedZones.size < 2) return;
    const hashIds = [...this._selectedZones];
    this._selectedZones.clear();
    this._render();
    try {
      await this._hass.callService("lymow", "merge_zones", {
        entity_id: this._config.mower_entity,
        zone_hash_ids: hashIds,
      });
    } catch (err) {
      console.error("lymow-map-card: merge zones failed", err);
      this._render();
      const b = this.shadowRoot.querySelector(".edit-bar");
      if (b) b.textContent = `⚠️ Merge failed: ${err?.message || err}`;
    }
  }

  // ---------------------------------------------------------------------------
  // Pin-and-go (double-click map → send robot to ENU coordinate)
  // ---------------------------------------------------------------------------

  async _onPinAndGo(evt) {
    if (!this._hass || !this._config.mower_entity) return;
    const enu = this._clientToEnu(evt);
    if (!enu) return;
    this._pinAndGoMode = false;
    this._render();
    try {
      await this._hass.callService("lymow", "pin_and_go", {
        entity_id: this._config.mower_entity,
        x: +enu.x.toFixed(3),
        y: +enu.y.toFixed(3),
      });
    } catch (err) {
      console.warn("lymow-map-card: pin_and_go failed", err);
    }
  }

  // ---------------------------------------------------------------------------
  // Zone rename
  // ---------------------------------------------------------------------------

  _enterRename() {
    this._editRename = true;
    this._render();
    // Focus the input after render
    setTimeout(() => {
      const inp = this.shadowRoot.querySelector('#rename-input');
      if (!inp) return;
      inp.value = inp.defaultValue; // reset to the rendered default (zone's current name)
      inp.focus();
      inp.select();
    }, 50);
  }

  _cancelRename() {
    this._editRename = false;
    this._render();
  }

  async _saveRename() {
    const input = this.shadowRoot.querySelector('#rename-input');
    const newName = input?.value?.trim();
    if (!newName || !this._editHash || !this._hass || !this._config.mower_entity) {
      this._editRename = false; this._render(); return;
    }
    const hashId = this._editHash;
    const isNogo = this._editType === "nogo";
    const isChannel = this._editType === "channel";
    this._editRename = false;
    if (isChannel) {
      this._channelNameOverrides[hashId] = newName;
      localStorage.setItem("lymow_channel_names", JSON.stringify(this._channelNameOverrides));
    } else {
      this._nameOverrides[hashId] = newName;
    }
    this._render();
    try {
      if (isChannel) {
        await this._hass.callService("lymow", "rename_channel", {
          entity_id: this._config.mower_entity,
          channel_hash_id: hashId,
          name: newName,
        });
      } else if (isNogo) {
        await this._hass.callService("lymow", "rename_nogo_zone", {
          entity_id: this._config.mower_entity,
          nogo_hash_id: hashId,
          name: newName,
        });
      } else {
        await this._hass.callService("lymow", "rename_zone", {
          entity_id: this._config.mower_entity,
          zone_hash_id: hashId,
          name: newName,
        });
      }
    } catch (err) {
      console.warn("lymow-map-card: rename failed", err);
      if (isChannel) {
        delete this._channelNameOverrides[hashId];
        localStorage.setItem("lymow_channel_names", JSON.stringify(this._channelNameOverrides));
      } else {
        delete this._nameOverrides[hashId];
      }
      this._render();
    }
  }

  // ---------------------------------------------------------------------------
  // Edit mode
  // ---------------------------------------------------------------------------

  _enterEdit() {
    this._editing = true; this._editHash = null; this._workPoly = null;
    this._editRename = false; this._selectedZones.clear(); this._render();
  }

  _cancelEdit() {
    this._editing = false; this._editHash = null; this._editType = null;
    this._workPoly = null; this._editRename = false; this._dragIdx = null; this._dragStation = false;
    this._drawingZone = null; this._drawPoly = null;
    this._drawNameStep = false; this._pendingDrawPolygon = null; this._pendingDrawType = null;
    this._splitMode = false; this._splitPoly = null;
    this._render();
  }

  _chooseEditZone(hashId, type) {
    if (this._editHash === hashId) return;
    const mapData = this._getMapData();
    if (type === "channel") {
      const ch = (mapData?.channels || []).find((c) => c.hashId === hashId);
      if (!ch) return;
      this._editHash = hashId;
      this._editType = "channel";
      this._workPoly = null; // channels have no vertex editor yet
      this._render();
      return;
    }
    const list = type === "nogo" ? (mapData?.nogoZones || []) : (mapData?.goZones || []);
    const zone = list.find((z) => z.hashId === hashId);
    if (!zone || !zone.polygon) return;
    this._editHash = hashId;
    this._editType = type;
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

  async _deleteEditZone() {
    if (!this._hass || !this._editHash || !this._config.mower_entity) return;
    const hashId = this._editHash;
    const isNogo = this._editType === "nogo";
    const isChannel = this._editType === "channel";
    this._cancelEdit();
    if (isChannel) {
      delete this._channelNameOverrides[hashId];
      localStorage.setItem("lymow_channel_names", JSON.stringify(this._channelNameOverrides));
    }
    try {
      if (isChannel) {
        await this._hass.callService("lymow", "delete_channel", {
          entity_id: this._config.mower_entity,
          channel_hash_id: hashId,
        });
      } else if (isNogo) {
        await this._hass.callService("lymow", "delete_nogo_zone", {
          entity_id: this._config.mower_entity,
          nogo_hash_id: hashId,
        });
      } else {
        await this._hass.callService("lymow", "delete_zone", {
          entity_id: this._config.mower_entity,
          zone_hash_id: hashId,
        });
      }
    } catch (err) {
      console.error("lymow-map-card: delete zone failed", err);
      this._render();
      const bar = this.shadowRoot.querySelector(".edit-bar");
      if (bar) bar.textContent = `⚠️ Delete failed: ${err?.message || err}`;
    }
  }

  async _saveEdit() {
    if (!this._hass || !this._editHash || !this._workPoly || !this._config.mower_entity) return;
    const polygon = this._workPoly.map((p) => ({ x: +p.x.toFixed(4), y: +p.y.toFixed(4) }));
    const hashId = this._editHash;
    const isNogo = this._editType === "nogo";
    if (isNogo) {
      this._nogoOverrides[hashId] = polygon;
    } else {
      this._polyOverrides[hashId] = polygon;
    }
    this._cancelEdit();
    try {
      if (isNogo) {
        await this._hass.callService("lymow", "update_nogo_polygon", {
          entity_id: this._config.mower_entity,
          nogo_hash_id: hashId,
          polygon,
        });
      } else {
        await this._hass.callService("lymow", "update_zone_polygon", {
          entity_id: this._config.mower_entity,
          zone_hash_id: hashId,
          polygon,
        });
      }
    } catch (err) {
      console.error("lymow-map-card: save failed", err);
      if (isNogo) delete this._nogoOverrides[hashId]; else delete this._polyOverrides[hashId];
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

if (!customElements.get("lymow-map-card")) {
  customElements.define("lymow-map-card", LymowMapCard);
}

window.customCards = window.customCards || [];
window.customCards.push({
  type: "lymow-map-card",
  name: "Lymow Map",
  description: "Interactive map: go/no-go zones, channels, charging station, RTK base, robot pose. Zoom, pan, expand, edit zones.",
  preview: false,
});
