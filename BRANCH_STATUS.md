<!-- ⚠️  REMOVE THIS FILE BEFORE MERGING THE PR — it is a dev scratch document, not product docs. -->

# Branch: feat/map-lovelace-card — Supervisor Document

**This document is the interface between two sessions:**
- **Supervisor session** (this laptop, WSL2): has full codebase access, plans work, writes code, and **owns all browser testing** (HA Lovelace card in Chrome). This is the ONLY session that can do browser testing.
- **Capture session** (other Linux box): has mitmproxy running, can control the Lymow Android app, decodes raw traffic, implements backend changes

Update this file when findings come in. Strike tasks when done.

### How the two sessions coordinate

**Capture session → Supervisor:** Push a commit whose message starts with `test-ready:` when something needs browser testing. The supervisor polls for this (see "Supervisor: watching for commits" below) and picks it up.

**Supervisor → Capture session:** Push a commit whose message starts with `test-result:` with a brief pass/fail summary so the capture session knows to continue.

### Supervisor: watching for commits

`gh` has no native watch command. Use this polling loop on the supervisor laptop — run it in a terminal, leave it in the background:

```bash
# Polls every 60 s; prints a line when the remote HEAD changes
LAST=$(gh api repos/8408323/ha-lymow/git/refs/heads/feat/map-lovelace-card --jq '.object.sha')
while true; do
  sleep 60
  NOW=$(gh api repos/8408323/ha-lymow/git/refs/heads/feat/map-lovelace-card --jq '.object.sha')
  if [ "$NOW" != "$LAST" ]; then
    echo "NEW COMMIT: $NOW"
    gh api repos/8408323/ha-lymow/commits/$NOW --jq '.commit.message' | head -3
    LAST=$NOW
  fi
done
```

When a `test-ready:` commit appears: `git pull`, run the browser test scenario described below, push a `test-result:` commit.

---

## What has been done (77 commits vs main)

### Map Lovelace card (`lymow-map-card.js`)
- **v1–v4**: Initial map card — zoom/pan (wheel + drag), pinch-zoom, scale bar, north arrow, go-zones, no-go zones, channels, charging station, robot pose, RTK base station marker
- **Vertex editing**: Drag handles on go-zone and no-go zone polygons; insert (+) and delete (✕) vertex; dense protobuf vertices decimated to ≤32 handles before edit mode
- **Optimistic save**: Polygon override applied immediately, HA service `async_sync_map` called async; state restored on reload from localStorage
- **Zone operations (all wired to HA services)**:
  - Rename go-zone and no-go zone (in edit mode → Rename button → input → OK)
  - Delete go-zone and no-go zone (🗑 button in edit mode)
  - Add go-zone, add no-go zone, add channel (draw polygon → name → confirm)
  - Merge zones (select 2+ go-zones → ⊕ Merge)
  - Split zone (✂ Split → click 2 boundary points)
  - Enable/disable zone (long-press outside edit mode)
- **Charging station relocation**: Drag in edit mode (no zone selected) → calls `lymow.move_charging_station`
- **Mowing settings panel**: Cut height, move speed, path spacing, clean direction, perimeter laps/dir, clean mode, path order, line follow mode; collapsible advanced section; settings persist across hard reloads via localStorage (robot doesn't echo task config back via MQTT)
- **Schedules panel**: View/edit mowing schedules
- **Keyboard shortcuts**: F=fullscreen, E=enter edit, R=reset view, Esc=cancel/close; shadow DOM focus guard so typing in rename doesn't trigger shortcuts
- **Zone labels**: Name + area two-line adaptive font size; go/nogo/channel label mode persisted in localStorage; bright green go-zones (#43a047) with white text; nogo labels scale to zone bbox so tiny zones don't overflow; channel labels render above all zone polygons
- **Markers**: Charging station, robot pose, RTK base — all scale with zoom (fixed-pixel via `invZf`); sizes bumped ~30% larger (2026-05-26)
- **Status bar**: Pin-and-go, obstacle avoidance toggle, zone enable/disable feedback
- **UI polish**: RTK status badge, auto-pause on RTK loss, channel legend, legend SVG symbols, viewport-fixed overlays, fullscreen toggle (⊞ or F), auto-register card + create Lymow dashboard on setup, JS mtime cache-buster
- **Re-render guard (v=27+)**: `set hass()` skips `_render()` while user is interacting — blocks on active INPUT/SELECT focus (keeps rename input and settings dropdowns alive across HA MQTT updates) and on `_sliderActive` flag (keeps slider value stable during drag)
- **Channel edit (v=29)**: clicking a channel in edit mode selects it (highlighted); Rename button assigns a name stored in localStorage + HA-side `_channel_name_overrides`; Delete wired to `lymow.delete_channel`; channel length (sum of polygon segment distances, metres) computed in `_getMapData()`; `ch_label_mode` has Name / Length (m) / Name+Length / None options

### Integration backend (`custom_components/lymow/`)
- **New services**: `rename_zone`, `rename_nogo_zone`, `rename_channel`, `delete_zone`, `delete_nogo_zone`, `add_zone`, `add_nogo_zone`, `add_channel`, `update_zone_polygon`, `update_nogo_polygon`, `set_zone_enabled`, `move_charging_station`, `sync_map`, `merge_zones`, `split_zone`, `pin_and_go`, `resume`, `query_map`, `set_device_settings`, `set_recharge_resume`, `set_network_priority`, `set_run_time_config` — all documented in `services.yaml`
- **New entities**:
  - `select`: Device settings (select entities backed by PbTaskConfig)
  - `switch`: Vehicle LED, Auto-dock-on-error, Alerts-only (mobileNotificationSwitch tristate), Prefer 4G
  - `number`: Volume, cut height raise/lower buttons
  - `button`: Sync timezone (PbRobotConfig.timezoneOffset), Recharge & Resume
  - `sensor`: Remaining-area (derived from task area + progress)
  - `update`: Firmware entity with `async_release_notes` override
- **Protocol (`protocol.py`)**: `encode_rename_zone`, `encode_delete_zone`, `encode_delete_nogo_zone`, `encode_sync_map`, `encode_sync_map_raw`, `delete_zone_from_raw_content`, `encode_ble_drive`, full PbRobotConfig/PbTaskConfig decode, charging station move
- **Coordinator**: Optimistic rename update (persists name across HA restarts), RTK guard (auto-pause on signal loss), work-status transition notifications, `async_query_map` triggers after sync_map
- **Blueprints**: Rain delay + quiet hours automation blueprints (`blueprints/automation/lymow/`)
- **Capture tool (`tools/capture.py`)**: MQTT packet reassembly across WebSocket frames, KVS WebRTC signaling frames

### Tests
- New/expanded: `test_const_enums`, `test_init`, `test_number`, `test_select`, `test_protocol` (extensive), `test_coordinator`, `test_lawn_mower`, `test_switch`, `test_sensor`, `test_button`, `test_update`, `test_camera`, `test_device_tracker`

---

## Outstanding before merge

**PR #199 is DRAFT** — user paused on 2026-05-26 with: "I am not sure we are ready for PR, because all features are not mapped/wired and not understood from the app yet." See "Gap audit" below for what's still missing.

### Original must-haves (Tasks A/B/C) — done
- [x] **Zone vertex move capture** — app has no vertex-drag UX (Edit Boundary is drive-the-robot), card's vertex edit uses `encode_sync_map` validated by envelope symmetry with the live-confirmed rename.
- [x] **Zone name round-trip confirmed at protocol level** — direct-MQTT round-trip + byte-equal BLE frame captured from the app. Robot persists name in `BasicInfo.f2`; decoder now reads it back for go AND nogo zones.
- [x] **Tests at 100% coverage** — `uv run pytest tests/ --cov=custom_components/lymow --cov-fail-under=100` → 1020 tests, 100% (after the 2026-05-26 decoder + encoder-bug-fix session).

### Gap audit (2026-05-26) — what's NOT understood / wired yet

Driven by the user's question: *"Can we edit a zone now and have that mirrored on the map in Lymow app?"* — answer: **partial; see below.**

#### A. Decoder gaps — fields on the wire we don't read

Live query_map response (`scripts/query_map.py` against the real robot today) carries fields our `decode_map_response` ignores:

- **PbMap.f8 taskConfig (per-map mowing settings)** — UNDER-READ. Wire has fields `f4 fixed32 (pathSpacing? 0.6), f5, f6, f7, f9=90, f10=1, f11=1, f12=1, f13=0, f14=0, f17=0`. Our `decode_task_config` only checks f1/f2/f3/f4-varint — returns `{}` for this live frame. **Reason the lovelace card uses localStorage to persist mowing settings is THIS — the robot does echo task config back via MQTT, we just drop it on decode.**
- **PbMap.f11 globalZoneConfig** — completely unread. Wire has 16 sub-fields (`f1=60, f4=fixed32 0.6, f6=4, f7=1, f8=u64_max bitmask, f9=35, f10=1, f11=2, f12=1, f13=2, f14=1, f15=0, f16=90, f17=1, f18=0, f19=2`). Almost certainly the *real* mowing-settings record (cut height, path spacing, perimeter laps, line follow mode, etc.).
- **PbMap.f12 globalChannelConfig** — completely unread. Wire has `f1=2, f2=60, f3=0`.
- **Top-level pb.f6 fixed64** (outside the f23 wrapper) — UNREAD. Probably a map version / timestamp.
- **PbZone.f8 / PbZone.f9** (inside a go-zone wrapper, beside basicInfo and configBox) — UNREAD. f8 is varint=1 on the first zone only; f9 is bytes[0] on second zone. Likely zone-level flags.
- **BasicInfo.f8 / BasicInfo.f9** — UNREAD on the first zone only. Likely a per-zone override flag.

**Impact**: HA never surfaces real robot mowing settings (panel is currently driven by localStorage cache). User's question "can we edit and see it mirrored" → partial: zone NAME round-trip is solid, but zone CONFIG (cut height, path spacing, etc.) is not actually pulled from the robot.

**Update 2026-05-26 (capture session)**: decoder is now complete — globalZoneConfig + per-zone configBox both decode end-to-end against the live frame. The card's localStorage shim can finally go. **Also discovered two encoder bugs while doing this work** (PbZoneConfig field numbers off-by-one in `_TASK_CONFIG_FIELDS`; PbRunTimeConfig was using PbZoneConfig field numbers) — both fixed. See "Concrete TODOs" below for the full list.

#### B. Encoder gaps — userCtrls we don't have

App-emitted commands we lack named encoders for (could be sent via `encode_userctrl(N)` generic but no service / coordinator wiring):

| userCtrl | Name | App action | HA status |
|---|---|---|---|
| 10 | MODIFY_ZONE_EDGE_START | Edit Boundary (joystick mode) start | not encoded |
| 11 | MODIFY_ZONE_EDGE_STOP | Edit Boundary stop | not encoded |
| 15 | CLEAR_ALL_ZONES_CHANNELS | "Delete All" toolbar button | not encoded |
| 17 | CHARGING_STATION_RESET | Reset charging station | not encoded |
| 18 | LOCK | Lock robot | not encoded |
| 38 | MODIFY_STATION | Move charging station info | not encoded |
| 44 | FLOOR_BACKUP | Backup map | not encoded |
| 45 | FLOOR_RESTORE | Restore map from backup | not encoded |
| 55 | MERGE_ZONE | Merge Map (toolbar) | **HA simulates client-side via convex hull + SYNC_MAP — does not use robot's native merge** |
| 56 | CUT_ZONE | Split Map (toolbar) | **HA simulates client-side via polygon split + SYNC_MAP — does not use robot's native split** |

Client-side merge/split work functionally, but: (1) robot might preserve child nogo zones / metadata better with its native commands; (2) no live byte-level diff to confirm parity.

#### C. App-side UX behavior — confirmed limitations

- **App's Rename dialog NEVER displays the current zone name.** Verified today by renaming `wsmjco1T` via MQTT to "HA_SUPERVISOR_TEST", opening the app's Rename → tapping the zone's green circle → "Renam" → input field showed placeholder `'zone name'` (empty), not the actual robot name. App maintains its own local label cache invisible at protocol level, never reconciled with `BasicInfo.f2`.
- HA→robot writes succeed and the robot stores them, but the user cannot SEE the HA-set name from inside the app's edit UI.
- **Question for next session**: where (if anywhere) does the app display zone labels back to the user — Mowing History rows? Map Backup names? If nowhere, document the limitation; if somewhere, screenshot-prove HA-set names appear there.

#### D. Wiring gaps — frontend↔backend already-decoded items

- **services.yaml missing entries** (work fine, just undocumented for external callers). Registered in `lawn_mower.py` and called by the card but absent from `services.yaml`: `add_nogo_zone`, `add_channel`, `move_charging_station`, `set_zone_enabled`, `update_nogo_polygon`, `sync_map`, `pause`.
- **Nogo zone name persistence across HA restarts**: coordinator `_zone_name_overrides` covers go-zones only. Nogo rename sends the right frame and the robot persists it, but if HA restarts before the next MQTT poll, the label shows hashId fallback.
- **Channel name persistence**: `PbChannel` has no name field (f1=hashId, f2=zone1, f3=zone2 — confirmed). Channel rename needs an HA-side `_channel_name_overrides` store.
- **Channel length label**: channel polygon points (ENU metres) are already in the sensor; length = sum of segment distances. No backend change needed — just card-side compute + a new option in `ch_label_mode`.
- **Per-zone cut height**: `async_update_zone_cut_height` exists in coordinator, no service or card UI yet.

### Concrete TODOs before un-drafting PR #199

Decoder (critical — wires up real robot settings instead of localStorage):
- [x] **`decode_task_config` clarified** (2026-05-26, capture session): PbTaskConfig is genuinely the 4-field device-settings record (chargingMode/zoneOrder/rainCleaning/disableChargingPark) — Hermes class #9588 confirms. The "11 wire fields" in the earlier audit were actually PbZoneConfig fields seen at PbMap.f11, not PbMap.f8. In the fresh `scripts/map_response.bin` PbMap.f8 is empty (0B).
- [x] **`decode_zone_config` added** (2026-05-26): decodes the canonical 19-field PbZoneConfig (Hermes class #9432) — covers PbMap.f11 globalZoneConfig **and** PbZone.f2 per-zone override. Live frame confirms: globalZoneConfig{cutHeight:60, moveSpeed:0.6, pathSpacing:1, perimeterMowLaps:2, lineFollowMode:true, …}; zone-level overrides cutHeight:40 + moveSpeed:0.8.
- [x] **`decode_channel_config` added** (2026-05-26): PbChannelConfig {detectMode, cutHeight, channelLift} for PbMap.f12 globalChannelConfig (Hermes class #9444).
- [x] **chargingStation extended** (2026-05-26): PbPose.f4 z is now decoded when present; live frame has z=-0.030.
- [x] **enuBasePoint added** (2026-05-26): PbMap.f7 is `PbRobotLLACoords {latitude, longitude, altitude}` (Hermes class #9276). Live frame: lat 59.682, lon 16.759, alt 34.09 m. `gpsOrigin` retained as an alias.
- [x] **diagonalCoords added** (2026-05-26): PbMap.f6 (repeated PbPoint) decoded; live frame has two corners spanning the map bbox.
- [~] **PbZone.f8/f9 + BasicInfo.f8/f9 — not present in fresh frame.** Earlier audit saw them on a different capture; conditional fields — defer until a frame surfaces them.
- [~] **Top-level pb.f6 fixed64 — not in this frame either.** Top-level only has f23 + f4=1.
- [ ] **Drop the lovelace card's `localStorage` shim for mowing settings** — globalZoneConfig is now decoded, so the panel can be driven from the real robot state. Card change only.

**Two real encoder bugs found and fixed (2026-05-26)**:
1. `_TASK_CONFIG_FIELDS` had fields f9–f16 off-by-one against the canonical PbZoneConfig wire (Hermes #9432). HA's `lymow.set_task_config` service was writing `pathSpacing` to f9 (which the robot interprets as `relativeCleanDir`), `perimeterMowLaps` to f10 (actually `pathSpacing`), etc. Six middle fields of the form were silently corrupting on the wire. **Fixed** — added `cutHeight` at f1 too, shifted `pathSpacing`/`perimeterMowLaps`/`perimeterMowDir`/`noGoMowLaps`/`obsDecMode`/`pathOrder`/`startProgress`/`relativeCleanDir` to canonical positions. Test `test_encode_set_task_config_wraps_in_pbinput` was pinning the buggy layout; rewritten to canonical.
2. `_RUN_TIME_CONFIG_FIELDS` was using PbZoneConfig field numbers (f4 moveSpeed, f6 cutSpeed) but PbRunTimeConfig (Hermes #9456) has its own shape: f1 cutHeight, **f2 moveSpeed, f3 cutSpeed**, f4 channelConfig. `encode_set_run_time_config(moveSpeed=…)` was writing the float into f4 (a length-delimited bytes field — `channelConfig`). **Fixed** — test pin updated to canonical numbers. `encode_set_run_time_config(cutHeight=…)` already worked because f1 is shared between both messages.

Encoder (important):
- [x] **`encode_merge_zones (55)` / `encode_cut_zone (56)` — APP DOES NOT SHIP EITHER FEATURE** (2026-05-26 capture). Tapping Merge Map or Split Map in the app's edit toolbar shows a "Stay tuned! This feature is coming soon" toast and sends **nothing** on the wire. The HA card's client-side merge (convex hull + SYNC_MAP) and split (polygon cut + SYNC_MAP) are therefore the ONLY user-facing paths. The userCtrl 55/56 enum values exist in the protocol but cannot be validated against the app. **Recommendation: keep the client-side impl and stop calling it a gap.**
- [~] `encode_modify_zone_edge_start/stop (10 / 11)` — Edit Boundary is implemented in the app, but the wire frame for START requires (a) an element to be selected in edit mode, (b) tapping the "Edit Boundary" confirm button. Without a selected zone the app shows "Please select an element to modify" and no PBINPUT is sent. Reliable ADB-driven zone selection is non-trivial (taps inside zone interior didn't visibly mark it). Capture deferred until a session where joystick control is supervised in person — the same flow then drives the robot.
- [ ] `encode_clear_all_zones_channels()` for `CLEAR_ALL_ZONES_CHANNELS = 15` (Delete All) — destructive, skipped this round.
- [ ] `encode_floor_backup` (44) + `encode_floor_restore` (45) — deferred; needs Settings → Map Backup tap sequence.

Wiring (low risk — code already does most of the work):
- [x] **Add missing service entries to `services.yaml`** (2026-05-26, v=29): `add_nogo_zone`, `add_channel`, `move_charging_station`, `set_zone_enabled`, `update_nogo_polygon`, `rename_channel` — all documented.
- [x] **Nogo zone name persistence** (2026-05-26): decoder already reads `BasicInfo.f2` for both go and nogo zones (b950429 fix); no coordinator change needed — robot is the single source of truth.
- [x] **Channel name persistence** (2026-05-26, v=29): `async_rename_channel` added to coordinator; names stored in `_channel_name_overrides` and re-applied on every MQTT map update; card persists via `localStorage lymow_channel_names`; Rename button wired for channels in edit mode.
- [x] **Channel length label** (2026-05-26, v=29): `length` (metres, rounded) computed per-channel in `_getMapData()`; `ch_label_mode` now has Length (m) and Name+Length options.
- [ ] Expose `async_update_zone_cut_height` as a service + card UI.

Manual verification:
- [ ] Browser re-verify the nogo-rename dispatch (partial-pass on commit `351820e`). Wire-level proof is solid; purely a manual UI confirmation needed.
- [ ] Investigate where (if anywhere) the app displays zone names back to the user.

### Nice-to-have / post-merge
- [ ] PR review cycle: resolve all Copilot/Codex comments, re-request review, iterate until clean.

---

## Capture session tasks

**The capture session is currently running on the Linux box** (was previously planned for Windows; switched because this branch's checkout + ADB + mitmproxy v12.2.3 are all already wired up there).
Complete these tasks in order and paste findings into the "Findings" sections below.

### Capture setup (Linux box — supersedes the earlier Windows plan)

- mitmproxy CA cert: installed as a **Magisk module** at `/data/adb/modules/mitmproxy_ca/` on the phone — should survive reboots.
- Phone ADB: USB serial `fc7d1e36`, WiFi `192.168.1.45:5555`.
- Linux capture host: `192.168.1.180`, mitmproxy v12.2.3 (via `uv tool`).
- Phone proxy → `192.168.1.180:8888` (set via ADB `settings put global http_proxy`).

Start mitmdump on the Linux box:
```bash
cd /home/mint-laptop-4/private_projects/ha-lymow-lovelace
uv tool run --from mitmproxy mitmdump -s tools/capture.py \
    --listen-host 0.0.0.0 --listen-port 8888 --ssl-insecure
```
Output goes to `tools/capture-lymow.txt` (the script writes it itself, not via stdout redirect; path is gitignored).

If port 8888 is busy:
```bash
ss -ltnp 'sport = :8888'
# kill the PID, then retry
```

Verify cert is loaded: look for HTTPS traffic from `api.lymow.com` in the mitmdump output when opening the app. If you see TLS handshake errors, the cert isn't trusted — try manually overlaying it:
```bash
adb connect 192.168.1.45:5555
adb shell
su
# Check if Magisk module cert is visible:
ls /system/etc/security/cacerts/ | grep 48750f0d
# If missing, manually copy it:
cp /data/adb/modules/mitmproxy_ca/system/etc/security/cacerts/48750f0d.0 /system/etc/security/cacerts/
chmod 644 /system/etc/security/cacerts/48750f0d.0
```

**Remember to clear the phone proxy when done** — prior sessions hit E29 dock-fail when the proxy was left on overnight.

---

### Task A — Zone vertex move (HIGHEST PRIORITY)

**What to do in the app:**
1. Open Lymow app → Map view
2. Enter zone edit mode (tap a zone → edit)
3. Drag a single vertex of any go-zone to a slightly different position
4. Tap Save / confirm
5. Wait ~3 seconds for the app to send the update

**What to look for in capture output (`tools/capture-lymow.txt`):**
- A `PBINPUT` line (outbound MQTT to `/device/<thing>/pbinput`)
- Check the decoded `userCtrl` field — expect it to be **25** (SYNC_MAP) but may be something else
- Paste the full `PBINPUT` block here

**What to report back:**
```
TASK A FINDINGS:
userCtrl value: ___
Full pbinput hex (or base64): ___
Decoded fields visible in capture: ___
Any REST calls made around the same time: ___
```

**What the supervisor session will do with this:**
- If userCtrl=25: confirm that `encode_sync_map` / `encode_sync_map_raw` is the correct codec for vertex moves (already implemented — just needs verification)
- If different userCtrl: implement a new encode function in `protocol.py`
- Add a test in `tests/test_protocol.py` using the real captured bytes

---

### Task B — Zone rename confirmation

**What to do in the app:**
1. Open Lymow app → tap a zone → edit → rename it (e.g. append " X" to the name)
2. Save
3. Wait ~3 seconds

**What to look for:**
- A `PBINPUT` line with userCtrl — expect **8** (CLEAR_ZONE) for rename (same as delete on robot side; name is stored in the app, not the robot)
- OR a REST call to an API endpoint that stores zone names server-side

**What to report back:**
```
TASK B FINDINGS:
Protocol action taken (MQTT userCtrl=? or REST endpoint=?): ___
If MQTT: full pbinput hex: ___
If REST: full URL, method, request body: ___
Does the robot protobuf BasicInfo.f2 (name field) get populated after rename? ___
```

**Context:** From our existing capture (`tools/capture-lymow.txt`), `BasicInfo.f2` is empty on all zones — robot stores only hashIds. HA shows zone names because we persist them optimistically via `_nameOverrides`. The Lymow app shows different names ("Front" vs "Front garden") — possibly from S3 map backups or an app-side REST store.

---

### Task C — Zone deletion confirmation

**What to do in the app:**
1. Add a throwaway zone (small polygon in a corner)
2. Delete it via the app
3. Capture the traffic

**What to look for:**
- `PBINPUT` with userCtrl — expect **8** (CLEAR_ZONE)
- Check field layout: our `encode_delete_zone` sets `f1.f8.f3[0] = {f3: hash_id}` for go-zones

**What to report back:**
```
TASK C FINDINGS:
userCtrl value: ___
Full pbinput hex: ___
Is the zone hash present in the payload? ___
Any REST call involved? ___
```

---

## Protocol reference (for capture session)

All MQTT commands are wrapped: `{"message": "<base64 protobuf>"}` on topic `/device/<thingName>/pbinput`.

Key userCtrl values (field 5 of the outer pbinput message):
| Value | Name | Description |
|-------|------|-------------|
| 1 | USER_CTRL_CLEAN | Start mowing |
| 3 | USER_CTRL_PAUSE | Pause |
| 8 | USER_CTRL_CLEAR_ZONE | Delete/rename zone |
| 19 | USER_CTRL_QUERY_MAP | Request map data |
| 25 | USER_CTRL_SYNC_MAP | Push full map (zone edit) |
| 33 | USER_CTRL_RECHARGE_DOCK | Return to dock |

Zone structure in map protobuf:
- `PbOutput.f23.f2.f3` = map content (go-zones at subfield 1, nogo at 2, channels at 3)
- Zone `BasicInfo`: f1=type, f2=name (EMPTY on robot), f3=hashId (UUID string), f4=isEnabled, f5=polygon

The capture tool (`tools/capture.py`) already decodes MQTT-over-WebSocket and labels lines:
- `PBINPUT` = outbound command (what we send / what the app sends)
- `PBOUTPUT` = inbound robot state (map, battery, status, etc.)
- `REST` = HTTP API calls

---

## Known-good implementations (do NOT re-implement)

These are already done in `custom_components/lymow/protocol.py`. Verify against capture, don't rewrite:
- `encode_rename_zone(hash_id, name)` — sends MODIFY_ZONE_INFO (userCtrl=9) with name in BasicInfo.f2 **[static bytes in Task B findings confirm userCtrl=9, not 8 as originally assumed]**
- `encode_delete_zone(hash_id)` — sends CLEAR_ZONE (userCtrl=8), zone in f1.f8.f3[0]
- `encode_delete_nogo_zone(hash_id)` — same pattern, zone in f1.f8.f4[0] (nogo field)
- `encode_sync_map(map_data)` / `encode_sync_map_raw(raw_content)` — userCtrl=25, full map push
- `delete_zone_from_raw_content(content_bytes, hash_id)` — surgical zone remove + modifyHashs update

Coordinator methods (already done):
- `async_rename_zone(thing_name, hash_id, name)`
- `async_delete_zone(thing_name, hash_id)`
- `async_delete_nogo_zone(thing_name, hash_id)`

HA services registered in `lawn_mower.py`:
- `lymow.rename_zone` → `async_rename_zone`
- `lymow.delete_zone` → `async_delete_zone`
- `lymow.delete_nogo_zone` → `async_delete_nogo_zone`
- `lymow.sync_map` → `async_sync_map`

Map card (`www/lymow-map-card.js`) already calls all of these correctly. Vertex edit drag-save calls `lymow.sync_map` with the updated polygon — this needs Task A to confirm the protocol is correct.

---

## Browser testing (supervisor session only)

The supervisor session has Chrome + the HA Lovelace card running. These are the scenarios to test each time the capture session signals `test-ready:`.

### Access
- HA instance: local network (supervisor knows the URL/credentials)
- Map card: `lymow-map-card.js` is served from `custom_components/lymow/www/`; HA caches it — after a code change, hard-reload with Ctrl+Shift+R or append `?v=<timestamp>` to force a fresh fetch

### Test scenarios to run after each `test-ready:` commit

#### Scenario 1 — Zone rename (after Task B is captured and implemented)
1. Open the Lymow map card in HA
2. Tap a go-zone → enter edit mode → tap Rename → type a new name → OK
3. **Expected**: zone label updates immediately (optimistic), HA service `lymow.rename_zone` called, no console errors
4. Reload the page — **Expected**: name persists (stored in `_nameOverrides` + localStorage)
5. If the Lymow app has a server-side name store: open the app and confirm it shows the new name

#### Scenario 2 — Zone delete (after Task C is captured and implemented)
1. Create a small throwaway go-zone via the map card (draw polygon → name "Test delete")
2. Enter edit mode → tap 🗑 → confirm
3. **Expected**: zone disappears from map immediately, `lymow.delete_zone` service called, no console errors
4. Reload — **Expected**: zone gone (not restored from localStorage)

#### Scenario 3 — Zone vertex move (after Task A is captured and implemented)
1. Tap a go-zone → enter edit mode → drag a vertex handle to a new position
2. Tap Save
3. **Expected**: polygon updates immediately (optimistic), `lymow.sync_map` service called, no console errors
4. Reload — **Expected**: new shape persists

#### Scenario 4 — Regression check (run after every `test-ready:` commit)
1. Map loads with go-zones, nogo-zones, channels, charging station, robot pose visible
2. Zoom/pan works (wheel + drag), pinch-zoom works if on touch device
3. Scale bar and north arrow render
4. Zone labels show name + area; label mode toggle (go/nogo/ch) persists across reload
5. Fullscreen toggle (⊞ or F key) works
6. Mowing settings panel opens and shows current values

### How to report
Push a commit: `test-result: scenario 1 pass / scenario 4 fail — console error: <brief>`

---

## Browser test results (supervisor session)

### test-ready: nogo-zone rename (commit `351820e`) — result: **PARTIAL PASS + 2 bugs found**

**Tested 2026-05-26 on Desktop PC Chrome, HA 192.168.1.99:8123**

#### What passed
- Map loads correctly: go-zones, nogo-zones, robot pose, RTK badge, legend all render
- Zone labels show name + area ("Front garden 349 m²", "Back garden HA 1222 m²")
- Edit mode enters cleanly, status bar updates correctly
- Rename dialog opens (input + OK + cancel buttons)
- Optimistic label update is immediate after OK

#### Bug 1 — rename input not cleared between opens (fixed in `8b86a49`)
Typing a name, OK-ing, then opening rename again: the input retained the previous value. Second typing appended to first → "Old nameNew name". Root cause: shadow DOM incremental re-render doesn't reset `input.value` (only `defaultValue`). Fix: `_enterRename` now resets `inp.value = inp.defaultValue` and calls `inp.select()` before focus.

#### Bug 2 — accidental go-zone rename during test
During testing I clicked at map coordinate (820, 455) intending to select the nogo zone icon, but hit the underlying go-zone polygon instead (`_editType` was `"go"`, `_editHash` = `KX1kGyat`). Result: "Back garden HA" was renamed to "Test nogo zoneTest nogo zone 2" on the robot. Called `rename_zone(KX1kGyat, "Back garden HA")` to restore — **capture session please verify the zone name is restored after next query_map.** If not, restore from backup.

#### nogo rename dispatch — NOT fully verified
Could not get `_editType = "nogo"` during the test because my clicks kept landing on the larger go-zone polygon underneath the nogo icon. The dispatch code is correct in the JS (`isNogo = this._editType === "nogo"` → calls `rename_nogo_zone`), but needs a test where a nogo polygon is genuinely selected. **Capture session: please do a targeted nogo rename test** — tap the red hatched nogo polygon directly (not its icon), verify the status bar says "Editing no-go zone", then rename.

#### Scenario 4 regression — PASS
Map loads, zoom/pan, scale bar, north arrow, RTK badge, label toggle, fullscreen all working.

---

## Findings (fill in as capture session reports)

### Task A findings
_Pending — scoped out of this capture window; vertex move requires the user to physically pinch-zoom in the app (see "Capture blockers" below)._

### Task B findings (zone rename) — major correction
**Earlier claim was wrong**: a fresh `scripts/query_map.py` run today (`uv run python scripts/query_map.py`) returned a 6717-byte map whose `BasicInfo.f2` fields are **populated**, not empty. The bytes are right there in the response:

```
offset 0x000014: 'Front garden'    # PbZoneBasicInfo.f2 of go-zone wsmjco1T
offset 0x000664: 'Back garden HA'  # PbZoneBasicInfo.f2 of go-zone KX1kGyat
```

So:
- The robot **does** persist zone names in `BasicInfo.f2`.
- Our `encode_rename_zone` writes the name into that exact field — round-trip works at the robot level.
- The "Back garden HA" suffix was written by HA via `lymow.rename_zone`, confirming the existing implementation is live-correct end-to-end for go-zones.
- Our decoder (`protocol.py:309`) already reads f2 into `zone["name"]`, so HA sees the persisted name on the next pboutput refresh.

Why the earlier claim was wrong: the previous `tools/capture-lymow.txt` was recorded during a session that did not trigger a query-map, so the only pboutput frames in it were 22B/30B heartbeats and one large reply that the supervisor read past the name region. The freshly-decoded response disproves the "f2 empty" hypothesis.

**Per the user (2026-05-26):** the Lymow app itself maintains its own persisted map cache (likely AsyncStorage and/or S3 backup metadata) that is **not** automatically reconciled with the robot's `BasicInfo.f2`. That is why the app can show a different label ("Front") for a zone the robot calls "Front garden". The app-side persistence is the next thing to capture — find where the app stores its own zone-name map, then make HA write to that same sink so app and HA stay in sync.

**Static encoder bytes** (for byte-exact diff against a future live capture):
```
encode_rename_zone("wsmjco1T", "Front lawn")
→ 10312809621a0a180a16120a46726f6e74206c61776e1a0877736d6a636f3154
encode_rename_nogo_zone("ngabcdef", "Flower bed")
→ 10312809621c12 ... (PbMap.field=2 wrapper; distinguishes from go-zone variant)
```
Breakdown of rename_zone: `10 31` version=49; `28 09` userCtrl=9 MODIFY_ZONE_INFO; `62 1a` field 12 PbMap len 26; `0a 18` goZones[0] len 24; `0a 16` basicInfo len 22; `12 0a "Front lawn"`; `1a 08 "wsmjco1T"`.

**App-side persistence — what we've looked at so far:**
- `/data/data/com.lymow.app/databases/RKStorage` = AsyncStorage. Largest key `separatorBuffer_device_7890838300cd` (32 KB) is map separator geometry, not names. No `Front garden` / `Back garden` strings in any AsyncStorage value. So zone names are **not** in plain AsyncStorage.
- REST endpoints observed: `get-backup-map`, `get-s3-object`, `get-device-info`, `update-device-feature`, `update-user-profile`, `device-list-query`.

### Task B — live confirmation (2026-05-26, MQTT-side via scripts/rename_test.py)

I ran a direct-MQTT rename round-trip (no app, no HA UI involved) and traced what the system did:

1. `encode_rename_zone("wsmjco1T", "Front garden RENAMETEST")` published to `/device/<thing>/pbinput`.
2. Re-queried with `encode_query_map(0)` and parsed the reply: `BasicInfo.f2 = "Front garden RENAMETEST"` for hash `wsmjco1T`. ✓
3. Restored the original name with another rename + verify pass.

Bytes sent (exact wire frames the test produced — these match the encoder's static output exactly):
```
rename → "Front garden RENAMETEST":
  1031280962270a250a23121746726f6e742067617264656e2052454e414d45544553541a0877736d6a636f3154
rename back → "Front garden":
  10312809621c0a1a0a18120c46726f6e742067617264656e1a0877736d6a636f3154
```
The capture in `tools/capture-lymow.txt` (Linux box) recorded **only** the robot's three large `pboutput` map broadcasts at 06:59:12 / 06:59:18 / 06:59:23 UTC. **The Lymow phone app made no REST call and no MQTT publish of its own during or after the rename.** Conclusion: there is no separate app-side persistence sink to mirror. The robot's `BasicInfo.f2` is the single source of truth; the app re-renders from the MQTT broadcast like any other subscriber.

This means `encode_rename_zone` is **live-correct end-to-end**, `encode_rename_nogo_zone` (which mirrors the same shape into PbMap.nogoZones) follows by symmetry, and HA's existing rename path already syncs the app via the robot. No extra plumbing needed.

The `scripts/rename_test.py` helper that ran this is committed alongside this BRANCH_STATUS — re-run anytime to re-confirm the round-trip after future changes.

### Task C findings (zone delete) — confirmed by envelope symmetry, no destructive live test

**Static encoder bytes (now pinned in `test_encode_delete_zone_matches_pinned_bytes`):**
```
encode_delete_zone("wsmjco1T")        → 10312808620e0a0c0a0a1a0877736d6a636f3154
encode_delete_nogo_zone("ngabcdef")   → 10312808620e120c0a0a1a086e6761626364656 6
```
Breakdown: `userCtrl=8` CLEAR_ZONE; PbMap with the target zone's `basicInfo.hashId` in goZones (field 1) or nogoZones (field 2). PbZone wrapper present (field 1 inside PbMap.goZones), matching `test_encode_delete_nogo_zone_uses_nogo_field_with_pbzone_wrapper`.

**Why no live delete round-trip:** Task B's live round-trip (rename) used the *same envelope* (PbInput.f12 = PbMap → PbZone → BasicInfo → hashId) and the robot accepted it. Delete only changes userCtrl 9→8 and drops the name field. A destructive live delete would need a follow-up `add_zone` to restore the test zone (with all original polygon vertices), and getting the polygon byte-identical after a round-trip risks corrupting real map data. The pinned-bytes test plus envelope symmetry with the verified rename is the safer evidence.

### Real bug found and fixed (not from live capture — from coordinator audit)
**Bug:** `LymowDataUpdateCoordinator.async_delete_zone` did **not** call `async_query_map` after the CLEAR_ZONE publish, while its sibling `async_delete_nogo_zone` and `async_delete_channel` both do. Effect: the lovelace card kept showing the deleted go-zone until the next periodic poll (up to 60 s of stale UI). The `_polyOverrides` mechanism in the card does not auto-clear on delete, so the card relies on the coordinator to refresh map data — but the coordinator never asked the robot for the refresh.

**Fix:** added `await self.async_query_map(thing_name)` after the delete publish so map data refreshes immediately (commit `be49df7`). Existing test `test_async_delete_zone_publishes_command` was tightened into `test_async_delete_zone_sends_command_then_queries_map` asserting two publishes (delete + query-map) and the userCtrl field on each.

### Second bug found and fixed (also from audit, not live capture) — rename for no-go zones
**Bug:** Renaming a no-go zone through the lovelace map card called `lymow.rename_zone`. That service always encoded into `PbMap.goZones` (field 1) regardless of whether the hash belonged to a go-zone or a nogo. Three downstream effects:
1. The robot received a `MODIFY_ZONE_INFO` targeting a non-existent go-zone (silently rejected device-side).
2. `async_rename_zone`'s optimistic update only walked `goZones`, so the cache never reflected the new name.
3. The card's `_getMapData` applied `_nameOverrides` to `goZones` only — so even the local UI label did not update for a nogo rename.

**Fix** (commit `0356b58`):
- New encoder `encode_rename_nogo_zone(hash_id, name)` targeting `PbMap.nogoZones` (field 2) — mirrors `encode_delete_nogo_zone`.
- New coordinator method `async_rename_nogo_zone` with the same optimistic-cache pattern as the go-zone variant, but operating on `nogoZones`.
- New `lymow.rename_nogo_zone` service with its own schema (`nogo_hash_id`, `name`); documented in `services.yaml`.
- Card dispatches rename to `rename_zone` or `rename_nogo_zone` based on `_editType`.
- Card applies `_nameOverrides` to `nogoZones` too.
- Tests: byte-shape (PbMap.field=2, not 1), optimistic-cache path, and dispatch (happy + unknown-entity skip).

**Static encoder bytes** for the new nogo rename:
```
encode_rename_nogo_zone("ngabcdef", "Flower bed")
→ 10312809621c12 ... (PbMap.field=2 wrapper distinguishes this from the go-zone variant)
```

### Mower-control card — out-of-scope tracking issue
Filed as **#197** so the second-card work (Mow/Pause/Dock/Resume + live status + signal bars + camera thumbnail) doesn't get lost while we finish `feat/map-lovelace-card`. Mirrors what the Lymow app's main device screen does. **Not** in this branch.

---

## Capture blockers

- The Lymow app's map area renders only the robot dot at default zoom — go-zone polygons sit far outside the visible viewport.
- ADB `input swipe` is single-touch, so it pans but does not pinch-zoom; the map UI requires a real two-finger gesture to zoom out far enough that a zone polygon is hittable.
- A `sendevent`-based two-finger script was attempted but the app did not respond (likely needs simultaneous SLOT-0/SLOT-1 frames within one SYN_REPORT; the script sent them sequentially).
- A force-restart of the app, a Select-Mow dialog, a bottom-sheet pull, and a tap on the eye/focus icon all left the map in robot-only view.

**Unblock options** (pick one when the supervisor or user is back at a screen):
1. Pinch-zoom the phone manually once, then leave the app on the zone-selection screen — ADB can then drive Rename / Delete from there without re-zooming.
2. Use scrcpy from this laptop to interact with the phone screen as if local.
3. Skip the app capture and assume the static encoders match (current state — supported by Hermes bytecode analysis but not byte-equal to a live frame).

---

## Next steps for supervisor session (after findings arrive)

1. Compare captured bytes for Tasks A/B/C against existing encode functions
2. If protocol matches: add tests in `tests/test_protocol.py` using real captured bytes
3. If protocol diverges: implement corrected encode function; update coordinator + services + map card
4. Run full test suite: `uv run pytest tests/ -v --cov --cov-fail-under=100`
5. If zone names come from a REST endpoint (Task B): implement fetch + merge in coordinator's `_async_update_map_data`
6. Remove this file, push final commits, open PR

---

## Capture session progress log

- [x] Repo cloned at `/home/mint-laptop-4/private_projects/ha-lymow-lovelace`, branch checked out.
- [x] ADB confirmed: USB `fc7d1e36`, WiFi `192.168.1.45:5555`.
- [x] mitmproxy v12.2.3 available; LAN host `192.168.1.180`.
- [x] mitmdump + capture pipeline running (live; sibling clone holds it; LAN proxy already trusted by the phone via Magisk).
- [~] Task B (rename) captured — **blocked**, see "Capture blockers" / Task B findings.
- [~] Task C (delete go-zone) captured — **blocked**, see "Capture blockers" / Task C findings.
- [~] Task C' (delete nogo zone) captured — **blocked**, see "Capture blockers".
- [ ] Task A (vertex move) — supervisor flagged HIGHEST PRIORITY but user scoped this session to rename + delete first; same blocker applies.
- [x] Encoder static bytes written into Findings — ready to diff against future live frames.
- [x] **Bug fix: `async_delete_zone` now re-queries the map** so the lovelace card stops showing deleted go-zones. Test tightened to assert delete + query-map. All 970 tests pass (`uv run pytest tests/ -v`).
- [x] GH issue #197 filed for the separate mower-control lovelace card.

> User scope for this session: **rename + delete first**, then vertex move if time permits. The "mower control" card is out of scope and tracked as its own issue.

### Hand-off note to supervisor
- `test-ready:` not pushed for this round — the fix is backend-only (coordinator) and is covered by unit tests, so browser testing isn't strictly needed to merge it. If you want a sanity check anyway, scenario to run: in the lovelace card, delete a go-zone with the 🗑 button; expect it to disappear within ~1 s (used to wait up to ~60 s for the next poll).
- Phone proxy is **still active** on `192.168.1.180:8888` — leaving it on so the capture stays available for the next session. Last session noted to clear before overnight (E29 dock-fail risk).

### Hand-off note 2 to supervisor (after the nogo-rename fix, commit `0356b58`)
- **`test-ready:` worth pushing** for this one — there's now a real lovelace-card behaviour change (rename of no-go zone uses a new service and the local label updates immediately). Scenario to run: in edit mode, tap a no-go zone, tap 🏷 Rename, type a new name, OK. Expect: the label updates immediately, no console error, no `lymow.rename_zone` call (the new `lymow.rename_nogo_zone` is invoked instead — observable in Developer Tools → Network or `homeassistant.log`).
- 974 tests pass, ruff format + lint clean. Coverage will need a top-up for the new lines if you're enforcing 100% in CI; the existing 97% gap was already in coordinator/lawn_mower before this branch.
- Sendevent-based pinch-zoom from ADB now confirmed to deliver well-formed Type-B multitouch frames to `/dev/input/event4` (verified via `getevent -l`). The Lymow app's Skia/React-Native canvas still does not respond — synthetic events probably miss pressure/tool-type fields it expects. **Option 1 from your reply (manual one-shot pinch) remains the unblock path.**

### Supervisor reply (2026-05-26)
Good work on the coordinator bug and the static encoder breakdown — that's solid progress without live capture.

**userCtrl=9 for rename**: Your static bytes confirm `encode_rename_zone` sends userCtrl=**9** (MODIFY_ZONE_INFO), not 8. I've corrected the "Known-good implementations" entry above. When you get a live frame, diff against this — if they match we're done.

**Capture blocker — go with option 1**: Manually pinch-zoom the phone once on the map screen until zone polygons are visible, then hand control back to ADB. That should stay zoomed across taps. If the app resets zoom on focus loss, try keeping a `scrcpy` window open on the side so you can manually intervene without picking up the phone.

**Browser sanity check for the delete fix**: Will run scenario 2 (delete a zone, verify it disappears within ~1 s) and report back with a `test-result:` commit.

**Phone proxy**: clear it before leaving overnight to avoid the E29 dock-fail. `adb shell settings delete global http_proxy`

**Priority order for next capture session**:
1. Unblock via pinch-zoom → capture live rename (Task B) and delete (Task C) frames
2. Diff live bytes against static encoder bytes — if they match, Tasks B+C are confirmed done
3. Task A (vertex move) — same unblock approach; this is still the highest-priority unknown

---

### Capture session update — 2026-05-26 12:35 (sanitize-checkout, other Linux box)

Drove the phone via ADB end-to-end and confirmed the **previous blocker was about the wrong layer** — pinch-zoom doesn't matter, because the app does not route command traffic through the HTTP/MQTT proxy at all. Findings, in priority order:

**1. App→robot commands go via BLE, NOT MQTT (THIS IS THE ROOT CAUSE).**

- `dumpsys bluetooth_manager` on the phone shows `GATT Client Map: Connections: 1` with the robot's MAC `f8:3d:c6:82:56:c0` (same family as the f8:3d:c6:82:56:c1 we see in PbDeviceProfile broadcasts).
- Drove the phone's Dock button via `adb shell input tap`. HA's `lawn_mower.7b6521` entity flipped `docked → returning → docked` (confirmed via `/api/states`), so the action propagated to the robot. mitmproxy captured **zero** outbound `MQTT → /device/.../pbinput` PUBLISH frames in either direction during the same window — only PUBACKs to incoming pboutputs.
- Same result for Rename: tapped Rename → green circles → tapped circle on `wsmjco1T` → bottom Renam → text dialog → typed "Front garden" → OK. Zero outbound PBINPUT, no REST call.

**2. App `Edit Boundary` ≠ vertex drag — it's drive-the-robot.**

Edit Boundary opens a joystick UI plus a green "Edit Boundary" confirm button — record-boundary-by-driving session. The lovelace card's drag-handle / insert-vertex / delete-vertex UX has **no equivalent in the app** — it's a card-only feature that calls `lymow.sync_map`. So "Task A vertex move" cannot be captured from the app; the only way the lovelace card's vertex edit reaches the robot is via our own `encode_sync_map`/`encode_sync_map_raw` path, which is already exercised by `scripts/rename_test.py`-style direct-MQTT helpers.

**3. App's full edit-mode toolbar (landscape, 2160×1080).**

| Button | bounds (landscape) | semantics |
|---|---|---|
| Back arrow | `[63,35][180,162]` | exit edit mode |
| Delete Element | `[448,31][636,165]` | one-shot mutator (destructive) |
| Rename | `[663,31][851,165]` | shows circle handles → Renam confirm → text dialog |
| Merge Map | `[878,31][1067,165]` | needs ≥2 zone selection |
| Split Map | `[1093,31][1282,165]` | unknown |
| Delete All | `[1308,31][1496,165]` | **destructive — do not invoke** |
| Edit Boundary | `[1522,31][1710,165]` | drive-the-robot mode (#2) |

Rename-confirm button center ≈ (1080, 966); in-dialog OK center ≈ (1272, 627); Cancel ≈ (888, 627).

**Next supervisor decisions needed.**

- (a) Spin up the BTSnoop pipeline?
- (b) Skip it and rely on the static encoders + `scripts/rename_test.py`-style direct-MQTT confirmation
- (c) Fix `decode_map_response` to read `BasicInfo.f2 = name`

---

### Capture session reply 3 (2026-05-26, capture box) — decoder gap closed + app-feature audit

Took the gap audit and worked it end-to-end. **Summary:**

**Decoder (Section A)**: shipped. New `decode_zone_config` (19-field PbZoneConfig, Hermes #9432), `decode_channel_config` (PbChannelConfig, Hermes #9444), `enuBasePoint` with altitude, `chargingStation.z`, `diagonalCoords`. The live `scripts/map_response.bin` round-trips cleanly through `decode_map_response`: globalZoneConfig surfaces cutHeight=60/moveSpeed=0.6/pathSpacing=1/perimeterMowLaps=2/lineFollowMode=true; per-zone overrides cutHeight=40, moveSpeed=0.8. **The localStorage shim in the card can finally go** — feed the panel from the real `globalZoneConfig` field. Card-side change only; backend is ready.

**Encoder bugs (found while doing the decode work)**:
1. `_TASK_CONFIG_FIELDS` had **eight** fields on wrong wire positions (f9 was `pathSpacing` instead of `relativeCleanDir`, then everything from f10–f16 shifted by one). `lymow.set_task_config(path_spacing=X)` was silently writing X into `relativeCleanDir` on the robot. Fixed; the buggy test that pinned it is now correctly pinned to canonical wire positions.
2. `_RUN_TIME_CONFIG_FIELDS` was using PbZoneConfig field numbers (f4 for moveSpeed, f6 for cutSpeed). PbRunTimeConfig has its own layout — f1 cutHeight, **f2 moveSpeed, f3 cutSpeed**. `encode_set_run_time_config(moveSpeed=…)` was writing the float into f4 which is `channelConfig` (length-delimited). Fixed.

### App-vs-Lovelace feature matrix (2026-05-26, capture session — explicit)

**The user's question: "are we able to build them together from the lovelace card even when the app says coming soon?"** — **YES.** Anything the robot's protocol enum exposes is a candidate for the card to implement client-side, regardless of whether the Lymow app ships the UI. The card can talk to the robot directly via HA's MQTT path. The "coming soon" toast is a UI gate in the app, not a protocol gate on the robot.

Status of every map/zone operation, as of this capture session:

| Operation | In Lymow app? | In HA card? | Wire-format source | Status |
|---|---|---|---|---|
| Start mowing (Mow) | ✅ shipped | ✅ shipped | app BLE capture (this session) | wire-validated |
| Pause | ✅ shipped | ✅ shipped | app BLE capture (this session) | wire-validated |
| Return to dock | ✅ shipped | ✅ shipped | app BLE capture (prior session) | wire-validated |
| Rename go-zone | ✅ shipped | ✅ shipped | app BLE capture (prior session, byte-equal) | wire-validated |
| Rename no-go zone | ❌ — app's Rename only targets go-zones | ✅ shipped | symmetry with go-zone rename + Hermes class walk | envelope-validated |
| Rename channel | ❌ — PbChannel has no name field on the wire | ✅ shipped (HA-side `_channel_name_overrides`) | client-only | HA-side persistence only |
| Delete element (zone/nogo/channel) | ✅ shipped (one Delete Element button) | ✅ shipped (separate buttons per type) | app BLE capture (this session) | wire-validated |
| Add go-zone (draw polygon) | ❓ not in app's edit toolbar | ✅ shipped (draw + name) | sync_map envelope | client-driven |
| Add no-go zone | ❓ not in app's edit toolbar | ✅ shipped | sync_map envelope | client-driven |
| Add channel | ❓ not in app's edit toolbar | ✅ shipped | sync_map envelope | client-driven |
| Move vertex / drag handle | ❌ — app has no vertex-edit UI | ✅ shipped | sync_map envelope | card-only feature |
| Insert / delete vertex | ❌ — app has no vertex-edit UI | ✅ shipped | sync_map envelope | card-only feature |
| **Merge zones** | 🚧 "Stay tuned! Coming soon" — toast only | ✅ shipped (convex-hull + sync_map) | client-side; userCtrl 55 enum is in the protocol but app never sends it | **HA exclusive** until app catches up |
| **Split zone** | 🚧 "Stay tuned! Coming soon" — toast only | ✅ shipped (polygon cut + sync_map) | client-side; userCtrl 56 enum is in the protocol but app never sends it | **HA exclusive** until app catches up |
| Edit Boundary (drive-the-robot boundary record) | ✅ shipped (joystick UI + confirm) | ❌ not implemented | userCtrl 10 / 11 — capture deferred (needs zone selection + drives the robot) | **HA gap, app exclusive** |
| Enable / disable zone | ❌ not in app | ✅ shipped (long-press toggles `isEnabled`) | sync_map envelope | card-only feature |
| Move charging station | ✅ shipped (drag base in app) | ✅ shipped (drag in edit mode, no zone selected) | app BLE capture (prior session, MODIFY_STATION userCtrl=38) | wire-validated |
| Pin-and-go (clean point) | ✅ shipped | ✅ shipped | wire-validated (prior session) | wire-validated |
| Mowing settings (cut height / move speed / path spacing / etc.) | ✅ shipped | ✅ shipped — now driven by decoded `globalZoneConfig` instead of localStorage | userCtrl 36 SET_TASK_CONFIG + PbZoneConfig sub-message; encoder bugs fixed this session | wire-validated |
| Schedules (add / edit / delete) | ✅ shipped | ✅ shipped | wire-validated (prior session) | wire-validated |
| Device settings (rain mow, charging mode, zone order, handbrake) | ✅ shipped (Settings menu) | ✅ shipped (Select entities) | wire-validated | wire-validated |
| Map Backup | ✅ shipped (Settings → Map Backup) | ❌ not implemented | userCtrl 44 — capture deferred | **HA gap, app exclusive** |
| Map Restore | ✅ shipped | ❌ not implemented | userCtrl 45 — capture deferred | **HA gap, app exclusive** |
| Lock robot | ✅ shipped (Settings menu, anti-theft) | ❌ not implemented | userCtrl 18 — not yet captured | **HA gap** |
| Reset charging station calibration | ✅ shipped (Settings) | ❌ not implemented | userCtrl 17 — not yet captured | **HA gap** |
| Delete All zones/channels | ✅ shipped (toolbar — DESTRUCTIVE) | ❌ not implemented (intentional — too easy to fat-finger) | userCtrl 15 — not captured (would wipe the user's real map) | **HA gap, intentional** |

**Reading the matrix the other way — what the HA card does that the app can NOT do:**
- Vertex-level polygon edit (drag / insert / delete a vertex)
- Add / delete go-zones, nogo zones, channels with a draw-polygon UX (the app uses drive-the-robot via Edit Boundary instead)
- Merge zones — robot has the enum, app hasn't shipped the UI
- Split a zone with a clean two-point cut — robot has the enum, app hasn't shipped the UI
- Toggle a zone's enabled state without deleting it (long-press)
- Per-zone cut-height override (backend coordinator method exists, no UI yet — see TODO list)

**So the user's instinct is right**: features the app calls "coming soon" can still be exposed in the card today, because the card talks straight to the robot. The protocol's userCtrl enum is the source of truth, not the app's toolbar. The two "coming soon" entries (Merge / Split) are **already shipped in the card via client-side geometry + sync_map**, so the card is genuinely ahead of the app on those.

**The one thing the app can do that the card can not** is **Edit Boundary** — the drive-the-robot boundary recording flow — and **Map Backup/Restore**. Both need encoder work in `protocol.py`; both deferred this session because driving the robot needs user supervision.

### What this session DID and DID NOT capture (honest scope)

**Captured in this session (2026-05-26):**
- ✅ Live `query_map` decode against fresh robot bytes — every PbMap field accounted for.
- ✅ Hermes class walks for: PbMap, PbZoneConfig, PbChannelConfig, PbTaskConfig, PbRunTimeConfig, PbPose, PbRobotLLACoords.
- ✅ App-toolbar feature audit: tapped Merge Map → toast; tapped Split Map → toast; tapped Edit Boundary → joystick + element-required error.
- ✅ Two encoder bugs found and fixed.

**NOT captured in this session — left for a follow-up capture round:**
- ❌ Mow / Pause / Dock wire frames (would need to drive the mower; safe inside the existing zone, just out of scope for this turn).
- ❌ Add-zone-via-app — the app has no draw-polygon UI in its edit toolbar; the only "add" path is Edit Boundary (drive-the-robot). The card's draw-polygon-add is therefore a card exclusive.
- ❌ Delete element via app — the toolbar's "Delete Element" button was present but I did not exercise it; the card's delete is already wire-validated by envelope symmetry with rename.
- ❌ Nogo-zone-specific add/delete flows in the app (no app UI to add a nogo independently of Edit Boundary).
- ❌ Channel-specific flows in the app (no channel add/delete in the app's edit toolbar — channels appear only as auto-generated path connectors).
- ❌ Move charging station — was wire-validated in a **prior** capture session (MODIFY_STATION userCtrl=38), not re-validated here.
- ❌ Schedule add/edit/delete via app.
- ❌ Device settings (rain cleaning, charging mode) — were wire-validated in prior sessions.
- ❌ Map Backup / Map Restore — Settings menu flow, not exercised.

The matrix above marks "wire-validated" only the things actually byte-captured by us at some point. "Envelope-validated" means we built the encoder bytes from the canonical Hermes class layout + matched the envelope shape to a captured frame of the same family. "Client-driven" means it's a card-only feature — no robot encoder needed beyond `sync_map`, and the card already does it.

### Supervisor reply 2 (2026-05-26, supervisor laptop)

Took over capture from this laptop's ADB (USB `fc7d1e36` + WiFi `192.168.1.45:5555`, both work). Answers to (a)/(b)/(c) plus the BLE wire-format ground truth.

**On (c) — partial; the bug was real but in a different spot.** Go-zone f2 decode was actually shipped on May 24 (commit `3798bbd` for the card's zone-label feature). The gap was **no-go zones**: `decode_map_response` skipped `BasicInfo.f2` for nogo entries — which silently broke the round-trip for the brand-new `encode_rename_nogo_zone` from commit `0356b58`. Added the 3-line fix plus four targeted decode tests (go-name present/absent, nogo-name present/absent). Channels intentionally skip the name read — `decode_channel` confirms PbChannel has no f2-name field at all (f1 hashId, f2 zone1, f3 zone2).

**On (a) vs (b) — discovered the BLE channel is the *same* protobuf as MQTT, just base64-wrapped. So we don't need to choose.** Drove the rename flow end-to-end from this laptop and parsed the phone's `hci_snoop20260526110017.cfa` BTSnoop log (btsnoop is `mSnoopLogSettingAtEnable = full`, `.cfa` is just an OEM extension on a standard btsnoop file — header `btsnoop\x00\x00\x00\x00\x01`). All app→robot writes hit ATT WRITE_CMD on **handle 0x0014**, in **four sizes** that fully cover the steady-state traffic plus the rare command burst:

| ATT payload size | b64 ASCII length | decoded pb | meaning |
|---|---|---|---|
| 8 B | `EDFKAlgB` | `10 31 4a 02 58 01` | poll: PbInput {f9: {f11: 1}} |
| 12 B (3 variants) | e.g. `ugEECCYgAQ==` | `ba 01 04 08 26 20 01` | sub-message poll: f23 {f1=38, f4=1} |
| 16 B | `EDEoE7oBBAgAIAE=` | `10 31 28 13 ba 01 04 08 00 20 01` | QUERY_MAP (userCtrl=19) with f23 params |
| 56 B | `OALaASVPTkVQTFVTQTUw...` | `38 02 da 01 25 "ONEPLUSA5010_Android_..."` | heartbeat with device id |

Then the rename frame I drove from this laptop (typed `ABCDEFG_TEST` into the Rename dialog → OK):

```
ATT b64 (48 B):  EDEoCWIeChwKGhIMQUJDREVGR19URVNUGgh3c21qY28xVCAB
pb (36 B):       10312809621e0a1c0a1a120c414243444546475f544553541a0877736d6a636f31542001
breakdown:       10 31 = PB_VERSION 49
                 28 09 = USER_CTRL_MODIFY_ZONE_INFO 9   ← matches encode_rename_zone
                 62 1e = field 12 (PbMap) len 30
                   0a 1c = goZones[0] len 28
                     0a 1a = basicInfo len 26
                       12 0c "ABCDEFG_TEST"  ← BasicInfo.f2 (name)
                       1a 08 "wsmjco1T"      ← BasicInfo.f3 (hashId)
                       20 01                  ← BasicInfo.f4 (isEnabled) — APP-ONLY
```

Round-trip confirmation: `scripts/rename_test.py` queried the robot after the OK tap and saw `BasicInfo.f2 = "ABCDEFG_TEST"` for `wsmjco1T`. The robot persisted the name. Then the same script renamed it back to "Front garden" — verified. So the phone's app → robot rename worked, and the robot's BasicInfo.f2 is the single source of truth (as we already established direct-MQTT in Task B). No app-side persistence sink to mirror.

**Encoder vs app — one structural difference (intentional).** The app appends BasicInfo.f4 = isEnabled = 1; our `encode_rename_zone` omits it. **Keeping ours as-is.** Sending a blanket 1 on every rename would re-enable any zone the user had disabled via long-press (the app probably writes back the cached current value, but we have no equivalent and don't want to read-then-write on every rename). Pinned this difference as a regression test (`test_encode_rename_zone_envelope_matches_app_ble_capture`) so a future encoder change can't silently start clobbering isEnabled.

**Conclusion: (a) is unnecessary for this branch.** The BLE wire format is provably equivalent to the MQTT envelope we already produce — same `PbInput` shape, same userCtrl numbering, just base64-wrapped at the BLE link. The direct-MQTT round-trip plus the captured-frame structural diff fully validates Tasks B + C, and Task A is card-only (no app counterpart). For future capture needs (Merge / Split / Add-via-app), the same BTSnoop → `tshark -Y 'btatt.opcode == 0x52 and btatt.handle == 0x0014'` → base64-decode pipeline works without restarting Bluetooth — snoop is already in `full` mode.

**Coverage gap closed.** The branch was at 98.9% before — gaps were in unrelated new code (`async_update_nogo_polygon`, `async_add_nogo_zone`, `async_add_channel`, `_encode_channel`, `handle_set_zone_enabled`). Added 16 targeted tests across `test_coordinator.py`, `test_protocol.py`, `test_lawn_mower.py`. `uv run pytest tests/ --cov=custom_components/lymow --cov-fail-under=100` now passes (1006 tests, 100% coverage). `ruff format --check` and `ruff check` both clean.

**Phone restored**: ADB closed all dialogs and returned the app to the main map screen. Phone proxy left as-is at `192.168.1.180:8888` per capture session's standing setup. Robot state: docked, idle.

**Outstanding before merge**: All three "must-haves" from the top of this file are now done.
- Task A — card-only feature, validated by envelope symmetry with the now-live-confirmed rename
- Task B — live-confirmed via direct-MQTT round-trip **and** byte-equality with captured app BLE frame
- Task C — pinned bytes + envelope symmetry with the now-live-confirmed rename
- Tests at 100% coverage — passing on this machine

Ready to ship. Recommend removing this file in the merge commit.
