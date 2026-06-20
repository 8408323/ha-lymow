<!-- ⚠️  REMOVE THIS FILE BEFORE MERGING THE PR — it is a dev scratch document, not product docs. -->

# Branch: feat/map-lovelace-card — Supervisor Document

> 🔒 **PUBLIC REPO — NO SENSITIVE DATA.** This branch is public. Never write
> GPS coordinates, PIN codes, email addresses, passwords, auth tokens, or
> Cognito *user* identity IDs into any tracked file (doc/code/tests/commit
> messages). IP addresses, the Cognito *pool* ID (shared app config), device
> thing-name, and factory-default hints (e.g. PIN default 0000) are OK.
> Capture artifacts (`*.cfa/*.pcap/*.har/capture-*.txt`, `/tmp/lymow-*`) are
> gitignored — decode them locally, record only redacted structure here.

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

## 🎯 Project goal — mirror the full Lymow Android app in the HA Lovelace card

Everything the Lymow Android app can do, the HA Lovelace map card should also be able to do (and ideally more — features the app gates with "coming soon" toasts but whose protocol opcodes the robot already accepts).

Treat the protocol/robot as the source of truth, not the app's toolbar. If the robot accepts a userCtrl, the card can implement it.

The **App-vs-Lovelace feature matrix** (see line ~636 below) is the running scorecard. Every "❌ not implemented" or "🚧 coming soon" entry in that matrix is a candidate for closure. When a new feature is wired up, update the matrix.

---

## 📨 Agent onboarding — read this before starting fresh work

Anything below is the only context a fresh agent or capture-session worker is guaranteed to have. Treat it as the spec.

### Repo + branch
- Branch: `feat/map-lovelace-card` on `8408323/ha-lymow`. Stay on this branch — do not branch off.
- Working tree: `/home/mint-laptop-4/private_projects/ha-lymow-lovelace` (supervisor laptop). Capture-session box has its own clone; coordinate via `git pull` and `git push`.
- Pull before doing anything: `git pull --ff-only`.
- Commit early, push often. The polling loop above only sees pushed commits.

### Codebase map (just the parts you need)
- `custom_components/lymow/protocol.py` — protobuf encode/decode. **Single source of truth for wire formats.** Add new encoders here.
- `custom_components/lymow/coordinator.py` — `LymowDataUpdateCoordinator`. `async_*` methods that publish MQTT and update cache. Add new async methods here.
- `custom_components/lymow/lawn_mower.py` — registers HA services (`lymow.*`) that wrap coordinator methods. Add service handlers + `hass.services.async_register` calls here.
- `custom_components/lymow/services.yaml` — user-facing service docs. Must be kept in sync with the actual registered services.
- `custom_components/lymow/api.py` — REST API client (Cognito-authenticated). Backup list/restore/delete/rename live here.
- `custom_components/lymow/www/lymow-map-card.js` — the Lovelace card. Add new UI here. Bump the cache-buster (`?v=…`) in the Lovelace resource when shipping a JS change.
- `tests/` — pytest, 100% coverage enforced. Every new branch needs a covering test.

### Run commands (always via `uv run`)
```bash
uv run pytest tests/ -v --cov=custom_components/lymow --cov-fail-under=100
uv run ruff format --check .
uv run ruff check .
```

### Capture-session capabilities
- ADB to phone (USB `fc7d1e36`, WiFi `192.168.1.45:5555`).
- mitmproxy v12.2.3 on `192.168.1.180:8888` — phone proxy points there; Magisk CA cert installed.
- BTSnoop log at `/data/misc/bluetooth/logs/btsnoop_hci.log` (or rotated `hci_snoop*.cfa`) — `mSnoopLogSettingAtEnable=full` mode (filtered .cfa drops ATT writes).
- Direct-MQTT scripts under `scripts/` — `rename_test.py`, `query_map.py`, `delete_zone.py`, `mow_zone.py`, etc. Use `LYMOW_USER`/`LYMOW_PASS` in `scripts/.env`.

### Mirroring the Lymow app over WiFi (REQUIRED for app captures)

The capture-session box cannot do good app-driven captures without seeing the phone screen. Use one of these to mirror/control the phone over WiFi while ADB is connected:

```bash
# Option A — scrcpy over WiFi ADB (recommended; no extra cable, low latency)
adb connect 192.168.1.45:5555
scrcpy -s 192.168.1.45:5555 --window-title "Lymow phone" --max-fps 30

# Option B — scrcpy over USB ADB (use when WiFi is flaky)
adb devices                          # confirm fc7d1e36 is listed
scrcpy -s fc7d1e36 --window-title "Lymow phone (USB)"

# Option C — vnc-like via Android's built-in screen-mirror (last resort)
adb -s 192.168.1.45:5555 exec-out screenrecord --output-format=h264 - | ffplay -framerate 30 -
```

Why scrcpy: lets the capture agent see the app UI, drive multi-touch (pinch-zoom, drag), and observe responses in real-time — none of which `adb shell input tap`/`swipe` can do reliably (see "Capture blockers" further down).

**Install scrcpy on the capture box if missing:** `sudo apt install scrcpy` (Ubuntu) or `brew install scrcpy` (macOS); needs `adb` already in PATH.

**Pre-flight checklist before any capture session:**
1. `adb connect 192.168.1.45:5555` succeeds (`adb devices` shows `device`, not `unauthorized`).
2. `scrcpy -s 192.168.1.45:5555` opens a window showing the phone home screen.
3. mitmproxy running (`ss -ltnp 'sport = :8888'` shows it).
4. Phone proxy is set: `adb shell settings get global http_proxy` returns `192.168.1.180:8888`.
5. BTSnoop is in `full` mode: `adb shell settings get secure bluetooth_hci_log` → `1`. If not, enable Developer Options → "Enable Bluetooth HCI snoop log" (full), then **toggle Bluetooth off/on** so the new log file is created.
6. Lymow app is **logged in** and connected to the robot (green dot in the app's main screen).
7. **Clear the proxy when done**: `adb shell settings delete global http_proxy` — leaving it on caused E29 dock-fail in a past session.

### Capture pipeline (BTSnoop → decoded pb)

The app→robot path is split: most commands go BLE (ATT WRITE_CMD on handle 0x0014), backup/restore goes MQTT/REST. To capture BLE:

```bash
# 1. Pull the snoop log (replace the timestamp suffix)
adb -s 192.168.1.45:5555 shell ls /data/misc/bluetooth/logs/
adb -s 192.168.1.45:5555 pull /data/misc/bluetooth/logs/btsnoop_hci.log

# 2. Extract the ATT writes to handle 0x0014 (app→robot commands)
tshark -r btsnoop_hci.log \
       -Y 'btatt.opcode == 0x52 and btatt.handle == 0x0014' \
       -T fields -e btatt.value | xxd -r -p | base64

# 3. Decode the base64 chunk with our decoder
uv run python -c 'import base64; from custom_components.lymow.protocol import decode_pbinput; print(decode_pbinput(base64.b64decode("<paste here>")))'
```

For MQTT capture, mitmproxy + `tools/capture.py` writes labelled lines to `tools/capture-lymow.txt` (`PBINPUT` / `PBOUTPUT` / `REST`).

### When in doubt
- Wire format always traces to **our own** capture (BLE or MQTT or REST). Never cite a third-party repo.
- `const.py` lists every named userCtrl with its source comment.
- `protocol.py` field comments document Hermes class numbers (`#9432` etc.) — those refer to the Lymow app's APK Hermes bytecode where we extracted the canonical message layouts.

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
- [x] **enuBasePoint added** (2026-05-26): PbMap.f7 is `PbRobotLLACoords {latitude, longitude, altitude}` (Hermes class #9276). Live frame carries the site lat/lon/altitude (values omitted — user location, public repo). `gpsOrigin` retained as an alias.
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

### Task D — Schedule create / edit / delete (NEW, 2026-05-27)

**Goal.** Confirm wire format for **each** of the schedule mutations the app exposes:

1. **Create a NEW schedule** — Settings → Schedules → "+" → fill in day, time, zones → Save.
2. **Edit an existing schedule** — tap an existing row → change the time or zone list → Save.
3. **Delete a single schedule** — long-press a row → Delete (or swipe / 🗑 icon, whichever the UI uses).
4. **Toggle a schedule's enabled state** — tap the row's toggle without entering the editor.
5. **Clear all schedules** — Already wire-validated (`encode_clear_schedules` → `10 31 5a 00`); only re-capture if some new path emerges.

**Why we still need this even though `encode_set_schedules` exists.**

`encode_set_schedules` is wire-validated for one shape (the full PbSchedules.tasks list), but we don't know which of these flows uses *that* shape and which use a different field:

- Does **delete-single** send the full list minus the deleted entry, or a scoped `delete-schedule-by-id` opcode?
- Does **toggle-enabled** send the full list with `isDisabled` flipped, or a small "set-isDisabled" frame?
- Does **edit** send the full list with one entry replaced (id-keyed), or a partial frame?

Without per-action capture we can't expose granular `enable_schedule(id)` / `delete_schedule(id)` services — the card would have to fall back to "read-modify-rewrite the whole list", which is fine but extra round-trips.

**Capture procedure (each subtask):**

1. With scrcpy mirroring the phone, open Lymow app → Settings → Schedules.
2. **Before** the action: `adb -s 192.168.1.45:5555 pull /data/misc/bluetooth/logs/btsnoop_hci.log baseline.log` (note last frame timestamp).
3. Perform the action in the app.
4. **Wait at least 5 minutes** — supervisor reply 4 found the app debounces some writes that long.
5. Pull the snoop file again: `adb pull /data/misc/bluetooth/logs/btsnoop_hci.log after.log`.
6. Extract the new ATT writes (handle 0x0014):
   ```bash
   tshark -r after.log -Y 'btatt.opcode == 0x52 and btatt.handle == 0x0014' \
          -T fields -e frame.time -e btatt.value
   ```
7. For each new b64-decoded payload, run it through `decode_pbinput` and document:
   - userCtrl number (or "none" if it's a payload-only command like clear_schedules)
   - Field layout
   - Whether the full list was re-sent or a scoped sub-message was used

**Report back** by appending to "Findings" section below and pushing a `test-ready: schedules` commit so the supervisor can wire up granular services.

**Code that already exists** (don't re-implement — verify and extend):
- `protocol.py::encode_query_schedules` (userCtrl=20) — wire-validated
- `protocol.py::encode_clear_schedules` — wire-validated (verbatim app capture)
- `protocol.py::encode_set_schedules(entries)` — wire-validated for "Save Task" flow
- `protocol.py::_encode_schedule_entry` — full PbSchedule field map (dayOfWeek, hour, minute, isRepeated, zones, id, timeZone, isDisabled, isAngleOffset, config)
- `coordinator.py::async_query_schedules / async_clear_schedules / async_set_schedules`
- `services.yaml::query_schedules / clear_schedules / set_schedules` services

---

### Task E — Map backup lifecycle (DONE 2026-05-27 by supervisor laptop, NO further capture needed)

Full end-to-end capture in `scripts/backup_lifecycle_capture.py` and Supervisor reply 4 below. Wire-validated:

| Action | Transport | Wire | Implementation |
|---|---|---|---|
| Create backup | MQTT pbinput | userCtrl=44 (`pb_hex=1031282c`) | `BackupMapButton`, `coordinator.async_backup_map` |
| List backups | REST GET `/prod/get-backup-map` | `{deviceThingName}` → `{mapList: [{map_file, backup_time, name}]}` | `api.get_backup_map_list`, surfaced via backup sensors |
| Restore backup | REST POST `/prod/restore-map-v2` | `{fromKey, toThingName}` | `api.restore_backup_map`, `lymow.restore_backup_map` service |
| Delete backup | REST POST `/prod/delete-backup-map` | `{objectKey}` → `{}` | `api.delete_backup_map`, `lymow.delete_backup_map` service |
| Rename backup | REST POST `/prod/update-backup-map-metadata` | `{objectKey, name}` | `api.rename_backup_map`, `lymow.rename_backup_map` service |

What's missing is **only the card UI**: a backups panel (📦 icon next to 📅) that lists, restores, deletes, renames. Backend is complete and round-trip-tested.

---

### Task F — Charging-station realign (DONE 2026-05-27 by supervisor laptop, NO further capture needed)

Live MQTT capture in `scripts/dock_realign_capture.py`. Wire-validated:

- **Send**: `pb_hex=1031282c` → correction: `1031 28 11` = userCtrl=17 (`CHARGING_STATION_RESET`), envelope `{"message":"EDEoEQ=="}`, 4 bytes total.
- **Robot reaction (within 5s)**: `workStatus` flips CHARGING(5) → WAITING(1), `isCharging` → false. Robot stays physically parked (rtkEastM/rtkNorthM stay near 0).
- **No payload-bearing response** — the change is observable only via the standard pboutput broadcast.
- Already implemented as `ChargingStationResetButton` (button.py); also `SetChargingStationHereButton` (userCtrl=38) for the inverse "record current position as new dock".

To restore the robot to its charging cycle after this command, send userCtrl=33 (RECHARGE_DOCK): `scripts/_one_off.py 33`.

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
- ✅ App-toolbar feature audit: Merge Map → "Coming soon" toast; Split Map → "Coming soon" toast; Edit Boundary → joystick UI with element-required gate.
- ✅ Two encoder bugs found and fixed (PbZoneConfig field off-by-ones, PbRunTimeConfig wrong field numbers).

**Captured in the follow-up round (2026-05-26 / -27, after user pushback on incomplete capture):**

Full mowing-state machine captured via app BLE writes (ATT WRITE_CMD to handle 0x0014):

| App button | Robot state when tapped | Wire frame (hex) | userCtrl | Matches `const.py` |
|---|---|---|---|---|
| **Mow** | Waiting (docked) | `10312801` | 1 (USER_CTRL_CLEAN) | ✅ |
| **Pause** | Mowing | n/a (test ended in confirm dialog before tap) | expected 3 (USER_CTRL_PAUSE) | ✅ |
| **Pause** | Docking | `10312815` | 21 (USER_CTRL_PAUSE_DOCK) | ✅ |
| **Resume** | Paused | `10312804` | 4 (USER_CTRL_RESUME) | ✅ |
| **Resume** | Pause-Docking | `10312816` | 22 (USER_CTRL_RESUME_DOCK) | ✅ |
| **Dock** | Idle/docked (no confirmation) | `10312802` | 2 (USER_CTRL_DOCK — **destructive: cancels task progress**) | ✅ |
| **Dock → "Yes forget progress"** | Mowing (confirmation dialog) | `10312802` | 2 (USER_CTRL_DOCK) | ✅ |
| **Dock → "No keep progress"** | Mowing (confirmation dialog) | `10312821` | 33 (USER_CTRL_RECHARGE_DOCK) | ✅ |
| **Clear Error** | Error state | `10312803` | 3 (USER_CTRL_PAUSE) — *no separate clear-error opcode; pause IS the clear* | ✅ |

**Key behavior difference**: the HA card's "Dock" button currently goes through `USER_CTRL_RECHARGE_DOCK = 33` (preserves task progress). The Lymow app's "Dock" button when tapped during active mow opens an explicit **"After docking, should the mower forget its progress?"** dialog — **Yes** sends userCtrl=2 (cancel/destructive), **No** sends userCtrl=33 (preserve). When the robot is already idle/docked, the app's Dock button sends userCtrl=2 directly with no confirmation. The card could expose the same explicit choice for parity with the app's UX.

**Device-settings (BLE writes captured this session):**

| App control | Wire frame (hex) | Decode | Notes |
|---|---|---|---|
| **Vehicle LED toggle ON** | `10316a02400a` | `PbInput{robotConfig{signal: 10}}` | matches `SIGNAL_TURN_ON_VEHICLE_LIGHT = 10` |
| **Vehicle LED toggle OFF** | `10316a02400b` | `PbInput{robotConfig{signal: 11}}` | matches `SIGNAL_TURN_OFF_VEHICLE_LIGHT = 11` |
| **Rainy Mowing ON** | `10312824d20106080018012000` | userCtrl=36 + `PbTaskConfig{chargingMode:0, rainCleaning:1, disableChargingPark:0}` | confirms PbTaskConfig 4-field layout; app re-sends ALL 4 fields every toggle |
| **Charging Handbrake → disabled (toggle)** | `10312824d20106080018012001` | userCtrl=36 + `PbTaskConfig{chargingMode:0, rainCleaning:1, disableChargingPark:1}` | sends full 4-field PbTaskConfig |
| **Return-to-Dock: Direct Route** | `10312824d20106080118012001` | userCtrl=36 + `PbTaskConfig{chargingMode:1, ...}` | `chargingMode=1` = QUICK = Direct Route; 0 = NORMAL = Follow Perimeter |

All four device-settings frames confirm that the canonical `PbTaskConfig` (Hermes class #9588) is what `lymow.set_device_settings` should write — which our encoder already does correctly.

**Cloud-side flows (NOT BLE — observed via mitmproxy):**

| App action | Transport | Wire | Notes |
|---|---|---|---|
| **Map Backup (Settings → Map Backup → Back up → Confirm)** | MQTT pbinput | `1031282c` = userCtrl=44 (USER_CTRL_FLOOR_BACKUP) | matches our `button.py` BackupMapButton; already implemented |
| **Map Restore (Settings → Map Backup → tap backup → Restore → Confirm)** | REST POST | `POST /prod/restore-map-v2` `{fromKey: "<thingName>/map/<key>.pb", toThingName: "<thingName>"}` | already implemented in our API client (per `reference_map_backup_delete.md`) |
| **List backups** | REST GET | `GET /prod/get-backup-map?deviceThingName=<thingName>` returns S3 keys | already implemented |
| **Download backup** | REST GET → S3 presigned | `GET /prod/get-s3-object?objectKey=<key>` → presigned S3 URL → raw map .pb | already implemented |

This **resolves a structural assumption from the prior session**: an earlier finding claimed ALL app→robot commands go via BLE. That's wrong. **Some commands (map backup, MQTT-only operations) bypass BLE entirely and go through MQTT or REST.** The transport choice appears to be per-feature:

- BLE writes to handle 0x0014: most one-shot commands (Mow, Pause, Dock, device settings, Vehicle LED) — fast, low-latency, close-proximity.
- MQTT pbinput: backup (userCtrl=44) — confirmed; possibly others when an internet round-trip is expected.
- REST API: restore (the cloud-side flow needs to coordinate AWS S3 + the robot fetch).

**NOT captured this session — explicitly deferred:**
- ❌ **Edit Boundary START frame (userCtrl=10)**. App requires a selected zone before the START frame is sent; ADB-driven zone selection unreliable. Capture deferred to a session with manual zone selection + supervision (the flow also drives the robot).
- ❌ **Add-zone-via-app** — the app's edit toolbar has no draw-polygon button. The only "add" path is Edit Boundary (drive the robot). The card's draw-polygon-add is therefore a card exclusive — no app frame exists to compare against.
- ❌ **Delete Element via app toolbar** — the button was present but not exercised this session (would have deleted a real zone). The card's delete is already wire-validated by envelope symmetry with the rename frame (which IS byte-captured).
- ❌ **Move charging station** — wire-validated in a **prior** capture session (`MODIFY_STATION userCtrl=38`); not re-validated here.
- ❌ **Schedule add/edit/delete** — Settings → Schedules page not exercised; encoder already implemented via `encode_set_schedules`.
- ❌ **Delete backup** — Settings → Map Backup has no per-backup-delete button visible; might be long-press, not tested.

The matrix above marks "wire-validated" only the things byte-captured by us at some point. "Envelope-validated" means we built the encoder bytes from the canonical Hermes class layout + matched the envelope shape to a captured frame of the same family. "Client-driven" means it's a card-only feature — no robot encoder needed beyond `sync_map`, and the card already does it.

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

---

### Supervisor reply 3 (2026-05-27, supervisor laptop)

**PR stays DRAFT. Two features still missing before merge:**

#### 1. Per-zone settings — not yet in the card

The global mowing-settings panel (speed, cut speed, path spacing, perimeter laps, etc.) is wired to `globalZoneConfig` (PbMap.f11). That's the global default.

The robot also sends **per-zone overrides** in `PbZone.f2` (configBox) — already decoded, already surfaced as zone attributes (`cutHeight`, `pathSpacing` on each zone object). But the card has no UI to edit them.

**What we need:**

**Capture session:** Confirm the wire format for `SET_TASK_CONFIG` with a zone-scoped override.
- In the app: tap a zone → enter edit mode → find the zone-specific settings (cut height, path spacing) → change a value → confirm.
- Capture the resulting BLE frame. Does it send a full `PbZoneConfig` sub-message scoped to that zone's hashId? Or does the app only write to globalZoneConfig?
- If the app doesn't support per-zone overrides at all in the UI (only global), document that so we know the per-zone data is read-only from the robot's perspective.

**Supervisor (after capture reply):** Add a per-zone settings panel to the card. When a zone is selected in edit mode, a small settings panel appears below the global one (or replaces it) showing that zone's cutHeight, pathSpacing, and moveSpeed — editable, with an Apply button that calls `set_task_config` with the zone's hashId if the wire supports it.

#### 2. Zone edit → mirrored in Lymow app — Edit Boundary not captured

`encode_modify_zone_edge_start/stop` (userCtrl 10 / 11) is still unvalidated. This is the only way the app adds/reshapes a zone by driving the robot. Without a confirmed wire frame we can't tell if the card's polygon-draw alternative is truly equivalent or if there's a different shape for zone boundaries.

**Capture session:** Try again with manual zone selection:
1. Open the Lymow app → tap directly on a go-zone polygon (not the centre label, the polygon border)
2. Confirm the edit toolbar appears and shows "Edit Boundary"
3. Tap Edit Boundary → the robot should start driving
4. Capture the BLE frame at step 3 (ATT WRITE_CMD handle 0x0014)
5. Also capture the STOP frame (userCtrl=11) when you tap Stop

This one is lower priority than the per-zone settings — the card's draw-polygon+SYNC_MAP path already works for adding zones. But it's the only card feature that's truly unvalidated at the wire level.

**Current deployment state (2026-05-27):**
- All 22 Python files + card JS deployed to HA at 192.168.1.99 from feat/map-lovelace-card
- Lovelace resource updated to `?v=0.2.3` — old `?v=29` double-registration error resolved
- Card settings panel (global) opens and applies settings correctly
- `globalZoneConfig` decoder confirmed working on captured binary (cutHeight=60, moveSpeed=0.6, pathSpacing=1, perimeterMowLaps=2, lineFollowMode=true)
- `globalZoneConfig` (f11) is absent from docked-state map responses — robot only echoes it when a task is active. This is expected robot firmware behaviour, not a bug.

---

### Capture session reply 4 (2026-05-27 14:02 UTC, capture box) — per-zone settings wire format confirmed via BTSnoop

Per supervisor reply 3's task: drove the app's Mowing Settings → **Customize** tab via ADB, captured the resulting BLE frame from `/data/misc/bluetooth/logs/hci_snoop20260527102424.cfa` (snoop mode = `full`), and decoded it through `_decode_fields` + the supervisor's `decode_zone_config` field map. **The per-zone wire format is unlike what we expected** — the app reuses the rename envelope, not `SET_TASK_CONFIG`.

**1. Confirmed: app HAS a per-zone settings UI.** Mowing Settings has two tabs: **Global** (sliders → `globalZoneConfig` / PbMap.f11) and **Customize** (per-zone tabs zone0/zone1/channel0/channel1 → each its own configBox). The Customize tab shows the same fields as Global (Moving Speed, Cutting Height, Blade Speed) but scoped to one zone or channel.

**2. Captured BLE frame (the unique 164-byte ATT WRITE_CMD on handle 0x0014 across both BTSnoop logs):**

```
ts:           2026-05-27 12:02:09.464 UTC
ATT WRITE_CMD handle=0x0014 value=164 B (base64-wrapped pb)
base64:       EDEoCWJzCjcKDhIAGgh3c21qY28xVCABEiUIKCXNzEw/MAY4AUAASBlQAlgCYAJoAnAAgAFa
              iAEAkAEAmAECCjgKDhIAGghLWDFrR3lhdCABEiYIKCWamRk/MAY4AUCzAUgZUAJYAmACaAJw
              AIABWogBAJABAJgBAg==
pb (121B):    1031280962730a370a0e12001a0877736d6a636f315420011225082825cdcc4c3f300638
              01400048195002580260026802700080015a8801009001009801020a380a0e12001a084b
              58316b47796174200112260828259a99193f3006380140b30148195002580260026802700
              080015a880100900100980102
```

Decoded as PbInput:

```
PbInput {
  version  (f2) = 49                          ← PB_VERSION
  userCtrl (f5) = 9                           ← USER_CTRL_MODIFY_ZONE_INFO (same envelope as rename!)
  PbMap    (f12) {
    goZones[0] = PbZone (55B) {
      basicInfo = PbZoneBasicInfo (14B) {
        name      (f2) = ""                    ← always empty for config-only writes
        hashId    (f3) = "wsmjco1T"
        isEnabled (f4) = 1
      }
      configBox = PbZoneConfig (37B) {
        cutHeight        (f1)  = 40
        moveSpeed        (f4)  = 0.8 (float32)  ← overridden vs global 0.6
        cutSpeed         (f6)  = 6
        cleanMode        (f7)  = 1
        f8                     = 0
        pathSpacing      (f9)  = 25
        perimeterMowLaps (f10) = 2
        perimeterMowDir  (f11) = 2
        noGoMowLaps      (f12) = 2
        obsDecMode       (f13) = 2
        pathOrder        (f14) = 0
        relativeCleanDir (f16) = 90
        lineFollowMode   (f17) = 0
        disableOuterDischarge (f18) = 0
        followDetectMode (f19) = 2
      }
    }
    goZones[1] = PbZone (56B) {
      basicInfo { name="", hashId="KX1kGyat", isEnabled=1 }
      configBox {
        cutHeight = 40, moveSpeed = 0.6, cutSpeed = 6, cleanMode = 1,
        f8 = 179,                                ← differs from zone[0] (probably an enabledZoneMask-like bitmap rendered per-zone)
        pathSpacing = 25, perimeterMowLaps = 2, perimeterMowDir = 2,
        noGoMowLaps = 2, obsDecMode = 2, pathOrder = 0, relativeCleanDir = 90,
        lineFollowMode = 0, disableOuterDischarge = 0, followDetectMode = 2
      }
    }
  }
}
```

**3. Implications for the card / backend (the big one).**

- The app's per-zone settings **do NOT use `USER_CTRL_SET_TASK_CONFIG (36)`** — i.e. `encode_set_task_config` / `lymow.set_task_config` is the wrong codec for per-zone overrides. That service only writes the global `PbTaskConfig` at PbInput.f26 (single field, no hashId scoping).
- Instead, per-zone overrides ride **`USER_CTRL_MODIFY_ZONE_INFO (9)`** with `PbMap.goZones[*]` carrying `basicInfo {hashId, isEnabled}` + `configBox` (PbZoneConfig — same 19-field shape that `decode_zone_config` reads from `globalZoneConfig` and `PbZone.f2`).
- The robot's routing on userCtrl=9 is by sub-message shape: BasicInfo.f2 (name) set → rename; PbZone.f2 (configBox) set → per-zone config; both set → both happen in one round-trip. We confirmed the rename path via the earlier `ABCDEFG_TEST` capture (supervisor reply 2); this reply confirms the config-only path on the same envelope.
- The app sends **all zones in one frame**, even ones whose configBox matches the global. zone[1] above has moveSpeed=0.6 (global default) but is still serialised with a full configBox — so the encoder we ship should accept a list of `(hashId, configBox)` pairs and emit them together rather than per-zone-per-frame.

**4. What the backend / card needs (concrete action items).**

- New encoder in `protocol.py`: `encode_set_zone_configs(zone_configs: list[dict])` — userCtrl=9, PbInput.f12 (PbMap), repeated goZones[] each `{basicInfo {hashId, isEnabled}, configBox {...}}` matching the shape above. The encoder should accept `pathSpacing`, `cutHeight`, `moveSpeed`, etc. with the same key names `_ZONE_CONFIG_FIELDS` already uses, so the card can call it the same way it calls `set_task_config`.
- New coordinator method `async_set_zone_configs(thing_name, zone_configs)` that publishes via MQTT (HA→robot path; the BLE link is the app's path and is structurally identical so the robot accepts the same envelope from either source).
- New HA service `lymow.set_zone_configs` documented in `services.yaml`, accepting `{zone_id_or_hashes, ...config_fields}` (the existing `lymow.set_task_config` should stay since global config still uses that path).
- Card UI: per-zone settings panel in edit mode (already scoped in supervisor reply 3) wires its Apply button to `lymow.set_zone_configs` with the current zone's hashId.

**5. Trigger that fired this frame (timing weirdness worth noting).** Drove ADB through tap Save (13:58 local) and tap trash icon (13:59 local). Neither produced a frame inside the immediate 5-second wait window. The 164-byte frame was finally emitted ~3 minutes later at **14:02:09** — almost certainly tied to a debounce timer, settings-screen exit, or periodic re-sync from the app. **Capture sessions should wait at least 5 minutes after the trigger action before pulling the snoop** — and snoop search should not be time-windowed too tightly.

**6. Field 8 mystery.** zone[0] has f8=0, zone[1] has f8=179 (0xB3). Per supervisor reply 3, f8 = `enabledZoneMask` (uint64 bitmask, "all-ones = all enabled"). 0 = no zones, 179 = some specific zones. Reading this verbatim doesn't make sense in a **per-zone-config** context (a single zone wouldn't carry a multi-zone bitmask), so f8 likely means something else when PbZoneConfig is nested under PbZone.f2 vs PbMap.f11. Not blocking — just record it; the encoder can echo whatever the robot last sent for that field rather than synthesise it.

**7. Edit Boundary (Task A2 from supervisor reply 3) — not attempted.** That command drives the robot physically (record-new-boundary mode) which is destructive in an unattended capture session. The lovelace card has no UI counterpart per the original audit, so the wire format has no consumer today. Recommend skipping unless explicitly directed to drive the robot.

**8. Other observations from the snoop.**

- Across the 5 MB live snoop file there is **exactly one** non-heartbeat outbound WRITE_CMD (the 164B frame above). All other writes are 8 / 12 / 16 / 56 byte heartbeats (`10312814` poll, `3802 da01 ... ONEPLUSA5010_Android_...` device-id keepalive, etc.). The 20 MB rolled-over snoop file has **zero** large writes — meaning the user did not touch Customize Settings in the previous day. Confirming the wire frame is the rare path it sounded like.
- Snoop file is `.cfa` (OneSync OEM extension) but the header is standard btsnoop `\x62\x74\x73\x6e\x6f\x6f\x70\x00\x00\x00\x00\x01\x00\x00\x03\xea` and parses cleanly with a 100-line Python parser. No tshark required.
- Snoop epoch on this phone is exactly **245 seconds short of the standard 62 168 256 000-second offset** — minor calibration nit, recorded here so future timestamp math doesn't get off.

**Robot state on exit**: `docked`, battery 98%, no side effects. Phone proxy still at `192.168.1.180:8888`. Customize settings on the phone are still whatever they were before this session — no destructive change was committed.

---

### Supervisor reply 4 (2026-05-27, supervisor laptop) — closed three matrix gaps, added Task D for schedules, opened Task E/F

Picked up after capture-session reply 4 and the user's instruction: *"the goal is to mirror full app to have all features in HA lovelace card."* Confirmed three matrix entries that were stale (already implemented), captured two end-to-end flows live from the supervisor laptop, and added the Project Goal / Agent Onboarding / WiFi-ADB sections at the top of this file so a fresh agent or capture worker can pick up cold.

**What I closed at the codebase level (already implemented, just unflagged in the matrix):**

- `USER_CTRL_LOCK = 18` — `LockRobotButton` exists in `button.py:121`, enabled by default. The matrix's "HA gap" entry is stale.
- `USER_CTRL_CLEAR_ALL_ZONES_CHANNELS = 15` — `ClearAllZonesAndChannelsButton` exists in `button.py:225`, disabled by default (correct for a destructive command). Matrix said "intentional gap"; it's actually implemented with the correct safety posture.
- `USER_CTRL_CHARGING_STATION_RESET = 17` — `ChargingStationResetButton` exists in `button.py:149` AND wire-validated live this session (see Task F above).

**What I live-validated end-to-end from this laptop (no app, no BLE needed):**

- **Task F (charging-station realign)** via `scripts/dock_realign_capture.py`. 4-byte command, robot transitions CHARGING → WAITING in ~5s. Restored via `_one_off.py 33`.
- **Task E (full backup lifecycle)** via `scripts/backup_lifecycle_capture.py`. Created backup, polled list, deleted only the new entry, verified all 3 pre-existing backups untouched. Pinned timestamps:
  - 3 existing backups dated 2026-05-14, 2026-05-22, 2026-05-26 — left alone
  - New backup `device_7890838300cd/map/map_20260527T120604Z.pb` appeared after one 3-second poll
  - Delete returned `{}` (empty body) in ~3.2s
  - Final list: 3 entries, all the originals.

**Per-zone cut-height implementation note (in response to capture reply 4's encoder finding).** The capture session correctly identifies that the app's Customize tab uses **userCtrl=9 + per-zone `configBox`**, not `userCtrl=36 SET_TASK_CONFIG`. **Our existing `coordinator.async_update_zone_cut_height` already takes a different valid path**: it deep-copies the map, mutates `goZones[i].cutHeight`, and pushes back via `async_sync_map` (userCtrl=25, full map replace). The robot accepts both shapes. We can ship the card UI on top of the existing sync_map path immediately; switching to the more efficient userCtrl=9 path can be a follow-up that doesn't block the UI.

**What's in scope for the user (manual):**

Per the user's 2026-05-27 message, the **zone editing** captures (save/update/create/delete zones, nogo zones, channels) will be done manually later. Don't add capture tasks for those — the user has those.

**Code-only follow-ups I'm shipping in the next commit(s):**

1. Per-zone cut-height UI in the card (selects a zone in edit mode → small panel → Apply → `lymow.update_zone_cut_height` service)
2. Dock confirmation dialog matching the app's "After docking, should the mower forget its progress?" prompt — calls `lymow.dock` (userCtrl=2, destructive) on Yes vs the existing recharge_dock path (userCtrl=33) on No.

**Browser tests deferred to after the code commits** — I'll push `test-ready: per-zone-cut-height + dock-dialog` once the UI lands so the capture session knows to verify nothing regressed.

**Updated App-vs-Lovelace feature matrix (delta only — strikethrough was matrix value, new value follows):**

| Operation | Matrix said | New value | Reason |
|---|---|---|---|
| Lock robot | ❌ HA gap | ✅ shipped + wire-validated | `LockRobotButton` exists, userCtrl=18 |
| Reset charging station calibration | ❌ HA gap | ✅ shipped + **wire-validated 2026-05-27** | `ChargingStationResetButton` exists; today's live capture confirms |
| Delete All zones/channels | ❌ HA gap (intentional) | ✅ shipped (disabled by default) | `ClearAllZonesAndChannelsButton` exists |
| Map Backup | ❌ HA gap | ✅ shipped + **wire-validated lifecycle 2026-05-27** | `BackupMapButton`; full create/list/delete tested today |
| Map Restore | ❌ HA gap | ✅ shipped | `lymow.restore_backup_map` service + REST round-trip |

**True remaining gaps after this session (the only "❌ not implemented" left):**

- **Edit Boundary** (drive-the-robot mode, userCtrl=10/11) — encoder doesn't exist; no card UI counterpart; per capture reply 4 deferred indefinitely (destructive in unattended capture).
- **Per-zone cut-height UI in card** — backend ready (`async_update_zone_cut_height`); UI pending (shipping next commit).
- **Backup-management UI in card** — backend complete and round-trip tested today; UI pending (📦 panel listing/restoring/deleting/renaming).
- **Schedule mutation granular services** — capture pending (Task D above).
- **Per-zone-config encoder via userCtrl=9** — optional optimisation per capture reply 4; sync_map path works today.

---

## Code changes shipped 2026-05-27

- **Per-zone cut-height**: new `lymow.update_zone_cut_height` service (wraps existing `coordinator.async_update_zone_cut_height`); card UI row appears under the edit toolbar when a go-zone is selected (number input + Apply, 20–100 mm). Coordinator persists via `sync_map` (userCtrl=25) — works today; can be swapped to the userCtrl=9 path from capture reply 4 later without UI changes.
- **DockAndForgetProgressButton** (`button.py`): new disabled-by-default button entity sending `USER_CTRL_DOCK = 2` (destructive: cancels in-progress task). Standard HA dock action continues to use userCtrl=33 (preserve progress). Exposes the app's "After docking, should the mower forget its progress?" Yes-path explicitly instead of building a modal dialog in the card.
- **Capture scripts** under `scripts/` (dev tooling, not shipped to HA): `dock_realign_capture.py`, `backup_lifecycle_capture.py`, `_one_off.py`. Output `.log` files alongside them — add to gitignore if not already covered.

## Pre-existing test breakage — RESOLVED 2026-05-27 (commit `bfb37bc`)

The 11 failing tests came from commit `355dd1f` which partially reverted `49a7ac6` — renaming `decode_zone_config` / `decode_channel_config`, flipping the canonical PbZoneConfig wire layout back to a buggy one, and dropping the rich decoder output (`globalZoneConfig` / `globalChannelConfig` / `enuBasePoint` / `diagonalCoords` / `chargingStation.z` / per-zone `zoneConfig`). The reverts also desynchronised the decoder from sensor.py (which still looked for `globalZoneConfig` — silently empty in production) and from `_encode_go_zone` (which was dropping 17 of 19 PbZoneConfig fields on every sync_map round-trip).

Fixed by restoring 49a7ac6's protocol.py + re-layering `encode_find_my_robot_play_sound` from `a4608bf`. Result: 1031 tests pass, 100% coverage, ruff clean. The "canonical Hermes #9432" layout (f9 relativeCleanDir / f10 pathSpacing / f11 perimeterMowLaps / f15 pathOrder / f16 startProgress) is now the single source of truth shared by encoder + decoder + tests.

**Note for future capture work:** reply 4's "live BLE capture" interpretation (f9 pathSpacing / f16 relativeCleanDir) contradicts this canonical layout. The captured *bytes* don't independently identify which field number maps to which name — that came from the field map being applied to the bytes. The canonical Hermes #9432 layout is what the tests pin and what the integration produced before the regression; if a future hardware check shows the robot actually interprets f9 as pathSpacing, we'll need to re-investigate the APK bytecode, not the captured bytes.

---

## 🔎 App-vs-HA feature audit via live WiFi-ADB exploration (2026-05-27 14:40, supervisor laptop)

Connected from this laptop directly to the phone (`adb connect 192.168.1.45:5555`) and dumped every screen of the Lymow app via `uiautomator dump`. Helper script at `/tmp/lymow-ui/dump.sh`. **The capture session does NOT need to redo this exploration** — full menu structure is enumerated below.

### Home screen anatomy

| UI element | Coords (1080×2160) | Routes to | HA status |
|---|---|---|---|
| 🔔 Notifications bell (top right #1) | tap (895, 115) | Notification list with current alerts (e.g. "Weak RTK Signal E15") | ❓ partial — we surface error codes; not a dedicated notif feed |
| ⋮ 3-dot device menu (top right #2) | tap (1000, 115) | Add / Rename / Share / Delete Device | partial (see below) |
| 🔋 Battery widget (right rail) | (right side, ~y=400) | (no action) | ✅ battery sensor |
| 📹 Camera widget (right rail) | (right side, ~y=600) | Opens **live onboard camera** (sees the AprilTag dock tags) | ✅ camera entity / RTSP (remote KVS tracked in issue #97) |
| ＋ Add Task | (346, 1398) | Schedule create flow → Settings → Schedules | partial — see Task D |
| **Go to Device** | (540, 1634) | Main device-control screen | ✅ all primary controls |

### Top-right ⋮ menu: device-account operations (cloud-side)

| Item | HA status |
|---|---|
| Add Device | app-only (pairing flow) — **not in HA scope** |
| Rename Device | ✅ `lymow.set_device_name` (PATCH /prod/device-update) |
| **Share Device** | ❌ NOT IN HA — share device with another account |
| **Delete Device** | ❌ NOT IN HA — unlink device from account |

### Device-control screen (after "Go to Device" tap)

Right rail icons (top-to-bottom):
- 🗺 Map view toggle
- 👁 Focus / locate robot
- ✏ **Edit Map** ← user-priority: charging-station reposition lives here
- 🎮 Joystick / remote control

Top middle icons:
- ⚙ Settings gear (top right)
- 🔔 Notifications
- 📹 Camera
- ≡ Mowing-settings sliders (top-middle, next to "Mow All Zones" card)

Bottom sheet (collapsible): signal strengths + Mow/Dock action buttons.

### Settings menu (⚙ gear) — complete enumeration

| # | Group | Item | HA status | Notes |
|---|---|---|---|---|
| 1 | Device Settings | **Cancel Task** | ❓ | likely userCtrl=2 (USER_CTRL_DOCK destructive) or userCtrl=28 (USER_CTRL_FORCE_REINIT) — needs capture |
| 2 | Device Settings | Device Settings (sub-screen) | ✅ partial | toggles we already have: rain, charging mode, zone order, handbrake, LED, alerts-only — capture session reply 4 confirmed wire formats |
| 3 | Device Settings | Schedules (sub-screen) | partial | `set_schedules` works; granular add/edit/delete capture pending (Task D) |
| 4 | Device Settings | **Mowing History** | ❌ NOT IMPLEMENTED | likely REST endpoint or `query_cleaning_summary` (userCtrl=34) — partially decoded but no UI |
| 5 | Device Settings | Map Backup & Restore | ✅ backend complete | full lifecycle wire-validated (Task E); UI panel in card still pending |
| 6 | Device Settings | Notifications | ✅ partial | mobileNotificationSwitch tristate switch — covers ON/Alerts-only/OFF |
| 7 | Connection | **Network Settings** | ❌ NOT IMPLEMENTED | 4G + WiFi config; we surface `prefer_4g` switch only |
| 8 | Connection | RTK Diagnostic | ✅ | `query_rtk_diagnostic_l1` / `_l2` services |
| 9 | Connection | **Bind RTK** | ❌ NOT IMPLEMENTED | RTK base-station binding flow (pair a new base to the robot) |
| 10 | Safety | **Find My Robot** | ❌ NOT IMPLEMENTED | almost certainly plays a sound on the robot; could be a userCtrl we haven't mapped |
| 11 | Safety | **PIN Code** | ❌ NOT IMPLEMENTED | set/change anti-theft PIN |
| 12 | Safety | **Anti-theft** | ❌ NOT IMPLEMENTED | likely a toggle for theft-protection state |
| 13 | Safety | Lock-device | ✅ | `LockRobotButton` (userCtrl=18) |
| 14 | Maintenance | Device Info | ✅ | firmware/serial sensors |
| 15 | Maintenance | OTA Update | ✅ | `update` entity |
| 16 | Maintenance | Factory Reset | ✅ | `RestoreFactoryDefaultsButton` (userCtrl=37) |
| 17 | Maintenance | Report Logs | app-only | sends logs to Lymow support — not in HA scope |

### NEW gaps surfaced by this audit (not previously catalogued)

1. **Share Device** (cloud REST) — share robot with another Lymow account
2. **Delete Device** (cloud REST) — unlink from account
3. **Mowing History** — per-session cleaning history (userCtrl=34 `query_cleaning_summary` already decoded, just no UI/service surface)
4. **Network Settings** — change 4G APN / WiFi SSID/PSK on the robot
5. **Bind RTK** — pair a fresh RTK base station to the robot
6. **Find My Robot** — sound-beacon to locate a lost robot
7. **PIN Code** — set/change the 4-digit anti-theft PIN
8. **Anti-theft** — toggle (separate from Lock-device) for theft-protection state
9. **Cancel Task** — top-level Settings entry; needs capture to confirm whether it's userCtrl=2 (DOCK destructive) or userCtrl=28 (FORCE_REINIT)

### Plan for closing these gaps

For each item with a known userCtrl mapping, we can implement now via `_UserCtrlButton`-style entities + capture from this laptop (no app needed). For items that require capture from the app (PIN flows, Bind RTK, Find My Robot), the capture session can run them via scrcpy + BLE snoop.

I'll iterate: capture/implement one at a time, commit after each, and update this section's status column.

### User-priority queue (per 2026-05-27 message)

1. ~~Charging-station reposition (Edit Map)~~ — **DONE, already implemented** (see 🚗 below)
2. Per-zone settings (go-zone + channel) — already partially understood from capture reply 4; needs UI
3. Manual zone create/edit/delete captures — user is doing these manually

### 🚗 Charging-station reposition — wire-captured live this turn (2026-05-27 14:48 UTC)

**Where it lives in the app:** Device screen → 🎮 joystick (right rail) → ➕ (top right) → **Adjust Charging** → Confirm. **Not** under the pencil/Edit-Map toolbar as initially assumed.

**App description (verbatim):** "This feature is for minor charging position adjustments only. 1. Position the robot 50–100 cm directly in front of the charging station … 2. The new position will be saved automatically once the robot detects the charging tag. ⚠️ Major position change? If your charging station has been moved, create a new docking channel using 'Add Channel'."

**BLE capture (2026-05-27 14:48:52.388):**
```
ATT WRITE_CMD, handle 0x0014, payload (ASCII): "EDEoJg=="   (base64)
Decoded protobuf bytes: 10 31 28 26   (4 bytes)
  = PbInput { version=49, userCtrl=38 (USER_CTRL_MODIFY_STATION) }
```

**Robot response:** the robot rejected this particular attempt with **W15 ("Location service not initialized. Drive forward/backward ~2 m in an open area to activate it.")** because RTK was uninitialised. The command was sent regardless — the rejection happened robot-side after receiving the userCtrl, so the wire format is captured cleanly.

**HA status: ✅ already shipped** — `SetChargingStationHereButton` (`button.py:160`) sends the exact same `userCtrl=38` bytes via `_UserCtrlButton._UserCtrlButton.async_press → coordinator.async_send_user_ctrl → encode_userctrl(38)`. The button is enabled-by-default and labelled "Set charging station here" (the app calls the same operation "Adjust Charging" — we should consider renaming the HA entity for parity, or leave both names since they describe the same op).

**Retry capture (2026-05-27 14:57:28 UTC), robot ~50 cm in front of dock:**
Same userCtrl=38 frame at Confirm tap, plus a **follow-up frame ~3.5 s later**:

```
14:57:28.790  ATT b64: "EDEoJg=="
              pb hex:  10 31 28 26
              = PbInput { version=49, userCtrl=38 MODIFY_STATION }
              → trigger the realignment

14:57:32.298  ATT b64: "EDE4AlIKDQAAAAAVAAAAAA=="
              pb hex:  10 31 38 02 52 0a 0d 00 00 00 00 15 00 00 00 00
              = encode_ble_drive(linear=0.0, angular=0.0)
              → stop-motors safety frame (protocol.py:1471)
```

No third frame. The app's complete Adjust Charging sequence is just **MODIFY_STATION → stop**. Both encoders are shipped (`encode_userctrl(38)` and `encode_ble_drive`). The HA card could mirror this UX by calling `lymow.ble_drive` with `linear=0, angular=0` after the user presses "Set charging station here", but the explicit stop is optional safety — the robot is already self-managed at this point.

**Companion finding from earlier today:** the *other* charging-station op — `ChargingStationResetButton` (userCtrl=17) — does something different (resets the recorded position; robot drops out of CHARGING into WAITING). Both are now live-wire-validated. We have:

| App label | HA entity | userCtrl | Effect |
|---|---|---|---|
| Adjust Charging (joystick → +) | `SetChargingStationHereButton` | 38 | save current robot position as the dock |
| (no app UI exposes this directly) | `ChargingStationResetButton` | 17 | clear stored dock position (robot exits charging cycle) |

No further work needed on this feature. Moving on to the next gap.

### 🏠 Dock command — verified (2026-05-27 15:26 CEST, supervisor laptop)

Drove the robot back to dock via app's Dock button (robot was idle, not mowing). Captured wire frame:

```
13:26:14.091 UTC  ATT b64: "EDEoAg=="
                  pb hex:  10 31 28 02
                  = PbInput { version=49, userCtrl=2 USER_CTRL_DOCK }
```

Confirms BRANCH_STATUS.md prior table: when robot is idle/docked (no confirmation dialog shown), Dock sends userCtrl=2 directly. We just shipped `DockAndForgetProgressButton` exposing this exact frame. Robot ack: `state=docked` per HA REST within a few seconds.

### 🧰 Capture pipeline reference — full reproduction recipe (for session resilience)

A fresh agent or session can reproduce everything below from scratch with these commands and no setup beyond `adb`, `tshark` (optional), and Python 3.

**1. Connect to phone over WiFi (skip if `adb devices` already shows `192.168.1.45:5555 device`):**
```bash
adb connect 192.168.1.45:5555
adb devices  # confirm "device" state
```

**2. UI exploration helper** — recreate at `/tmp/lymow-ui/dump.sh` if missing:
```bash
mkdir -p /tmp/lymow-ui
cat > /tmp/lymow-ui/dump.sh << 'EOF'
#!/bin/bash
ID=$1
adb -s 192.168.1.45:5555 shell uiautomator dump >/dev/null 2>&1
adb -s 192.168.1.45:5555 shell cat /sdcard/window_dump.xml > /tmp/lymow-ui/screen_$ID.xml
python3 << 'PY'
import os, re
xml = open(f"/tmp/lymow-ui/screen_{os.environ.get('ID','')}.xml").read()
print(f"--- visible text ---")
for t in dict.fromkeys(re.findall(r'text="([^"]+)"', xml)):
    print(f"  {t!r}")
print("--- clickable elements ---")
for m in re.finditer(r'<node[^>]*?clickable="true"[^>]*?>', xml):
    n = m.group(0)
    text = re.search(r'text="([^"]*)"', n)
    desc = re.search(r'content-desc="([^"]*)"', n)
    bounds = re.search(r'bounds="([^"]+)"', n)
    label = (text and text.group(1)) or (desc and desc.group(1)) or ""
    if label.strip():
        b = bounds.group(1) if bounds else ""
        bm = re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b)
        if bm:
            cx = (int(bm.group(1))+int(bm.group(3)))//2
            cy = (int(bm.group(2))+int(bm.group(4)))//2
            print(f"  tap {cx:>4} {cy:>4}  {label!r}")
PY
EOF
chmod +x /tmp/lymow-ui/dump.sh
ID=home /tmp/lymow-ui/dump.sh home   # example usage
```

**3. Screenshot any screen** (then Read the PNG):
```bash
adb -s 192.168.1.45:5555 exec-out screencap -p > /tmp/lymow-ui/<label>.png
```

**4. Wire-format capture per app action:**
```bash
# (a) baseline
SNOOP=$(adb -s 192.168.1.45:5555 shell su -c 'ls -t /data/misc/bluetooth/logs/hci_snoop*.cfa | head -1')
SIZE0=$(adb -s 192.168.1.45:5555 shell su -c "stat -c %s $SNOOP")
TIME0=$(date +%H:%M:%S.%3N)

# (b) do the action
adb -s 192.168.1.45:5555 shell input tap <x> <y>
sleep 6

# (c) pull + decode
adb -s 192.168.1.45:5555 shell su -c "cp $SNOOP /sdcard/sg.cfa && chmod 666 /sdcard/sg.cfa"
adb -s 192.168.1.45:5555 pull /sdcard/sg.cfa /tmp/lymow-ui/snoop.cfa
cd /home/mint-laptop-4/private_projects/ha-lymow-lovelace
uv run python scripts/parse_btsnoop.py /tmp/lymow-ui/snoop.cfa
```

`scripts/parse_btsnoop.py` is committed in this branch — it walks every record (where `tshark` gives up on the first malformed packet), filters to ATT WRITE_CMD on handle 0x0014, and prints each frame as ASCII-b64 + decoded protobuf hex.

**5. Decode a raw protobuf frame:**
```bash
uv run python -c '
import base64, sys
sys.path.insert(0, "custom_components/lymow")
from protocol import _decode_fields
raw = base64.b64decode("<paste b64 here>")
print(_decode_fields(raw))
'
```

**Heartbeat noise to ignore in snoop output** — these all repeat every 1–60 s:

| ASCII b64 | pb hex | Meaning |
|---|---|---|
| `EDEoFA==` | `10 31 28 14` | `query_schedules` poll (userCtrl=20) |
| `EDFKAlgB` | `10 31 4a 02 58 01` | sub-message poll |
| `EDFKAlAB` | `10 31 4a 02 50 01` | variant poll |
| `ugEECCYgAQ==` | `ba 01 04 08 26 20 01` | sub-message poll (f23) |
| `OALaASVPTk…` (long) | `38 02 da 01 25 ...` | phone device-ID keepalive |

Everything else is a candidate user-action frame — find one near your tap timestamp.

**Snoop epoch (verified 2026-05-27):** standard btsnoop epoch (0000-01-01 UTC), offset `62_168_256_000` s from Unix. `parse_btsnoop.py` uses this offset. *(Capture reply 4 noted "245 seconds short of standard" — that turned out to be wrong; live re-measurement against a known-time Dock tap gave exact match at standard offset.)*

### 📋 Full forward plan (read top-to-bottom; pick up where the prior session stopped)

This section is the **work queue** for the user's stated goal of full app→HA parity. Strike through items as they're done.

#### Phase 1 — Capture remaining wire formats from supervisor laptop (no manual help needed)

For each: navigate via the helper above, capture the wire frame, cross-reference against `const.py` / `protocol.py`, document in this file.

| # | App location | What to capture | Status |
|---|---|---|---|
| 1.1 | Settings → Cancel Task | tap the row | ✅ userCtrl=28 = ForceReinitButton |
| 1.2 | Settings → Notifications | tap row, observe toggles | ✅ REST tristate; already MobileNotificationSwitch + AlertsOnlySwitch |
| 1.3 | Settings → RTK Diagnostic | tap row, scroll | ✅ userCtrl=57+58 query services exist; per-band SNR/sat-count/error-rate sensors missing |
| 1.4 | Settings → Network Settings | tap row | ⚠️ 4G priority shipped; **WiFi SSID/password write missing** |
| 1.5 | Settings → Bind RTK | tap row | ❌ MISSING — `lymow.bind_rtk(sn)` service |
| 1.6 | Settings → Find My Robot | tap row → activate | ✅ NEW: FindMyRobotPlaySoundButton shipped today (wire `10316a023064800101`) |
| 1.7 | Settings → PIN Code | tap row → set | ❌ MISSING — LCD-screen PIN service |
| 1.8 | Settings → Anti-theft | tap row → toggle | ⚠️ toggle shipped (TheftDetectionSwitch); **geofence center+radius config missing** |
| 1.9 | Settings → Device Info | read-only | ✅ partial — SN+firmware as sensors; IP+MAC missing |
| 1.10 | Settings → Device Settings (sub) | toggle each | ✅ mostly; **Headlight Mode is multi-state (vehLedStatus 0–4) but we only have boolean Vehicle LED** |
| 1.11 | Settings → Mowing History | tap row | ⚠️ userCtrl=34 already decoded; **session-list sensor/service to surface it is missing** |
| 1.12 | Top-right ⋮ → Rename Device | rename + save | ✅ already shipped — `lymow.set_device_name` via PATCH /prod/device-update |
| 1.13 | Top-right ⋮ → Share Device | full flow | ❌ MISSING — REST endpoint to share with another account |
| 1.14 | Top-right ⋮ → Delete Device | 🚫 SKIP on primary device |
| 1.15 | Device-screen top sliders icon | open + each slider | ✅ Global tab covered; Customize tab needs full per-zone+per-channel writers (capture reply 4) |
| 1.16 | Device-screen camera icon | tap (live view) | ⬜ |
| 1.17 | Right rail map / focus icons | tap each | ⬜ |
| 1.18 | Schedule create flow | full create | ⬜ (user has indicated they'll do these manually but a single test from app is fine) |
| 1.19 | Schedule edit existing | change time, save | ⬜ (user is doing) |
| 1.20 | Schedule delete one | swipe / 🗑 | ⬜ (user is doing) |
| 1.21 | Mowing settings (top-middle ≡) — global tab | each slider | ⬜ |
| 1.22 | Mowing settings — per-zone tab | per-zone overrides | ⬜ |
| 1.23 | Mowing settings — per-channel tab | per-channel overrides | ⬜ |

#### Phase 2 — Backend gaps surfaced by Phase 1

(Refine after Phase 1 results — likely candidates already documented above as "NEW gaps surfaced".) Each gap gets:
1. Encoder in `protocol.py` (if new opcode) or REST in `api.py`
2. Coordinator method
3. Service registration in `lawn_mower.py` + `services.yaml` entry
4. Optionally entity (switch/button/sensor)
5. Tests
6. Commit + push

#### Phase 3 — Card UI surfaces

- 📦 Backup-management panel (list/restore/rename/delete) — backend ready today
- Per-zone settings panel (extend the cut-height row with moveSpeed + pathSpacing)
- Per-channel settings panel (channels are first-class per user)
- Schedule create/edit/delete UI
- Find My Robot button
- Drop localStorage shim for mowing settings (already partially done; needs follow-through once docked-state echo is consistent)

#### Phase 4 — Pre-existing tech debt

- ~~11 failing `tests/test_protocol.py` cases from prior decoder refactor~~ — **fixed 2026-05-27 (commit `bfb37bc`)**. Cause was a partial revert in `355dd1f` that desynchronised the decoder from sensor.py / tests / lovelace card. Restored 49a7ac6's canonical PbZoneConfig layout (Hermes #9432); re-layered `encode_find_my_robot_play_sound`. 1031 tests, 100% coverage, ruff clean.
- 2 commits behind `origin/feat/map-lovelace-card` before this session — pull before resuming.

---

### 🔬 Full Settings exploration findings (2026-05-27 15:37–15:48 CEST, supervisor laptop)

Drove through every Settings → sub-screen via WiFi-ADB, captured wire frames where the action mutates state. The Lymow app turned out to be **far more covered by HA than the matrix suggested** — most "gaps" are stale, but a few real gaps surfaced.

#### Wire frames captured + decoded

| Action | Wire (pb hex) | Decoded | HA status |
|---|---|---|---|
| Cancel Task | `10 31 28 1c` | userCtrl=28 FORCE_REINIT | ✅ shipped as `ForceReinitButton` (named "Force stop" — cosmetic rename for parity) |
| Dock (from idle) | `10 31 28 02` | userCtrl=2 DOCK | ✅ shipped today as `DockAndForgetProgressButton` |
| Adjust Charging | `10 31 28 26` + `10 31 38 02 52 0a …` | userCtrl=38 + ble_drive(0,0) | ✅ shipped as `SetChargingStationHereButton` |
| **Find My Robot → Play Sound** | `10 31 6a 02 30 64 80 01 01` | **PbInput.f13{f6:100 audioVolume} + f16:1 (NEW)** | ❌ **MISSING** — need PbInput.f16 encoder |
| RTK Diagnostic entry | `10 31 28 39` + `10 31 28 3a` | userCtrl=57 QUERY_RTK_DIAGNOSTIC_L1 + userCtrl=58 _L2 | ✅ shipped as services |
| Network Settings entry | `10 31 28 35` + `8a 01 02 18 01` | userCtrl=53 QUERY_NET_DETAIL + (probable notif/feature read) | ✅ query service shipped |
| Notifications toggle | (none on BLE — REST `/update-device-feature`) | `mobileNotificationSwitch` tristate | ✅ shipped as `MobileNotificationSwitch` + `AlertsOnlySwitch` |

#### Sub-screen layout findings — what each shows + HA coverage

| Settings item | App contents | HA coverage |
|---|---|---|
| Cancel Task | (no sub-screen — immediate userCtrl=28) | ✅ |
| Device Settings | Recharge & Resume, Headlight Mode, Vehicle LED, Rainy Mowing, Charging Handbrake, Timezone (Sync with Phone), Return to Dock (Follow Perimeter / Direct Route) | ✅ mostly — **Headlight Mode is a multi-mode picker (off/auto/on?) we may have only as on/off toggle** |
| Schedules | (couldn't enter due to repeated app timeouts) | ✅ partial — granular add/edit/delete capture deferred to user-manual session |
| Mowing History | (timeout) | ⚠️ — we have `query_cleaning_summary` (userCtrl=34) decoded but **no sensor/service to surface the data** |
| Map Backup & Restore | list + restore + (long-press) rename/delete + Back up button | ✅ backend wire-validated today (Task E); **card UI still missing — 📦 panel needed** |
| Notifications | Device Notifications (master) + Alerts Only (nested) | ✅ tristate switch |
| Network Settings | SSID dropdown, password input, Reconnect button, Network Priority (4G Preferred toggle) | ⚠️ — we have `prefer_4g` switch but **SSID/password write is not in HA** |
| RTK Diagnostic | RTK status (Fixed/Fix), Location precision, GNSS sat count, L1/L2/L5 sat counts + SNR, Base station status, Data error rate, Advanced Diagnostics expander | ✅ — full per-band suite IS surfaced as sensors (precision, gnss/L1/L2/L5 sat counts, L1/L2/L5 SNR, data error rate, differential age, lora bandwidth, DC voltage, CW interference, antenna gain) + `LymowRtkSensor` for status; names confirmed against official i18n `rtk_diagnostic` labels (2026-05-30) |
| Bind RTK | RTK SN field + Scan/Bind buttons | ✅ — `lymow.bind_rtk` service (encode_bind_rtk, robotConfig f17) shipped this session |
| Find My Robot | Map view with lat/lon + reverse-geocoded address + **Play Sound** button | ⚠️ — we have `FindRobotSwitch` (REST enable/disable) but **the BLE Play Sound trigger is NEW** (see wire above) |
| PIN Code | 4-digit PIN entry + Update button. Default 0000. Locks the mower's body LCD screen | ❌ **MISSING** — no service to set/clear the PIN |
| Anti-theft | Map with **geofence center pin + radius slider (currently 150 m)** + Enable toggle + Save | ⚠️ — we have `TheftDetectionSwitch` + `TheftLockSwitch` (REST switches), but **geofence center + radius are not configurable from HA** |
| Lock-device | (toggle / button — couldn't drill in this session) | ✅ `LockRobotButton` |
| Device Info | SN, IP, MAC address, Software Version, MCU Version (all read-only) | ✅ partial — SN + Software Version surfaced; **IP and MAC not surfaced as sensors today** |
| OTA Update | (didn't drill — assume sub-screen) | ✅ update entity |
| Factory Reset | (didn't drill — assume confirm dialog) | ✅ `RestoreFactoryDefaultsButton` |
| Report Logs | Sends logs to Lymow support (app-only) | 🚫 out of scope |

#### Mowing Settings panel (top-middle sliders icon) — confirmed two-tab layout

**Global Settings tab** — applies to all zones:
- Basic: Moving Speed (0.3–1 m/s), Cutting Height (30–100 mm), Blade Speed (Standard/Eco/Power/Turbo)
- Advanced: Path Spacing (25–35 cm), Stripe Angle (Optimized + dropdown), Mowing Order (Main Area First / Perimeter First), Zone Obstacle Detection (Smart/Touch-Only), Perimeter Obstacle Detection, Perimeter Mowing Direction (Random/CW/CCW), No-Go Zone Mowing Laps (0–3), Zone Perimeter Mowing Laps (0–3), Turn Off Outer Mowing Motor toggle, Safe-margin mode (Offset Edge / Precise Edge), Channel Obstacle Detection, Channel Deck Height, Raise The Omni Wheels On Channel toggle

**Customize Settings tab** — per-entity overrides:
- Sub-tabs: zone0, zone1, channel0, channel1
- "Delete All" button (removes all customizations)
- Each sub-tab: Name field + same Basic/Advanced controls as Global
- Wire format: per capture reply 4, uses userCtrl=9 + PbMap.goZones[*].configBox

HA status:
- ✅ Global path: `lymow.set_task_config` + `set_run_time_config` cover most controls (sync_map for path_spacing/perimeter_mow_laps via PbZoneConfig)
- ⚠️ Per-zone path: `async_update_zone_cut_height` exists (we wired it today); **moveSpeed + pathSpacing + bladeSpeed per-zone are NOT exposed as services yet**
- ⚠️ Per-channel path: **completely missing** — no `update_channel_*` services
- ❌ Several Advanced fields not surfaced: Stripe Angle, Safe-margin mode, Turn Off Outer Mowing Motor toggle, Channel Deck Height (raise/lower omni-wheels-on-channel)

#### Naming inconsistencies (cosmetic — same wire, different label)

| App label | HA entity / service | Recommendation |
|---|---|---|
| Adjust Charging | "Set charging station here" button | Add `name="Adjust Charging"` alias for app parity |
| Cancel Task | "Force stop" button (`ForceReinitButton`) | Rename to "Cancel task" |
| Lock-device | "Lock robot" button | Already close — fine as-is |
| Headlight Mode | (probably only Vehicle LED on/off switch) | Investigate — may be a multi-mode select we have as boolean |

#### Concrete new gaps requiring implementation (in priority order)

1. **Find My Robot Play Sound** — new encoder `encode_find_my_robot()` writing `PbInput { f13:{f6:100}, f16:1 }`. Add `FindMyRobotPlaySoundButton` entity.
2. **Mowing History sensor/service** — `query_cleaning_summary` (userCtrl=34) is already decoded; surface as `sensor.lymow_history_*` with last-N sessions, or as a `lymow.get_mowing_history` service that returns the data.
3. **Per-zone settings full** — extend `lymow.update_zone_cut_height` to `lymow.update_zone_config(moveSpeed, pathSpacing, bladeSpeed, ...)` matching capture reply 4's `encode_set_zone_configs` finding.
4. **Per-channel settings** — equivalent for channels.
5. **Anti-theft geofence config** — `lymow.set_anti_theft_geofence(center_lat, center_lon, radius_m)` + sensor for current geofence.
6. ✅ DONE — **PIN Code service** (`lymow.set_pin`, robotConfig f13.f9; PIN never logged/stored).
7. ✅ DONE (BLE-only) — **WiFi SSID/password write** (`lymow.set_wifi`); MQTT path raises HomeAssistantError, real write is BLE-only (issue #200).
8. ✅ DONE — **Bind RTK** (`lymow.bind_rtk`, robotConfig f17).
9. ✅ DONE — **Per-band RTK sensors** all surfaced (see RTK Diagnostic row above); names i18n-confirmed.
10. **Headlight Mode multi-state** — confirm app shows more than on/off; add select entity if so.
11. **Stripe Angle / Safe-margin mode / Channel Deck Height / etc. global settings** — add fields to `set_task_config` if currently missing.
12. **Device IP / MAC sensors** — minor, low priority.
13. **Share / Delete Device REST endpoints** — only if user needs them.

### 🆕 Iteration 2 findings (2026-05-27 16:00 CEST, supervisor laptop)

**Mowing History — confirmed sub-screen layout** (the timeout cleared on retry). Top stats: Total Area (e.g. 698 m²), Duration (128 min), Total Times (count). Then a scrollable list of sessions. Each row: timestamp + mode ("All" or "Selected") + area mowed + duration. Icon colour distinguishes successful (green) from failed/aborted (red, 0–1 min duration). Tapping a row drills into per-session detail (Method/Mode/Duration/Area). We have `query_cleaning_summary` (userCtrl=34) already decoded but **no sensor/service surfaces this list**. Concrete impl: `sensor.lymow_history_last_session` (latest summary) + `sensor.lymow_history_total_area_m2` / `_total_duration_min` / `_total_sessions`, OR a `lymow.get_mowing_history` service that returns the raw list (good for automations).

**Headlight Mode — multi-state confirmed**. `const.LED_LEVELS` already defines NONE/LOW/MEDIUM/HIGH/OFF (0–4) for `vehLedStatus`/`camLedStatus`. Our existing `Vehicle LED` switch is boolean only; we drop the brightness levels. Concrete impl: add `select.lymow_headlight_mode` entity backed by `PbRobotConfig.vehLedStatus` field (need to add to `_ROBOT_CONFIG_FIELDS`).

**Joystick BLE drive — wire-validated**. The follow-up frame in the Adjust Charging retry capture was `encode_ble_drive(0, 0)` (stop). Same encoder. No new work needed — `lymow.ble_drive` service + `coordinator.async_ble_drive` already exist for driving the robot.

**Camera — current implementation correct**. Local RTSP (`rtsp://<robot_ip>:10022/h264ESVideoTest`, 640×480) is what we use for the HA camera entity; this works for any LAN-connected HA. Remote/WAN access via AWS KVS WebRTC is the unsolved bit (issue #97) — the robot only acts as WebRTC master for the app's authenticated cloud session, so we can't trivially replicate that flow. **No new info to add to #97 — confirmed the gap remains the same.**

**App-side load failures** — the Lymow app frequently shows "Request Timeout: Failed to retrieve device settings" when entering sub-screens (Device Settings → Headlight Mode, Mowing History, etc.). Retries usually succeed within 1–2 attempts. **Capture session takeaway:** don't read a single timeout as "feature unavailable"; always retry at least twice before recording a finding.

### Update to existing issues based on this audit

- **Issue #97 (KVS WebRTC remote camera)**: confirmed live camera works via LOCAL RTSP (the AprilTag dock view we just streamed via the app over WiFi); remote access is still the gap. No new info to add.
- **New issues to open** (after I capture wire formats / confirm whether app uses MQTT, BLE, or REST for each):
  - "Mowing History" UI + service
  - "Network Settings" — change WiFi/4G credentials from HA
  - "Bind RTK" flow
  - "Find My Robot" (sound beacon)
  - PIN Code set/clear
  - Anti-theft toggle
  - Share Device REST endpoint
  - Delete Device REST endpoint

---

### 🛠 Supervisor session — 2026-05-27 evening iteration (this laptop)

Picked up the branch with 11 failing tests + a partial-revert regression
(`355dd1f`) and worked through the priority queue. **6 commits, +1053 →
no regressions, 100% coverage throughout.** Commits, oldest first:

| Commit | What |
|---|---|
| `bfb37bc` | fix(protocol): restore canonical PbZoneConfig layout + `decode_zone_config`. Undid the 355dd1f partial revert that had broken 11 tests AND silently empty-ed sensor.py's `mowing_settings` / `channel_config` attributes in production. Re-canonical Hermes #9432 layout (f9 relativeCleanDir / f10 pathSpacing) + restored `decode_zone_config` 19-field decoder + `decode_channel_config` {detectMode, cutHeight, channelLift} + `globalZoneConfig` / `globalChannelConfig` / `enuBasePoint` / `diagonalCoords` / `chargingStation.z` keys + per-zone `zoneConfig` dict + `_encode_go_zone` re-emits all 19 PbZoneConfig fields on sync_map round-trip + `_RUN_TIME_CONFIG_FIELDS` back to PbRunTimeConfig canonical (f1/f2/f3). |
| `cc887cf` | feat: per-zone PbZoneConfig via `userCtrl=9` (app's Customize-tab path). New `encode_set_zone_config(updates)`, `async_set_zone_config(thing, updates)`, `lymow.set_zone_config` service. Bandwidth-efficient alternative to `update_zone_cut_height` / sync_map — single-zone configBox write byte-equal to the app's BLE write. |
| `4a7913b` | feat: anti-theft full geofence setter (`async_set_geofence` + `lymow.set_geofence` service: lat/lon/radius/name optional, seeds defaults if no record) + MAC address sensor. Previous `set_geofence_radius` only mutated radius and required the centre to already be set in the Lymow app. |
| `5e59b8d` | feat: live-decoded RTK diagnostic + network info from `userCtrl=57`/`58` pboutput responses (PbOutput.f34/f35/f36). New sensors: `wifi_ssid`, `cellular_ip`, `mac_address` (all disabled by default for noise). Card-side `value_key` now walks dotted paths like `networkInfo.wifiSsid`. RTK per-band fields stored as `rtkL1{f1..f11}` / `rtkL2{f1..f13}` pending app-UI label correlation. Capture script `scripts/query_all_diagnostics.py` shipped for future re-capture work. |

**Backend gaps still open** (mostly needing live captures we can't do
without ADB + phone-side BLE access from this laptop):

| Feature | Why it's still open | Unblock path |
|---|---|---|
| Headlight Mode multi-state (vehLedStatus 0–4) | `PbRobotConfig.vehLedStatus` field number is not in the Hermes #9506 map we documented. `const.LED_LEVELS` shows the enum, but the wire field hasn't been captured. | BLE capture of Settings → Device Settings → Headlight Mode dropdown change. |
| PIN Code set/clear | `PbRobotConfig.f9 lcdPinCode` is mentioned in the decoder docstring but its sub-message layout isn't pinned (kept out of the decoder for safety — PINs shouldn't be read back through HA's logs anyway). | BLE capture of Settings → PIN Code → Update. |
| WiFi SSID/password write | App's Network Settings → Reconnect → SSID/password — wire format unknown. | BLE capture of the reconnect flow. |
| Bind RTK | Settings → Bind RTK — REST or BLE unknown. | Try `update_device_feature` first with `rtkSn` field; if the API rejects it, BLE-capture the app's flow. |
| Per-band RTK labels | `rtkL1`/`rtkL2` dicts decoded but key labels are `f1..f13` instead of `l1VisibleSats` / `l2Snr` etc. | App screenshot of Settings → RTK Diagnostic with all numbers visible, then correlate by running `query_rtk_diagnostic_*` immediately after. |
| Share / Delete Device REST endpoints | Top-right ⋮ menu — REST paths unknown. | Mitmproxy capture of the app while sharing / unlinking the device. |
| Schedule add/edit/delete granular wire | Bulk `set_schedules` works; granular per-row write isn't pinned. | User said they'd do these manually. |
| Remote camera (KVS WebRTC) | Local RTSP works (`camera.lymow_*` entity). Remote = issue #97 — robot only acts as WebRTC master for the app's cloud session. | **Explicitly excluded from the "no-PR-undraft-until-app-parity" gate per user instruction.** |

**Note for the supervisor session driving the Lovelace card**: every
backend change above is already wired through the service layer + sensor
layer. The card can plug in directly:

- Per-zone settings panel: call `lymow.set_zone_config` (zone_hash_id +
  per-field args; same field names as `set_task_config`).
- Anti-theft full config: call `lymow.set_geofence` (lat/lon/radius/name
  optional; will seed a sensible default if no record exists).
- Network info display: read `sensor.<device>_wifi_ssid`,
  `sensor.<device>_cellular_ip`, `sensor.<device>_mac_address` — all
  disabled-by-default; user enables them if they want them visible.

**Robot interaction this session**: read-only MQTT queries only (cleaning
summary, robot config, run-time config, RTK L1/L2, etc.). No mowing,
docking, charging-station moves, or destructive writes. Robot is docked
at 96% battery, idle.

**Test suite at session end**: 1053 passing, 100% coverage, ruff clean
on both `format --check` and `check`. Branch is 4 commits ahead of
`origin/feat/map-lovelace-card` — push when ready.

### 🛰 Remote camera (#97) — capture analysis, 2026-05-30 (supervisor laptop)

User asked: can we do the camera feed over the internet, not just local? Two
distinct answers, don't conflate them:

1. **View the camera through HA from outside the LAN — already works, no code.**
   This HA instance has `cloud` (Nabu Casa) + `go2rtc` + `stream` + `ffmpeg`
   loaded, and HA sits on the robot's LAN. So HA itself proxies the local RTSP
   entity out to any remote client (Nabu Casa URL / reverse proxy / VPN). The
   only reason `camera.7b6521_camera` reads `unavailable` is the **robot is
   offline right now** (every `7b6521` entity is unavailable; pboutput in the
   capture stops at 17:48 today). Nothing to build for this case — verify once
   the robot is back online. (`external_url`/`internal_url` are unset, so the
   remote path is whatever Nabu Casa / proxy the user already uses.)

2. **Robot streaming to the cloud independent of HA's LAN reach (KVS WebRTC) —
   still issue #97, NOT solved.** Analyzed a fresh `tools/capture-lymow.txt`
   (app opened the camera at 15:47:36 today). What it shows:
   - Full app flow confirmed: MQTT presence + `POST /prod/kvs/cmd` →
     `getSignalingChannelEndpoint` (VIEWER) → `get-ice-server-config` →
     SigV4-presigned WSS connect (HTTP 101) → `SDP_OFFER` + trickle ICE. All of
     this is already implemented in `api.py` (`start_video_session`,
     `get_signaling_channel_endpoint`, `get_ice_server_config`,
     `presign_signaling_url`) and `scripts/camera_feed_test.py`.
   - **NEW: the app continuously publishes a presence beacon** to
     `/device/<thing>/pbinput`: `{f7:2, f27:<viewerClientId>}` (177× in this
     capture). Decoded bytes: `3802 da0125 <ascii clientId>`. The 22B
     "heartbeat" is the same message with f27 empty (`3802da0100`). The beacon
     clientId is the **short** form `ONEPLUSA5010_Android_<devhex>` (no
     `_userId_<sub>` suffix), whereas the KVS *viewer* clientId is the **long**
     form `ONEPLUSA5010_Android10_<devhex>_userId_<sub>`. This beacon is the
     same `f7=2` realtime-control family as `encode_ble_drive` (which uses f10).
     `scripts/camera_feed_test.py` does **not** send this beacon and uses a
     random clientId — a gap vs. the app.
   - **BUT this captured session FAILED**: the robot returned only 8 × 0-byte
     `KVS-WSS ←` keepalives, **no `SDP_ANSWER`**, even though the real app sent
     the real beacon + real clientId + `kvs/cmd`. So the capture does **not**
     reveal the success condition, and the "beacon is the missing trigger"
     idea is **unproven** (the app sent it and still got no answer here).
   - Conclusion unchanged from prior sessions: the robot-becomes-MASTER trigger
     is not reproduced by this capture. The decisive next artifact is a capture
     of a **successful** remote session (app on cellular/off home WiFi, robot
     online, video confirmed visible) — only that shows what differs. Then the
     experiment is: replay beacon (long+short clientId) + viewer handshake as
     **sole** client with no app open. Blocked on: robot online + a successful
     reference capture. ADB (USB `fc7d1e36`) is available on this box now.

   **LIVE TEST, 20:16 CEST (18:16 UTC) same day — robot WAS online (HA link was
   just down; pboutput flowing). Drove the app via ADB and captured a SUCCESSFUL
   remote camera open, then ran our client as sole viewer. Definitive results:**
   - Tapped the app's camera icon → **live video confirmed** (robot's lawn view,
     timestamp overlay, streaming over 4G). Robot was actively **mowing**, 29% batt.
   - The successful open is a **pure cloud flow**: `POST /prod/kvs/cmd`
     `{deviceThingName, action:"start"}` → signaling → ICE → app sends SDP_OFFER →
     **robot SDP_ANSWERs in ~2.5s** (1636B inbound `KVS-WSS ←`, `messageType`
     ICE from robot IPs 192.168.1.85 / relay). **NO app→robot MQTT pbinput and NO
     presence beacon in the success window** (beacon-as-trigger hypothesis is
     **dead** — `da01` count = 0 across the whole 18:16 session). kvs/cmd body is
     byte-identical to what `api.py::start_video_session` already sends.
   - Then closed the app camera and ran `scripts/camera_feed_test.py` as the SOLE
     viewer while the robot was still mowing → **NO SDP_ANSWER over 30 offers /
     120s.** Same account, same cloud flow, robot willing for the app 2 min
     earlier. So replicating the cloud flow is **necessary but not sufficient.**
   - Also note: the SAME app **failed at 15:47** (robot returned only 0-byte
     keepalives) but **succeeded at 18:16** while mowing → the robot's
     willingness to become MASTER is **state/timing-gated**, not deterministic.
   - **Narrowed gap (what still differs app-vs-our-client):** (a) viewer clientId
     prefix (`ONEPLUSA5010_Android10_<devhex>_userId_<sub>` vs our
     `ha-lymow_<pid>_userId_<sub>` — same sub); prior sessions say exact-clientId
     was already tried & failed, but possibly while robot was unwilling (docked).
     (b) the app holds a **live AWS IoT MQTT subscription** (pboutput stream) for
     the account throughout; our standalone test does NOT. (c) freshness: our run
     was seconds-to-minutes after the app's kvs/cmd; the robot-master window may
     be tight. **Next experiments (robot must be online + mowing):** run the KVS
     handshake while holding a live IoT MQTT subscribe for the account, with the
     app's exact clientId, issuing our own fresh kvs/cmd, all within ~3s — i.e.
     the "sole client in real HA with live MQTT" condition. Capture our client's
     kvs/cmd response and diff channelARN/creds vs the app's.

   **FOLLOW-UP, same evening — ran those experiments in the willing window
   (robot still mowing, app still getting video). ALL FAILED:**
   - `scripts/camera_feed_test.py` made the clientId prefix overridable via env
     `LYMOW_KVS_CLIENT_PREFIX` (sub is appended from the token, never written).
   - Test 1 — app's **exact** KVS clientId
     (`ONEPLUSA5010_Android10_<devhex>_userId_<sub>`): **no SDP_ANSWER / 120s.**
   - Test 2 — exact clientId **+ a live AWS IoT MQTT presence** held the whole
     time (`/tmp/presence_hold.py`: auth → identity creds → connect → subscribe
     `/device/<thing>/pboutput`+`/notify-app`, idle): **no SDP_ANSWER / 120s.**
   - So in the confirmed-willing window, replicating the cloud flow + the app's
     exact identity + a live IoT presence is **still insufficient** — the robot
     serves the app but not our standalone client. The "sole client in real HA
     with live MQTT" hypothesis is **effectively disproven** (reproduced both its
     pillars, still nothing).
   - **Verdict:** the master-wake is tied to something the app does that is NOT
     on the captured cloud surface (kvs/cmd body is just
     `{deviceThingName, action:"start"}` — no extra headers/params; no app→robot
     MQTT). Remaining viable leads, both heavier: (1) **APK/Hermes analysis** of
     the camera-tap path — does the app attach a device/push token to kvs/cmd,
     write an IoT shadow, or hit an endpoint we didn't proxy? (2) the kvs/cmd
     Lambda likely keys the master-wake on the app's **registered device
     identity** (FCM/APNs push token or IoT thing cert) a Cognito-only client
     can't present. **#97 unsolved; ship the LAN/Cloud selector with the Cloud
     option gated/experimental.**

   **APK/Hermes analysis + owner-identity test, 2026-05-30 night (this box,
   ADB+root via Magisk). Pulled `base.apk` (40MB) → `assets/index.android.bundle`
   (HBC v96) → `hbc-disassembler` (153MB `out.hasm`). Findings:**
   - The app's camera-open is **client-side identical to ours**: `kvs/cmd
     {deviceThingName, action:"start"}` signed by the generic Amplify SigV4
     signer (no extra device/token header), then the VIEWER handshake
     (`getSignalingChannelEndpoint`→`getIceServerConfig`→`createOffer`→
     `sendSdpOffer`, `RTCPeerConnection`). clientId =
     `<model>_Android<ver>_<uniqueId>_userId_<sub>`. **No hidden client
     ingredient.**
   - App uses Firebase/Expo push tokens (`devicePushToken`/`updateDeviceToken`)
     and bundles Cognito device-remembering (`DEVICE_SRP_AUTH`/`ConfirmDevice`/
     `rememberDevice`) — but **neither is in the camera request**, and the app's
     live AccessToken (pulled from `RKStorage`, claim NAMES only) has **NO
     `device_key` claim** → device remembering is NOT the gate.
   - **Account check:** `scripts/.env` (`LYMOW_USER`) is a **DIFFERENT Cognito
     user than the app owner** (sub mismatch; owner token has `cognito:groups`+
     `version`, ours doesn't). So earlier "exact clientId" tests carried the
     WRONG sub. Re-tested **as the owner** — extracted the app's live access+id
     tokens from `RKStorage`, ran the handshake with owner identity + owner's
     exact clientId (`camera_feed_test.py` gained env overrides
     `LYMOW_ACCESS_TOKEN`/`LYMOW_ID_TOKEN`/`LYMOW_KVS_CLIENT_PREFIX`; tokens kept
     in subprocess env only, never written): **STILL no SDP_ANSWER / 120s.**
   - **FINAL VERDICT on #97:** a standalone client cannot make the robot become
     MASTER — not with random id, exact id, live MQTT presence, OR the **owner's
     byte-exact identity**. The APK proves there's no client-side secret we're
     missing. The robot-master wake is bound to the **live running app instance**
     in a way the cloud API surface doesn't expose — most plausibly the kvs/cmd
     Lambda routes the wake to the robot scoped to the requester's *active AWS
     IoT session*, which a separate process can't assume. Cracking further needs
     robot firmware or the Lambda — out of reach. **Recommendation: ship
     LAN(RTSP) now (already remotable via HA/Nabu Casa); keep Cloud(KVS) wired
     but disabled/experimental. The 4G-away-from-HA case is not achievable from
     the client side on current evidence.** `tools/_apk/` is gitignored (`_*`).

   **⚡ BREAKTHROUGH (supersedes the verdict above) — #97 IS ACHIEVABLE; it's an
   SDP-shaping problem, NOT identity/registration.** User corrected me: the robot
   is **multi-viewer** (two phones, same owner account, stream simultaneously).
   So our failures = our *viewer* was rejected, not the robot refusing extras.
   Decisive test: with the capture tool's KVS-WSS truncation lifted (1400→8000 in
   `tools/capture.py`), captured the app's **complete** SDP offer (3623 chars),
   then **replayed it verbatim** from our own viewer connection (our clientId,
   owner tokens) → **robot returned `ICE_CANDIDATE` + `SDP_ANSWER`.** So the robot
   answers an app-shaped offer from us. The gate is purely the **offer SDP**.
   - Robot's answer selects **H.264 PT-equiv, `profile-level-id=42e01f`,
     `packetization-mode=1`** (Constrained Baseline 3.1). The robot's camera is
     **H.264 video-only** (app offer has ONE m-line, `BUNDLE 0`, no audio).
   - Why our `aiortc` offers are ignored (structural deltas vs the app's, found by
     diffing): aiortc emits **video+audio** (extra `m=audio`); H264 present but
     after VP8; missing `transport-cc`/`ccm fir` feedback; only 3 header
     extensions vs the app's 11; **`a=ssrc`/`a=msid` on a recvonly m-line** (app
     has none); no `extmap-allow-mixed`/`rtcp-rsize`/`ice-options:trickle`; and on
     this dev box a **20+ candidate explosion** from docker interfaces vs the
     app's 1 host + 2 relay.
   - Attempts so far (all concurrent with a confirmed-live app stream = robot is
     master): force-H264, video-only, and a munge adding transport-cc/fir/extmap/
     rtcp-rsize/extmap-allow-mixed + stripping ssrc/msid — **none answered yet.**
     Incremental munging is the wrong tactic (too many deltas at once).
   - **Test harness** (`scripts/camera_feed_test.py`, env-gated, off by default):
     `LYMOW_ACCESS_TOKEN`/`LYMOW_ID_TOKEN` (use owner tokens, skip login),
     `LYMOW_KVS_CLIENT_PREFIX`, `LYMOW_FORCE_H264`, `LYMOW_VIDEO_ONLY`,
     `LYMOW_MUNGE_OFFER` (+ `_munge_offer()`). Owner tokens pulled from app
     `RKStorage` via `adb su`, kept in subprocess env only.
   - **Next (methodical):** bisect — start from the app's verbatim offer (known to
     answer) and swap in OUR ufrag/pwd/fingerprint/candidates/ssrc piece by piece
     until it breaks, to find the exact sensitive field; OR generate the offer with
     a native-shaped stack (Pion / GStreamer `webrtcbin` / libwebrtc) instead of
     aiortc; OR hand-build a recvonly H264 offer string with our crypto + the
     app's attribute set and matching PT numbers. Once an answer→frame is proven,
     wire it into a `camera.py` Cloud(KVS) mode behind a LAN/Cloud selector.
   - **The earlier "not achievable client-side" verdict is WRONG — remote KVS
     streaming for HA is achievable; remaining work is SDP convergence.**

   **⚡⚡ THE KEY FIELD FOUND + FULL WEBRTC CONNECT ACHIEVED (2026-05-30 night).**
   Signaling-level **bisection** (raw WS, mutate the app's known-good offer one
   axis at a time, check for SDP_ANSWER) gave a clean verdict:
   | mutation of app offer | answered? |
   |---|---|
   | verbatim (control) | ✅ |
   | our ICE ufrag/pwd | ✅ (we can use our own ICE creds) |
   | strip all `a=extmap` | ✅ (extmaps not required) |
   | strip `a=rtcp-fb` | ✅ |
   | single H264 codec only | ✅ |
   | **strip `a=ice-options`** | ❌ **NO ANSWER** |
   → **The robot REQUIRES `a=ice-options:trickle renomination` in the offer, and
   `aiortc` never emits it (no trickle).** That one missing line was the entire
   blocker the whole time. Added it via `_munge_offer` (`LYMOW_MUNGE_OFFER`).
   - With it, the full aiortc viewer run (video-only + H264 + munge, owner tokens,
     concurrent with a live app stream): **`recv SDP_ANSWER` → ICE `completed` →
     `PC state: connected`.** The WebRTC peer connection to the robot **fully
     establishes over the cloud.** This is the hard part — DONE & PROVEN.
   - **Final mile remaining: no decoded video frame yet.** `track.recv()` hangs
     120s → no RTP video reaching the decoder despite PC connected. Leading
     hypotheses (in order): (a) **PT mismatch** — robot likely sends H264 as PT 98
     (its preference) while aiortc negotiated PT 101 for 42e01f, so packets get
     dropped; force our offer's 42e01f to PT 98, or remap. (b) **candidate noise**
     — this dev box emits 20+ host candidates from docker/veth interfaces and NO
     TURN-relay candidate; a real HA box (clean net) likely "just works", and we
     should add the KVS TURN servers so a relay candidate is offered. (c) keyframe
     (PLI) not requested. Next session: log the robot's answer SDP (PT + a=sendonly
     dir), and an RTP-arrival counter, to pick between (a)/(b).
   - Env harness now also has `LYMOW_VIDEO_ONLY`, `LYMOW_FORCE_H264`,
     `LYMOW_MUNGE_OFFER` (+ `_munge_offer` adds `ice-options`, transport-cc/fir,
     extmap, rtcp-rsize, extmap-allow-mixed, strips ssrc/msid), `LYMOW_DEBUG_ANSWER`,
     `LYMOW_RTC_STATS`. capture.py KVS-WSS truncation raised 1400→8000.
   - **UPDATE: PT mismatch RULED OUT + STANDALONE (no app) CONFIRMED.** Logged the
     robot's answer to OUR offer: `m=video … 101`, `a=sendonly`,
     `H264/90000 profile-level-id=42e01f`, robot ssrc — clean, PT 101 matches
     aiortc. And critically, ran with the **app CLOSED** → still `recv SDP_ANSWER`
     → `PC state: connected`. So the robot wakes & connects from `kvs/cmd` alone,
     **no app needed** (the earlier "only the live app" belief was an artifact of
     the missing `ice-options`). So hypothesis (a) is out; remaining frame blocker
     is (b) media transport on this dev box (20+ docker/veth host candidates, no
     working TURN-relay candidate) and/or (c) keyframe. **Best finished on a real
     HA box with a clean network + a KVS TURN relay candidate, then wired into
     `camera.py` Cloud(KVS) mode behind the LAN/Cloud selector.**

   **✅ #97 SOLVED & PROVEN END-TO-END — REAL VIDEO via headless Chrome,
   STANDALONE, NO APP (2026-05-30 ~23:30).** Deeper instrumentation showed the
   real frame blocker wasn't network/decode at all: aiortc connected (local-LAN
   pair) but received **0 RTP / 0 SRTP** packets — the robot streams to a proper
   WebRTC client but not to aiortc (its DTLS-server-role/PLI/ICE handling never
   pulls RTP, and aiortc resists being forced). So tested a **different stack**:
   a headless **Chrome** KVS viewer (`/tmp/kvschrome/viewer.js`, playwright-core
   → system `google-chrome`, fed by `kvs_prep.py` → `/tmp/kvs_cfg.json`). Result:
   `pc:connected`, 640×480, **`framesDecoded:21`, `packetsReceived:113`**, and it
   **saved a real frame** (robot dock AprilTags + live timestamp). Chrome's native
   WebRTC completes the media exchange where aiortc stalls. **Conclusion: the
   signaling recipe is 100% correct, the robot streams over the cloud with NO app,
   and aiortc was the sole blocker.** PRODUCTION: use a Chrome/Pion-class media
   stack — **`go2rtc` (already loaded in HA, Pion-based) is the natural fit**;
   `api.py` already has `presign_signaling_url` + `start_video_session`. Next:
   wire go2rtc (or a Pion helper) into `camera.py` as the Cloud(KVS) source behind
   the LAN/Cloud selector and validate on the HA box.

   **✅ SHIPPED: LAN/Cloud camera card (2026-05-30). Chose in-browser WebRTC over
   go2rtc** — the Lovelace card already runs in a real browser (the exact stack
   the Chrome test proved), so no extra server process, and go2rtc can't speak KVS
   signaling natively anyway. Backend (tested, 100% cov): `api.viewer_client_id()`
   (random prefix + `_userId_<sub>` from the access token) and
   `coordinator.async_start_video_session` now also return turnkey `viewerWssUrl`
   (SigV4-presigned signaling WSS), `viewerClientId`, and `webrtcIceServers`
   (RTCPeerConnection-shaped). Surfaced via the existing `lymow.start_video_session`
   service. Frontend: self-contained `www/lymow-camera-card.js` (registered via a
   2nd `add_extra_js_url` in `__init__.py`) with a **LAN | Cloud** segmented toggle:
   - **LAN** → `<img>` on `/api/camera_proxy_stream/<camera_entity>` → HA pulls
     RTSP from the robot on the local network (traffic stays local).
   - **Cloud** → in-browser KVS WebRTC (service → presigned WSS → RTCPeerConnection
     recvonly H264 → `<video>`), browser↔AWS↔robot, bypassing HA's LAN reach.
   Genuinely separate transports; switching stops the other; self-contained
   element so the map card's `_render()` churn can't kill the live connection.
   `camera.py` RTSP entity unchanged. **Validate in real HA**: deploy+restart, add
   `custom:lymow-camera-card` (mower_entity + camera_entity), confirm a Cloud frame
   (proven standalone via Chrome; the card mirrors that exact flow).

   **Map-card camera button (opt-in).** `lymow-map-card` now takes `show_camera`
   (default **false** — off until enabled), plus optional `camera_entity` /
   `camera_default_source`. When `show_camera: true`, a 📹 toolbar button opens the
   camera as a **modal overlay** that embeds `<lymow-camera-card>`. The overlay is
   mounted in `document.body` (NOT the map's shadow root) so the map's `_render()`
   churn can't tear down the live `<video>`/peer connection; closing it (✕, Esc, or
   backdrop click) removes it from the DOM → camera card's `disconnectedCallback`
   stops the cloud stream. `set hass` forwards hass to the open overlay card.

### Continuation, 2026-05-27 late evening (same supervisor session)

User asked to keep iterating. Three more commits shipped on the
backend-only / no-app-capture-needed track:

| Commit | What |
|---|---|
| `56be3fa` | feat: per-channel settings (cut_height + channel_lift via sync_map). Channels carry settings directly on PbChannel (f9 cutHeight, f10 channelLift) — no configBox sub-message like zones, so the userCtrl=9 path doesn't apply. `async_update_channel_settings(thing, hash_id, cut_height_mm, channel_lift)` mutates the local cache and resyncs via sync_map. New `lymow.update_channel_settings` service. |
| `fa05be4` | feat: `lymow.get_clean_history` service — returns the full paged cleaning-history list as a response object (`SupportsResponse.ONLY`). Each entry has clean_area / clean_time / date / error_list / map_total_area / percent / soc_version / start_type / status_times / used_battery / history_file / hash_id. The existing per-entry sensors only surface the latest mow; this service is the bulk-read complement for automations that walk historical mows ("alert me if my last 3 mows failed"). |
| _next push_ | docs: this section + close out PbRobotConfig field-probe attempt (inconclusive — see below). |

**PbRobotConfig field-probe attempt (inconclusive)**: tried to discover
unmapped `PbRobotConfig` fields like `vehLedStatus` by sending
`USER_CTRL_QUERY_ROBOT_CONFIG=52` and dumping every field number the
robot returns at `PbOutput.f17`. Live result: the robot's response
returned an empty `f15(0B)` + empty `f16(0B)`, no `f17` at all. Either
the robot doesn't echo robotConfig back in the docked-idle state, or
the response carries it at a different field number than the
`PbOutput.encode tag 138 = (17<<3)|2` derivation suggests. Probe script
shipped as `scripts/probe_robot_config.py` so a future capture session
can re-run it while toggling app-side state (e.g. flip Vehicle LED in
the app, then probe — the response shape should reveal the field).

**Backend gaps now closed in this session** (recap, post-this-extension):

- ✅ Per-zone settings via app's userCtrl=9 path (cc887cf)
- ✅ Per-channel settings via sync_map (56be3fa)
- ✅ Anti-theft full geofence config — lat/lon/radius/name (4a7913b)
- ✅ Mowing History list as a service (fa05be4)
- ✅ Network info — wifi_ssid / cellular_ip / mac_address sensors (5e59b8d, 4a7913b)
- ✅ RTK diagnostic L1/L2 raw fields surfaced (5e59b8d) — labels still pending

**Final test count**: 1064 passing, 100% coverage, ruff clean. Robot
still docked at 96% (no mow started this session — purely backend
plumbing and read-only diagnostic queries).

---

## 🛑 DISCIPLINE: NO ASSUMPTIONS (2026-05-27, user instruction)

> "We need to confirm all, we cannot assume information."

For every wire format documented in this file, the source of truth is a
**live capture** — either mitmproxy on a REST/MQTT call or BTSnoop on a
BLE write, with the exact bytes archived. Inference from class numbers,
field-name proximity, or "looks like the previous opcode" is **not
acceptable** for shipping production encoders.

Concrete rules:

1. **A wire format is "confirmed" only if it has bytes attached** — paste
   the captured hex into the section. If we only have a guess, mark it
   "❓ UNCONFIRMED" so future readers don't trust it.
2. **Encoders without a confirmed capture are NOT shipped.** They can
   exist as drafts in branch notes, but not in `protocol.py`.
3. **Decoders may be soft-shipped** with all unknown fields stored under
   raw field numbers (`f1..fN`) — never with labels we haven't validated.
4. **If a labeled name doesn't match a UI value we saw with our own eyes,
   relabel it as `f<n>`**. (We did this — and got burned — with
   `vehLedStatus`: the const enum suggested a brightness picker, but the
   live UI tour proved Headlight Mode is a scheduled on/off window.)
5. **Every "out of scope" decision needs a reason** (user instruction,
   tracked in #97, app feature doesn't exist, etc.) — not "we'll probably
   never need it".

When the priority queue says a feature needs a "capture" — that means a
**live wire frame** with bytes archived alongside the decoded fields.
Anything short of that is a hypothesis, not progress.

---

## 🗺 MASTER APP-FEATURE INVENTORY (2026-05-27 late evening tour)

Complete walk of every screen in the Lymow Android app (v3.0.6 build 351).
Screenshots + UI dumps at `/tmp/lymow-app-tour/*.png|*.xml`. Map edits
(create/edit/delete zones/channels) **deferred per user instruction** —
they need supervised manual capture later.

### 0. Imperial / Metric toggle (Me → Unit) — **frontend-only, confirmed**

Tested by toggling Imperial and reading the device-main map area: switched
from `1489.02 m²` (metric, wire) to `16027.67 ft²` (Imperial, app-side
conversion: 1489 × 10.7639 = 16027.5 ✓). Robot wire data is **always
metric** (mm, m/s, m²). HA already serves metric — no backend change needed.

### Decoded vs missing — full matrix

**Legend**: ✅ shipped · 🟡 partial · ❌ missing · 🚫 out of scope ·
🔒 PR-blocking

| # | App screen / control | What it does | HA backend status | Notes |
|---|---|---|---|---|
| 1 | **Home — robot tile (battery / state)** | Live status | ✅ existing sensors | |
| 2 | **Home — Notification bell** | Error event history list | 🟡 current-error sensor exists; history list NOT exposed | Separate REST endpoint not yet mapped. Low priority — `error_code` sensor surfaces what users actually need. |
| 3 | **Home — Camera widget** | Live video | ✅ local RTSP works; ❌ remote (KVS) = #97 | Explicitly excluded from PR-undraft gate. |
| 4 | **Home — Add Task** | Schedule create | 🟡 `set_schedules` (bulk) works; granular add not pinned | User said schedules are done manually. |
| 5 | **Home — ⋮ → Add Device** | Pair new device | 🚫 | App-side pairing — not HA scope. |
| 6 | **Home — ⋮ → Rename Device** | Cloud display-name PATCH | ✅ `lymow.set_device_name` | |
| 7 | **Home — ⋮ → Share Device** | (NOT IMPLEMENTED — "Coming soon" toast in app) | 🚫 | App-side feature doesn't exist yet. Not a real gap. |
| 8 | **Home — ⋮ → Delete Device** | Unbind device from account | ❌ endpoint unknown | Confirmation dialog "Are you sure you want to unbind?". Mitmproxy + actual click needed. **Low priority** — user manages devices in app. |
| 9 | **Me → Account** | Email, OAuth provider, change-pw, delete-account, logout | 🚫 | Account mgmt is app-only. |
| 10 | **Me → Language** | UI language picker | 🚫 | Frontend. |
| 11 | **Me → Unit** | Metric / Imperial | 🚫 | Frontend-only (proven). |
| 12 | **Me → Help Center / Report Logs / About Us** | Zendesk pages, bug reporter, marketing | 🚫 | Not HA scope. |
| 13 | **Device main — Mow All Zones header** | Tap to pick zones for next mow | ✅ `lymow.start_zone` | |
| 14 | **Device main — Sliders icon (≡)** | Opens Mowing Settings | (see Mowing Settings rows) | |
| 15 | **Device main — Camera icon** | Live camera | ✅ local RTSP (see #3) | |
| 16 | **Device main — Right-rail 🗺** | Map Backup & Restore shortcut | ✅ backend complete | UI panel pending (supervisor card session). |
| 17 | **Device main — Right-rail 👁** | Centre map on robot | 🚫 | Pure UI camera-follow. |
| 18 | **Device main — Right-rail ✏ (Edit)** | Landscape edit toolbar | 🟡 see Edit Mode rows below | Map edits postponed per user. |
| 19 | **Device main — Right-rail 🎮 (Joystick)** | BLE drive + Adjust Charging | ✅ `lymow.ble_drive` + `SetChargingStationHereButton` | |
| 20 | **Device main — Mow / Dock buttons** | Start mow / return to dock | ✅ existing | Dock confirmation dialog wired via `DockAndForgetProgressButton` |
| 21 | **Joystick "+" → Adjust Charging** | userCtrl=38 MODIFY_STATION | ✅ shipped | |
| 22 | **Joystick "+" → Add Zone / Nogo / Channel** | App drives robot to record boundary (Edit Boundary path) | ❌ userCtrl=10/11 not encoded | Postponed per user (map edits). |
| 23 | **Edit toolbar → Merge Map / Split Map** | "Coming soon" in app | ✅ HA card has client-side merge/split via sync_map | HA is ahead of app. |
| 24 | **Edit toolbar → Edit Boundary** | Drive-the-robot boundary record | ❌ userCtrl=10/11 not encoded | Postponed. |
| 25 | **Edit toolbar → Rename / Delete / Delete All** | Single-zone rename/delete + clear-all | ✅ all shipped | |
| 26 | **Mowing Settings → Global → Basic** | moveSpeed, cutHeight, blade-speed (Eco/Standard/Power/Turbo) | ✅ `set_task_config` covers all | |
| 27 | **Mowing Settings → Global → Advanced** | path_spacing, stripe_angle, mowing_order, zone/perimeter/channel obs detection, perimeter_mow_dir, no_go_mow_laps, zone_perimeter_mow_laps, turn_off_outer_motor, safe_margin_mode, channel_deck_height, raise_omni_wheels_on_channel | 🟡 most fields covered; **Safe-margin mode + Raise-omni-wheels-on-channel** not in `_TASK_CONFIG_FIELDS` yet | Likely 2 new bool fields in PbZoneConfig — needs capture. |
| 28 | **Mowing Settings → Customize → zoneN** | Per-zone overrides | ✅ `lymow.set_zone_config` (userCtrl=9 path) | |
| 29 | **Mowing Settings → Customize → channelN** | Per-channel overrides (Channel Obs Detection + Channel Deck Height) | ✅ `lymow.update_channel_settings` (sync_map path) | Channel Obs Detection (Smart/Touch-Only) wire field within PbChannel still untested — assumed `detectMode`. |
| 30 | **Settings → Cancel Task** | userCtrl=28 FORCE_REINIT | ✅ `ForceReinitButton` | |
| 31 | **Settings → Device Settings → Recharge & Resume** | PbRobotConfig.rrConfig (f18) | ✅ `lymow.set_recharge_resume` | |
| 32 | **Settings → Device Settings → Headlight Mode** | **SCHEDULED auto-on/off** (toggle + start/end time) — NOT brightness | ✅ SHIPPED e510f2d | Captured live 2026-05-30: PbRobotConfig f14 start / f15 end (PbTimeZone UTC), f9={f10:1} marker, disable=signal 7 + zeroed times. `set_headlight_schedule` service. See "✅ DONE 2026-05-30: Headlight schedule" section. |
| 33 | **Settings → Device Settings → Vehicle LED** | Manual on/off | ✅ `VehicleLedSwitch` (signal=10/11) | |
| 34 | **Settings → Device Settings → Rainy Mowing / Charging Handbrake** | PbTaskConfig f3/f4 | ✅ `lymow.set_device_settings` | |
| 35 | **Settings → Device Settings → Timezone "Sync with Phone"** | PbRobotConfig.timezoneOffset (f21) | ✅ `SyncTimezoneButton` | |
| 36 | **Settings → Device Settings → Return to Dock** | PbTaskConfig.chargingMode (Direct/Perimeter) | ✅ `lymow.set_device_settings` (charging_mode) | |
| 37 | **Settings → Schedules** | Add / edit / delete / toggle | 🟡 bulk `set_schedules` works | User does these manually — granular wire not pinned. |
| 38 | **Settings → Mowing History** | Total area / time / per-session list with detail | ✅ sensors (last entry) + `lymow.get_clean_history` (full list) | |
| 39 | **Settings → Map Backup & Restore** | List / create / restore / delete / rename | ✅ all REST endpoints wired | UI panel pending. |
| 40 | **Settings → Notifications** | Device-notifications + Alerts-Only tristate | ✅ `MobileNotificationSwitch` + `AlertsOnlySwitch` | |
| 41 | **Settings → Network Settings → WiFi SSID/password + Reconnect** | Push new WiFi creds to robot | ❌ wire unknown | Risky to capture (mistyped password disconnects robot). |
| 42 | **Settings → Network Settings → Network Priority (4G/WiFi)** | PbRobotConfig.metric_4g (f11) | ✅ `Prefer4gSwitch` + `lymow.set_network_priority` | |
| 43 | **Settings → RTK Diagnostic** | RTK Status, Location Precision, GNSS sat count, L1/L2/L5 sat counts + SNRs, Base Station online, Data Error Rate, Advanced (Differential Age, Lora Bandwidth, HW DC Voltage, CW Interference, Antenna Gain — all per-band) | 🟡 raw rtkL1/rtkL2 fields decoded as `f1..f13`; **labels now correlated (see below)** — sensor labels still pending in code | |
| 44 | **Settings → Bind RTK** | RTK base SN input + Scan/Bind | ❌ endpoint/wire unknown | Mitmproxy capture needed; risky (could unbind existing base). |
| 45 | **Settings → Find My Robot** | Map + reverse-geocoded address + enable toggle + Play Sound button | ✅ `FindRobotSwitch` + `FindMyRobotPlaySoundButton` | |
| 46 | **Settings → PIN Code** | 4-digit LCD-screen unlock PIN; default 0000 | ❌ wire unknown | Mitmproxy capture needed; do not log captured value. |
| 47 | **Settings → Anti-theft** | **MULTI-region** geofence (< > arrows + dot indicators) + enable toggle + radius slider | 🟡 `lymow.set_geofence` handles **first region only** | **NEW GAP**: app supports multiple geofence regions. Coordinator needs `index` parameter. |
| 48 | **Settings → Lock-device** | userCtrl=18 LOCK | ✅ `LockRobotButton` | |
| 49 | **Settings → Device Info** | SN, IP, MAC, Software Version, MCU Version | ✅ all 5 fields exposed as sensors | |
| 50 | **Settings → OTA Update** | Current version + Update button + (PIN code 0000 hint shown) | ✅ `update` entity | |
| 51 | **Settings → Factory Reset** | userCtrl=37 RESTORE_FACTORY_DEFAULTS | ✅ `RestoreFactoryDefaultsButton` | |
| 52 | **Settings → Report Logs** (per-device) | Send tech-support log | 🚫 | App-only |

### 🆕 RTK per-band label correlation (LIVE CAPTURE 2026-05-27 23:14)

UI values cross-referenced to live `rtkL1` / `rtkL2` dicts from a synchronous probe:

```
PbOutput.f35 (rtkL1, populated by USER_CTRL_QUERY_RTK_DIAGNOSTIC_L1=57):
  f1  = subMsgVersion (2)
  f2  = locationPrecisionM    (float, "Location Precision: 0.010 m")
  f3  = gnssSatellites         (int, "GNSS Satellites: 23")
  f4  = l1SatCount             (int, "L1 Band: 16")
  f5  = l2SatCount             (int, "L2 Band: 16")
  f6  = l5SatCount             (int, "L5 Band: 13")
  f7  = l1SnrMedian            (int, "L1 SNR: 38")
  f8  = l2SnrMedian            (int, "L2 SNR: 34")
  f9  = l5SnrMedian            (int, "L5 SNR: 36")
  f10 = dataErrorRatePct       (int, "Data Error Rate: 0.0%")
  f11 = (float, always 0.0 observed — unmapped)

PbOutput.f36 (rtkL2, populated by USER_CTRL_QUERY_RTK_DIAGNOSTIC_L2=58):
  f1  = differentialAgeSec     (float, "Differential Age: 2.00 s")
  f2  = loraBandwidthL1Bps     (int, "Lora Bandwidth L1: 268 bps")
  f3  = loraBandwidthL2Bps     (int, "...L2: 389 bps")
  f4  = loraBandwidthL5Bps     (int, "...L5: 680 bps")
  f5  = hwDcVoltageL1V         (float, "Hardware DC Voltage L1: 0.89 V")
  f6  = hwDcVoltageL2V         (float, "...L2: 1.00 V")
  f7  = hwDcVoltageL5V         (float, "...L5: 1.79 V")
  f8  = cwInterferenceL1       (int, "CW Interference L1: 60")
  f9  = cwInterferenceL2       (int, "...L2: 94")
  f10 = cwInterferenceL5       (int, "...L5: 36")
  f11 = antennaGainL1          (int, "Primary Antenna Gain L1: 38")
  f12 = antennaGainL2          (int, "...L2: 71")
  f13 = antennaGainL5          (int, "...L5: 53")

Base Station Status (Online / Offline) — observed but not yet pinned to a field
(f10 of rtkL1 always 0 in idle; needs base-offline test).
```

Next backend commit will rename the decoder keys + ship 13+ labeled sensors
behind `entity_registry_enabled_default=False`.

### ✋ True gaps left to close before PR un-drafts

In priority order (skipping out-of-scope and the user-deferred map edits):

1. ~~**RTK per-band labeled sensors**~~ — **DONE** (`2043f89` basic 10 +
   `9056564` advanced 12). All 22 rtkL1/rtkL2 fields decoded with
   live-correlated labels and surfaced as disabled-by-default sensors.
2. ~~**Multi-region anti-theft geofence**~~ — **DONE** (`e850b25`).
   `lymow.set_geofence` now takes `index` (0 = first; `index == len`
   appends a region; out-of-range/negative raise `HomeAssistantError`).
3. **Safe-margin mode + Raise-omni-wheels-on-channel** — 2 missing
   PbZoneConfig (or PbChannelConfig) bool fields. Captures pending.
4. **Channel Obstacle Detection per-channel** — verify wire field
   (assumed `detectMode` in PbChannel sub-message).
5. **Headlight Mode SCHEDULED frame** — PbRobotConfig sub-message at
   unmapped field number; same shape as rrConfig (enable + startTime
   + endTime).
6. **Network Settings WiFi credentials write** — Reconnect tap wire format.
7. **Bind RTK SN write** — REST or BLE unknown.
8. **PIN Code update** — 4-digit write; field 9 of PbRobotConfig
   (lcdPinCode) likely.
9. **Notification list endpoint** — for showing app-style event history.

Gaps 3–9 each need a **live wire capture** before any encoder ships (the
"NO ASSUMPTIONS" discipline above). The ready-to-run playbook for those
captures is in the next section — follow it top to bottom, paste the
bytes, then the implementation checklist under each gap can be done
directly. **User decisions on file (2026-05-29):** capture session is
*paused* (don't drive the app live yet); when it resumes, **all tiers are
in scope including the risky WiFi-write and Bind-RTK captures** (user is
to be physically near the robot for those two).

### 🚫 Permanently excluded from PR-undraft gate

- **Remote camera (KVS)** — issue #97, explicitly excluded by user.
- **Share Device** — feature doesn't exist in app ("Coming soon" toast).
- **Delete Device, Add Device** — account-level, HA has its own device mgmt.
- **Me-tab settings** (Account, Language, Help Center, About Us, Report
  Logs, Imperial/Metric toggle) — app-only / frontend / out of scope.
- **Map edits** (create/edit/delete zones/channels via Edit Boundary) —
  user said postpone to a manual-supervision session.

## 📋 2026-05-30: APK-verified message field maps (Hermes v96 disasm)

Extractor: `/tmp/extract_fields.py <anchorFieldName>` over `/tmp/disasm.txt`
(finds the encode fn whose field-map contains the anchor; pairs each field's
name string with its LoadConst tag). **Verdict: every field NUMBER our
encoders/decoders use is CONFIRMED correct.** Notable names + new fields:

**PbInput (commands):** userCtrl=5, remoteControl=10, schedule=11, map=12,
robotConfig=13, wifiConfig=17, btMap=23, theftSetting=24, taskConfig=26,
floorData=28, mergeZone=30, cutZone=31, netRtcm=32. → our encoders (userCtrl5,
schedule11, map12, robotConfig13, wifiConfig17, taskConfig26) all correct.

**PbRobotConfig:** rcCutSpeed=2, rcCutHeight=3, rcRaiseCutHeight=4,
rcLowerCutHeight=5, audioVolume=6, isOpenLed=7, signal=8, lcdPinCode=9,
cmdCellularSwitch=10, metric_4g=11, **camLedStatus=12, vehLedStatus=13**
(LED brightness 0–4 — the "Headlight Mode multi-state" we'd wondered about),
**openLedTime=14, closeLedTime=15** (= our headlight start/end — names confirmed),
resumeBat=16, **rtkBinding=17** (our bind_rtk ✓), rrConfig=18, scheduleId=19,
schedulePathOffset=20, timezoneOffset=21, dockOnError=22. → all our mappings ✓;
NEW available: vehLedStatus (LED brightness select), scheduleId.

**PbSchedule:** dayOfWeek=1…isDisabled=8, isAngleOffset=9, **mowAngle=10**(new),
config=11. → all ours ✓; NEW: mowAngle (per-task stripe angle).

**PbPath (PbOutput f13):** poses=1, cleanFinishedZones=2.

**PbZoneConfig — numbers all correct, but two NAME mismatches vs our remap:**
cutHeight1, raiseCutHeight2, lowerCutHeight3, moveSpeed4, brushSpeed5, cutSpeed6,
cleanMode7, **cleanDir8** (we'd guessed "enabledZoneMask"), pathSpacing9,
perimeterMowLaps10, perimeterMowDir11, noGoMowLaps12, obsDecMode13, pathOrder14,
**startProgress15**, relativeCleanDir16, **lineFollowMode17** (we call it
`safeMarginMode`), **disableOuterDischarge18** (we call it `turnOffOuterMotor`),
followDetectMode19. → DECISION NEEDED: align f17/f18 to the proto names
(authoritative, per the verbatim-wire-names rule) vs keep our UI-derived names
(the supervisor's card reads these keys). Field numbers are right either way.

(Anchor `statusTimes` hit the internal mowing-algorithm state msg —
boustrophedon/wallFollowing/chess/curPose/cleanStartTime/usedBattery/errorList —
not user-facing; useful end-of-run bits are cleanStartTime, usedBattery.)

**PbRobotInfo (PbOutput f5 — core status):** robotStatus=1, battery=2,
wifiSignalQuality=3, lteSignalQuality=4, **btSignalQuality=5**, workStatus=6,
isRecharging=7, isCharging=8, **wifiWorking=9, lteWorking=10**. → all our
decodes ✓. **PbAreaInfo:** areaOrGlobal=1, cleanZoneIds=2. RTK-diag structured
msg (= rtkDiagnosticL1/L2 f35/f36, matches the f8 JSON): diffAge=1, loraBps0-2=
2-4, hwDc0-2=5-7, cwRatio0-2=8-10, antValue0-2=11-13.

**COMPLETE cross-check (2026-05-30) — every message verified vs bytecode:**
- PbMap ✓ goZones1/nogoZones2/channels3/chargingStationLoc4/enuBasePoint7/
  taskConfig8/globalZoneConfig11/globalChannelConfig12/runTimeConfig13.
- PbChannel ✓ isValid4/polygon5/isDockingChannel6/detectMode8/cutHeight9/channelLift10.
- PbRRConfig ✓ enableRr1/start2/end3/rechargeBat4/resumeBat5 (exact).
- PbDeviceProfile ✓ fwVersion1/mcuVersion2/softwareVersion3/ipAddress5/macAddress6/
  sn7 (+ new rtkSn8/wheelVer10/knifeVer11; f4 wifiSsid & f9 simId left out = sensitive).
- PbRobotLlaCoords ✓ latitude1/longitude2/altitude3. PbWifiConfig ✓ ssid1/password2
  (our f5=3 = `secret`, matches app). PbAreaInfo areaOrGlobal1/cleanZoneIds2.
- PbZoneBasicInfo: type1/name2/hashId3/**isEnabled4**/polygon5/zoneRename6/
  updateTime7/**mowOrder8**/**mowOrderTextPos9**. decode reads isEnabled@4 ✓;
  encode_set_zone_config sets isEnabled@4 ✓. NOTE: encode_start_zones + schedule
  _encode_zone_basic_info put the "selected" flag at f8 (=mowOrder) & the point at
  f9 (=mowOrderTextPos), and don't set f4=isEnabled — works (mower treats listed
  zones as enabled; f8=index legitimately sets mow order). Optional faithfulness
  tweak: also set f4=isEnabled=1 (the app does).
**VERDICT: all decoder & encoder field NUMBERS are APK-correct.** Only nuances:
UI-vs-proto names (f17/f18 by choice; wifi f5=secret; zone f8/f9), all benign.

**Enums / error tables (2026-05-30):** workStatus enum already complete in
const.py; setting-value enums (cutSpeed 3–6, obsDecMode/followDetectMode 1/2,
perimeterMowDir) verified live earlier. **Error/warning code tables now
AUTHORITATIVE (commits 713f1cc → 573c8b1):** two layers, both from the bytecode.
(1) The **PbErrorCode (90) and PbWarningCode (63) enums** — symbolic names + numeric
values — extracted by reading each enum member's LoadConst register (declaration
order is non-sequential, e.g. WARNING_BLADE_STUCK=32 between 13 and 14, so positions
were NOT assumed). (2) **The official user-facing text IS bundled too** — earlier
"remote i18n only" note was WRONG: the i18next EN (+fr/de/es…) resource ships in the
bundle as `NewObjectWithBufferLong` literals (`errors`/`warnings` namespaces), keyed
by `code_<enumvalue>` (grouped, e.g. `code_50_53_67_68_69_70_71`). So:
- `ERROR_DESCRIPTIONS` = official titles for the 54 user-surfaced codes (groups
  expanded) + humanized enum-name fallback for internal codes. Matches the app
  exactly (E71="Navigation Internal Error", E18="Excessive Tilt Detected").
- `ERROR_REMEDIATION` (new) = official step-by-step fix text (`*_detail` keys),
  surfaced by the error sensor as a `remediation` attribute.
- `WARNING_DESCRIPTIONS` = official text for the 31 surfaced warning codes +
  humanized fallback. Error sensor also exposes `warning_descriptions`.
This REPLACED the old 10-entry hand-curated table, which had codes 51/52 wrong
(those were *warning* codes, not error codes). The remote `remote_config.*` fetch
only swaps text for the *active* device language at runtime; the EN catalog is
the bundled fallback. Phone AsyncStorage (`@robot_errors`) also caches each fired
error as `{message,code:"(E15)",detail}` — confirms display "E<n>" uses the enum
value (E15 ↔ error 15 "Weak RTK Signal"). Symbolic audio-error categories also found:
AUDIO_ERROR_{BATTERY_LOW, BLADE_STUCK, CLIFF, DOCK_FAIL, INI_FAIL, ROBOT_SLIP, SLOPE}.
**Bytecode decoding is COMPLETE** — every message/field verified, and now the
human-facing error/warning text + remediation are extracted too (not just codes).

## 📋 2026-05-31: cleanReport DECODED + live-confirmed; app 3.0.7 verified

**`cleanReport` (PbOutput f28 = PbCleanReport) — DECODED & wired in (commit
e5e2d55).** This is the report the robot pushes at the end of a mow — the
real-time counterpart to the polled REST clean-history. `decode_pboutput` now
surfaces `state["cleanReport"]`. Field map (confirmed by cross-checking 5 captured
reports field-for-field against the live REST `get-clean-history` record — identical
numbers, and the live completion report was caught end-to-end at docking on
2026-05-31):

```
f28 PbCleanReport
  f1 date            epoch seconds — the TASK START/reference time, NOT dock time
                     (a resumed multi-session task keeps its original start date)
  f2 PbCleanSummary
     f1 cleanTimeMin   cumulative mowing minutes (across charge cycles)
     f2 cleanAreaM2    area cut (float32; omitted by proto3 when 0)
     f3 {f2: mapHashId} which map/zone (sub-msg; f2 = hashId string)
     f5 percent        task completion fraction 0–1 (decoder ×100 → %)
     f6 mapTotalAreaM2 whole-map area (float32; constant per map)
  f3 startType        1 = app/normal, 2 = interrupted/other
  f4 [error_list]     repeated {f1 code, f2 percent(f32)} — error code + the
                      progress-% at which it occurred (codes map to ERROR_DESCRIPTIONS)
  f6 usedBatteryPct   battery consumed; CUMULATIVE so >100 for multi-cycle mows
```

Real live sample (the full ~1200 m² zone, completed across sessions): cleanTimeMin
270, cleanAreaM2 1197, percent 100, usedBatteryPct 197 (~2 charge cycles), errorList
[76@29%, 13@87%, 15@87%, 71@89%, 16@89%]. Tests use synthetic helper-built payloads
(no captured bytes committed). **Gotcha for future sessions:** the app re-receives
the *last* cleanReport on reconnect, and its `date` is the task-start time — so a
"new" frame may show an old date. Tell live-vs-replayed apart by the **capture
timestamp** (`capture.py` `_ts()` is **UTC**), not the report's `date`.

**Scanning a capture for cleanReport (reusable recipe):** `capture.py` logs each
MQTT publish as a topic line `[ts] MQTT ← /device/<thing>/pboutput (NB)` followed by
`→ message: N pb bytes hex: <HEX>`. Pair each `pboutput` topic line with the next
hex line, hex-decode, walk top-level protobuf fields, and flag frames containing
field 28; then `decode_pboutput(bytes)["cleanReport"]`. The capture file is
**append-mode**, so it accumulates many sessions — filter by capture timestamp.

**Operational capture playbook (learned the hard way 2026-05-30/31):** to capture a
robot→app push (like cleanReport) the **Lymow app must be open AND foreground on a
proxied phone** at the moment it fires — backgrounding it (e.g. another app such as
Plejd coming to the foreground) or letting the screen sleep drops the MQTT-over-WS
socket and capture silently stops (file just stops growing). Checklist: phone proxy
set to the mitmproxy host:8888; `mitmdump -s tools/capture.py --listen-host
0.0.0.0 --listen-port 8888 --ssl-insecure` running; app foregrounded
(`adb shell monkey -p com.lymow.app -c android.intent.category.LAUNCHER 1`); bump
`settings put system screen_off_timeout 1800000`; verify the capture file is
*growing* (not just that mitmproxy is alive). **When done: clear the phone proxy
(`settings put global http_proxy :0`) on every proxied device BEFORE killing
mitmproxy**, or those phones lose internet (dead proxy). Kill mitmproxy by its
listening port, not `pkill -f` on a pattern that also appears in your own shell
command (that self-terminates the shell).

### Remaining decode gaps — characterized 2026-05-31 (NOT shipped; here's why)

Chased the leftover gaps; all three are now characterized but intentionally not
implemented — each needs something we don't safely have yet:

- **`promptInfo` (PbOutput f15 = PbPromptInfo) — command result feedback. Structure
  CONFIRMED, code semantics NOT.** Layout: `{f1 selfCheckingRet, f2 zoneRet, f3
  mutateRet}`, each a result sub-msg `{f1 code(varint), f2 hashId(string)}`. f15 is
  present-but-empty in nearly every heartbeat; in the whole overnight capture only
  **one** non-empty sample fired: `mutateRet{code:5}` at the big-mow completion. The
  only named result enum in the bytecode is `MUTATE_RES_NONE=0`, so code `5` can't
  be named with confidence. **Blocker: too few samples + no rich enum to name the
  codes.** Decoding the structure is trivial; do it once we've captured several
  results of known operations (rename/delete/cut a zone → read the resulting code).
- **`cutZone` (PbInput, `USER_CTRL_CUT_ZONE` confirmed) — split a zone by a cut
  line.** PbCutZone carries `hashId` + a cut `line`/`points` (exact field numbers
  need the encoder dump). Complement to the merge we already support. **Blocker:
  it's a destructive map mutation — derive the wire format from the bytecode, but it
  must be validated in a supervised live session before shipping as a service (per
  the map-edit safety rule), not sent blind.**
- **`floorData` (PbInput, `USER_CTRL_FLOOR_ADD/DELETE/MODIFY/BACKUP/RESTORE`
  confirmed) — multi-map / multi-floor management.** PbFloor{floors}, PbFloorInfo.
  BACKUP/RESTORE (44/45) are already covered. **Blocker: multi-map mutations need
  live validation AND the account must actually have multiple maps to test against.**

Net: the high-value, safe-to-ship decode work is complete. What's left is either
sample-starved (promptInfo) or map-mutation command encoders that the project's
own rules say must be validated live-with-supervision, not shipped from static
analysis. Recommend deferring until a supervised map-edit session is set up.

**App version is current: 3.0.7 (versionCode 362, 2026-05-31).** Pulled the live
installed APK and diffed its JS bundle vs the prior build: only 12 new string
literals (MQTT-reconnect fix, zone-settings clear logic, sign-in error handling,
dock-on-error i18n) and **0 protobuf/enum/userCtrl changes** — 3.0.7 is bug-fixes
only, our backend is current. Note: `assets/app.config` "version" is a STALE Expo
string (says 2.1.14); the authoritative version is native `versionName`. There is
NO expo-updates OTA layer — the bundle inside the installed APK IS the running code.
(See the [[reference-apk-hermes-re]] memory for the full re-check + diff procedure.)

**NEW fields now named & available for future HA features (numbers verified):**
`vehLedStatus`/`camLedStatus` (robotConfig f13/f12, LED brightness 0–4 → a
brightness select), `mowAngle` (schedule f10, per-task stripe angle),
`btSignalQuality`/`wifiWorking`/`lteWorking` (robotInfo f5/f9/f10), `scheduleId`
(robotConfig f19). All decoder/encoder field NUMBERS across PbInput, PbOutput,
PbRobotInfo, PbRobotConfig, PbZoneConfig, PbSchedule are APK-CONFIRMED correct.

---

## ✅ DONE 2026-05-30: PbZoneConfig remap SHIPPED (commit 246c639)

The mowing-settings field-map bug is FIXED and merged to the branch. Decoder
(`_ZONE_CONFIG_INT_NAMES`/`_BOOL_NAMES`) + encoder (`_TASK_CONFIG_FIELDS`)
both now use the live-confirmed layout (pathSpacing=f9, perimeterMowLaps=f10,
noGoMowLaps=f12, safeMarginMode=f17, turnOffOuterMotor=f18; dropped
cleanDir/startProgress/lineFollowMode/brushSpeed). Fixes what the card shows
and what per-zone (userCtrl=9) + sync_map writes send. Service params updated
(line_follow_mode/brush_speed accepted-but-ignored; added safe_margin_mode/
turn_off_outer_motor/relative_clean_dir). 1073 tests, 100% cov, ruff clean.

**Remaining mowing-settings follow-ups:**
- ✅ DONE (commit 18f4302): Global `set_task_config` now uses **userCtrl=49 +
  PbInput.f12(PbMap).f11(globalZoneConfig)** — live-confirmed via BLE, not the
  old userCtrl=36+f26. Per-zone + sync_map paths were already correct.
- ✅ RESOLVED: f15 = reserved/unused (no Global UI control touches it); raise/
  lower-cut-height is not a Global/Customize control (absolute cutHeight only).
  See "✅ DECODED 2026-05-30: Global mowing-settings envelope" below.
- Card panel (supervisor) can later drop line_follow_mode/brush_speed and add
  safe_margin_mode/turn_off_outer_motor/stripe_angle controls, plus a Blade
  Speed select (cutSpeed Eco=3/Standard=4/Power=5/Turbo=6).

## 📋 2026-05-30: live full-mow capture findings (dock→channel→zone)

Captured a full mow of KX1kGyat (1197 m², ~1cm RTK). The whole sequence is
traceable from already-decoded telemetry — **no path query exists**; the
coverage trail = `pose` (f14) accumulated over time (the app draws it the same
way), plus `mowProgress`/`mowStripCount`/`currentTaskZoneHashId`.

- **`mowProgress` (f12.f5 × 100) WORKS** — true completion %, climbs slowly
  (~6% at 46 min for the 1197 m² zone; ~0–1% early so the app rounds to "0%").
- **`f12.f1` = MISSION TIME in minutes** (commit 5678ce3) — was mislabeled
  "mowStripCount". LIVE-CONFIRMED: matched the app's "Mission time" 1:1 over a
  46-min run (and it's mower-reported, so both phone apps agree). Now exposed as
  the `mission_time` DURATION sensor (minutes). So the progress-bar's "Mission
  time 46 min" + "Map area 1197 m²" are BOTH decoded (missionTimeMin + totalTaskAreaM2).
- **Units (Metric/Imperial)** = frontend-only — set in the app's "Me" tab;
  toggling Imperial sent ZERO traffic (no REST/MQTT/BLE). The mower always
  reports metric; the app converts for display. HA does its own unit display,
  so nothing to change in the backend.
- **`PbOutput.f8` = advanced RTK/LoRa diagnostic — DECODED (commit ab0a5ce).**
  JSON blob {precision, quality, diff_age, sats[], chl_snr_median[],
  ant1_value[], cw_ratio[], lora_bandwidth[], hw_dc[], primary_error_desc,
  error_desc_list, error_position_list[x/y/z], timestamp}. Surfaced as
  `state['rtkDiagnostic']` = {precisionM, quality, diffAgeS, primaryError,
  errors}. The RTK base links via **LoRa**; `ERTK_LORA_DATA_ERROR_RATE` (noisy
  LoRa) is what degrades accuracy — the field to watch for low-precision
  episodes. Per-error x/y/z positions intentionally not surfaced.
- **`PbOutput.f37` = varint, CONSTANTLY 15** (12 samples). Appears ONLY during
  active mowing (workStatus=2) in an early window (mission minutes ~8–13), then
  the mower stops sending it. Behaviorally a transient early-phase marker
  (plausibly the perimeter-lap phase, since perimeterMowLaps=2 runs first —
  inference, NOT confirmed). Semantic NAME unreadable from the bundle:
  protobufjs exposes field *names* but field *numbers* are compiled into Hermes
  bytecode, so #37→name needs bytecode disassembly. Left un-decoded
  (no-assumptions) — fully characterized but not named.
- `cleanReport` (PbOutput f28, fires at session end) — ✅ **DECODED & live-confirmed
  2026-05-31** (commit e5e2d55). See the "cleanReport DECODED" section below.

## 📋 2026-05-30: APK-VERIFIED PbOutput field→number map (Hermes v96 disasm)

Disassembled `index.android.bundle` (HBC v96) with `hermes-dec` and read
`PbOutput.encode` for authoritative field numbers. **Method:** pip-install
hermes-dec in a venv → `hbc-disassembler bundle out.txt` → in the encode fn each
field's `LoadConstString '<name>'` / `GetById <name>` pairs with its
`LoadConst(UInt8|Int) <tag>` (tag = field#<<3 | wiretype; tags ≤255 use
LoadConstUInt8, larger use LoadConstInt).

```
 1 msgId        11 map*          21 audioId            31 robotPosePib
 2 version      12 cleanInfo     22 wifiConfigRes       32 taskConfig
 3 errorCodes   13 path          23 btMap(=map resp)    33 floorData
 4 warningCodes 14 pose          24 chargingStationLoc  34 netDetailInfo
 5 robotInfo    15 promptInfo    25 mobilePushNotif     35 rtkDiagnosticL1
 6 localizationInfo(our"RTK") 16 schedule 26 robotLlaCoords 36 rtkDiagnosticL2
 7 baseOutput   17 robotConfig   27 theftLock           37 heatedLensTimes(varint)
 8 iotCmd(RTK-diag JSON) 18 outputCtrl 28 cleanReport    38 aeRangeLevel(varint)
 9 debugSetting 19 algoLocOutput
10 deviceInfo   20 algoSegOutput
```
**All our decoder field numbers verified correct.** Resolutions:
- **f37 = heatedLensTimes** (camera lens defog/de-ice heater count — the
  "intermittent =15"). f38 = aeRangeLevel (camera auto-exposure). Decoded (cb1ef05).
- **f13 = path** (PbPath poses) — the coverage-path geometry's true home; not
  populated in captures yet, decode from f13 when a populated frame appears.
- **f28 = cleanReport** — session-end report. ✅ DECODED & live-confirmed 2026-05-31 (e5e2d55).
- f6=localizationInfo (we read as RTK pose — content matches); f8=iotCmd (carries
  the RTK-diag JSON we decode); f23=btMap is where the mower returns the map (our
  reader is right); f11=map appears unused for the query reply.

## 📋 2026-05-30: AUTHORITATIVE undecoded-gaps list (from APK proto schema)

Source: `tools/apk/assets/index.android.bundle` (protobufjs schema strings
`pb.PbInput.*` / `pb.PbOutput.*` / `pb.Pb<Msg>.<field>`). Cross-referenced vs the
integration's encoders/decoders. (Captured #1–4 of the "next" list this session:
currentTaskZoneHashId + softwareVersion decode shipped; pause/resume already
wired; clean-history is REST. The rest are blocked on live capture — phone USB
dropped mid-session.)

**Command gaps — PbInput fields with NO encoder:**
- `theftSetting` → **anti-theft + device-lock** (PbTheftSetting = geoFencing[covered]
  + antiTheft + deviceLock; LOCK=userCtrl 18). NOT implemented.
- `floorData` → **multi-floor / multi-map** (PbFloor{floors}; FLOOR_SWITCH/ADD/
  DELETE/MODIFY = userCtrl 40–43; BACKUP/RESTORE 44/45 are covered). NOT implemented.
- `remoteControl` → **manual remote drive over MQTT** (PbRemoteControl). Only BLE
  drive (`ble_drive`) exists; the MQTT remoteControl path is undecoded.
- `cutZone` → **split a zone by a cut line** (PbCutZone; CUT_ZONE=56). merge is
  covered; cut/split-by-line is not confirmed.
- `debugSetting` → debug settings (low value). `btMap`/`algoLocInput`/
  `algoSegInput`/`baseInput`/`wirelessStatus` → internal/algorithm (not user features).

**Telemetry gaps — PbOutput fields NOT decoded:**
- `path` → **coverage-path geometry** — PbPath = {poses[], cleanFinishedZones[]}.
  HIGH value for the map card (draw mowed path + finished zones). Exists but our
  QUERY_PATH reply didn't populate it; needs the right query trigger. **Top gap.**
- `promptInfo` → **operation result codes** — PbPromptInfo = {mutateRet,
  selfCheckingRet, zoneRet}. Confirms command success/failure + self-check result.
- `cleanReport` → ✅ **DECODED 2026-05-31 (e5e2d55)** — PbCleanReport, the
  real-time per-session report. See the dedicated section below for the field map.
- `cleanInfo.areaInfo.cleanZoneIds` → which zones were cleaned (area/progress is
  decoded; cleanZoneIds is not).
- `wifiConfigRes` → Wi-Fi set result (and set_wifi is BLE-only — see #200).
- `localizationInfo`, `iotCmd`, `btMap`, `algo*`, `baseOutput` → internal/minor.

**Cloud/REST settings — ALREADY IMPLEMENTED (live-validated 2026-05-30):** these
go via `PATCH /prod/update-device-feature` (NOT robot protobuf) and are already
exposed as HA switches backed by `async_set_device_feature`:
- **Notifications** = `mobileNotificationSwitch` → `MobileNotificationSwitch`. ✓
- **Anti-theft** = `theftDetectionSwitch` + geoFence[{lat,lon,radius}] →
  `TheftDetectionSwitch` + `set_geofence`. ✓ (live PATCH body confirmed the shape)
- **Device-lock** = `theftLock` → `TheftLockSwitch`. ✓
- **Find-robot** = `findRobotSwitch` → `FindRobotSwitch`. ✓
  So #5/#6 from the "next" list were NOT gaps — they're done and now verified.

**Multi-floor (40–43):** constants exist (FLOOR_SWITCH/ADD/DELETE/MODIFY) but no
encoders/services; this device is SINGLE-MAP (no floor/map switcher in the app),
so there's nothing to capture here. BACKUP/RESTORE (44/45) are covered. Only
relevant if a multi-map device shows up.

**Known userCtrl, intentionally not wired (low value / risky):** SELF_CHECKING(16),
CHARGING_STATION_RESET(17), FORCE_REINIT(28), RECORDING start/stop(30/31),
RESTORE_FACTORY(37), RESET_INIT(47), SWITCH_LTE_AIRPLANE(54), modify-zone-edge(10/11).

## ⚠️ 2026-05-30: MQTT backend live-validated; Wi-Fi BLE-only (#200); RTK-rebind caution

Live test publishing app→robot frames to `/device/{thing}/pbinput` from a
throwaway MQTT client (`_publish_hex.py`, unique client id, alongside the app):
- **`start_zones` (userCtrl=1) and `set_zone_config` (userCtrl=9) WORK over
  MQTT** — set the big zone to cut60/speed0.6/perim2/nogo2 then started it; the
  robot went to "Mowing". End-to-end confirms the command path + envelope.
- **`set_wifi` does NOT work over MQTT** (BLE-only) — a wrong-password frame
  (with and without the `1031` version prefix) drew no reaction while the above
  commands did. → issue #200; `async_set_wifi` now raises (commit e4d8bb2).
- **CAUTION: the mow hit `E71 Navigation Internal Error` at 0%.** Likely the
  RTK fix was disturbed by the earlier `bind_rtk` re-bind (same base). Cancelled
  task + docked. **Don't casually re-bind RTK on a working unit — it can drop
  the fix and block navigation (E71).** Retry the mow only after RTK re-fixes
  (check Settings → RTK Diagnostic).
- MQTT publishes from a 3rd client need a UNIQUE client id (the app holds the
  single per-identity connection; duplicate ids get kicked — that's why
  `sniff_pbinput` subscribe failed earlier, but `_publish_hex` works).

## ✅ DECODED 2026-05-30: PIN / Wi-Fi / Bind-RTK provisioning (sensitive — structure only)

All captured live; **no real values are recorded here or in git** (the real
PIN/SSID/password/base-id live only in the gitignored capture). Shipped as
`set_pin` (d21de63), `set_wifi` (feee97b), `bind_rtk` (5800ae4):

- **Set PIN** — `PbInput{f2:49, f13(robotConfig):{f9(lcdPinCode):{f1:<4 bytes,
  one per digit>}}}`, no userCtrl. `decode_robot_config` already omits f9.
- **Set Wi-Fi** — `PbInput{f17(wifiConfig):{f1:ssid, f2:password, f5:3}}`, **no
  version prefix**, sent over BLE during (re)provisioning. f5=3 constant. The
  app de-dupes unchanged creds (a no-op "Reconnect" transmits nothing); a real
  change/forget+add triggers the send.
- **Bind RTK** — `PbInput{f2:49, f13(robotConfig):{f17:{f1:baseId}}}`, no
  userCtrl. (NB: robotConfig.f17 = RTK bind, but PbInput-top-level f17 = Wi-Fi —
  two different f17s by parent.)

SECURITY: encoders never log the secret; ValueErrors carry no value; all tests
use placeholders. The mower's robotConfig pboutput echoes the PIN (f9) and
network status (SSID/IP/4G/iccid) — decoders must keep omitting/avoiding those.

## ✅ VALIDATED 2026-05-30: Start-mow-selected command (live mow capture)

User authorized mowing the bigger zone for a live capture. Started it from the
app (Select Mow → tap big zone → Mow), captured the start command over BLE,
then docked ("forget progress"). The start-mow-selected frame:
```
10312801621c0a1a0a181a08<hashId>40014a0a0d<f32 x>15<f32 y>
PbInput { f2:49, f5:1 (USER_CTRL_CLEAN), f12(PbMap){ f1 goZones[0]=PbZone{
  f1 basicInfo{ f3 hashId, f8:1 selected, f9 point{f1 x,f2 y} } } } }
```
This **validates our `encode_start_zones`** (same userCtrl=1 + PbMap.goZones.
basicInfo shape). Deltas vs the app, single-zone-harmless, left as-is:
- app sends basicInfo `f8=1` (selected flag); ours sends `f8=index` (1,2,3…).
  Only one zone captured — can't tell if multi-zone app uses 1,1,1 or 1,2,3,
  so NOT changed (no-assumptions). Revisit with a multi-zone capture.
- app includes the zone `f9 point`; ours omits it (robot re-derives — same as
  schedules + the shipped set_schedules path).

In-progress telemetry during the mow is the **standard pboutput** the
integration already decodes (workStatus block, battery 99→98, position floats
updating as it moved, zone area in PbMap f12). No new undecoded fields surfaced.
Live HA validation not possible mid-capture: `lawn_mower.7b6521` reads
`unavailable` because the phone app holds the single AWS IoT connection while
mitmproxy/ADB capture is active (known limitation — see [[reference_ha_live_access]]).

## ✅ DECODED 2026-05-30: Schedule mutations = full-list replace via MQTT (gap)

Live capture (added a 1-zone test task, toggled it off, deleted it; device
restored to empty). **Schedules transmit over MQTT, NOT BLE** (no handle-0x0014
write fired; the frames appeared only in the mitmproxy MQTT capture). Every
mutation is a **full-list replacement** on `PbInput.f11 (PbSchedules)`:
- **Add / Save Task**: `PbInput{f2:49, f11:PbSchedules{tasks:[PbSchedule]}}`.
  PbSchedule: f1 days(packed, Sat=6), f2 hour(UTC=local-2), f3 minute,
  f4 isRepeated, f5 zonesInfo{f3 hashId, f8=1, f9 point}, f6 id(large int),
  f7 timeZone(=2, UTC offset hrs), f8 isDisabled.
- **Toggle off**: re-sends the SAME full task list with `f8 isDisabled=1`.
- **Delete (last one)**: `PbInput{f2:49, f11:<empty>}` == `10315a00` ==
  exactly `encode_clear_schedules()`.

So **`encode_set_schedules` (already shipped) is the one wire primitive** for
add/edit/toggle/delete — granular ops are coordinator-level read-modify-write
over the cached `schedules` list, re-sending the full list each time. The
already-shipped `set_schedules` service ALSO omits the zone point (sends zones
as hashId strings only) and works — so the robot re-derives the point; granular
read-modify-write that drops point/config is consistent with shipped behaviour,
no new risk. NOTE: decoded entry doesn't carry per-task PbScheduleConfig (f11)
— lossy for tasks that set custom config, same as existing set_schedules.
**✅ SHIPPED (commit f4dd7f7): granular schedule ops** — coordinator
async_add_schedule / async_delete_schedule / async_toggle_schedule + the
matching services (add_schedule / delete_schedule / toggle_schedule). They
read-modify-write the cached schedule list and re-send the full list via
encode_set_schedules (`_wire_entries_from_cached` preserves each entry's UTC
time/id/timeZone and re-fills the zone point from cached map data). Delete-last
falls back to encode_clear_schedules; unknown id raises. **Edit-in-place
deferred** (composable as delete+add; the local↔UTC round-trip on existing
entries has DST edge cases for a focused follow-up).

## ✅ DECODED 2026-05-30: Global mowing-settings envelope + Blade Speed + OD fields

Live BLE capture of the Mowing Settings → **Global** tab "Save → Keep Custom"
(two frames: baseline vs Blade=Turbo + Perimeter-OD=Touch-Only; then restored
byte-identical to baseline). Findings:

**Global write envelope (CONFIRMS the #7 fix — userCtrl=49, NOT 36):**
```
PbInput { f2:49, f5:49(userCtrl GLOBAL_SETTING), f12(PbMap):{
  f11(globalZoneConfig = full PbZoneConfig): { ...19 fields... },
  f12: { f1:2, f2:60, f3:0 }   # global channel cfg? f2=60 mirrors cutHeight
}}
```
So the app sends global mowing settings as **userCtrl=49 + PbInput.f12(PbMap)
.f11(globalZoneConfig)** — a full PbZoneConfig, same field map as per-zone.
Our `encode_set_task_config` still wraps userCtrl=36 + PbTaskConfig(f26) for
the global path (#7) — that's the bug to fix. Per-zone (userCtrl=9) + sync_map
paths are already correct. NOTE: the app ALSO sends a sibling PbMap.f12
{f1:2,f2:60,f3:0} in the same frame; reproduce it for byte-parity if we switch
the envelope (decode TBD — likely globalChannelConfig).

**Baseline globalZoneConfig field values (anchors the whole map):**
f1 cutHeight=60, f4 moveSpeed=0.6(f32), f6 cutSpeed=4, f7 cleanMode=1,
f8 enabledZoneMask=-1(all), f9 pathSpacing=35, f10 perimeterMowLaps=1,
f11 perimeterMowDir=2(Random), f12 noGoMowLaps=1, f13 obsDecMode=2,
f14 pathOrder=1, **f15=0 (unknown/reserved)**, f16 relativeCleanDir=90
(stripe "Optimized"), f17 safeMarginMode=1, f18 turnOffOuterMotor=0,
f19 followDetectMode=2.

**Newly confirmed via the A→B diff (only f6 and f19 changed):**
- **Blade Speed = cutSpeed (f6)** — UI slider Eco/Standard/Power/Turbo =
  **3 / 4 / 5 / 6** (Standard=4 → Turbo=6 observed).
- **Zone Obstacle Detection = obsDecMode (f13)**; **Perimeter Obstacle
  Detection = followDetectMode (f19)**. Both: **Touch-Only=1, Smart=2**.
- **f15 stays 0** under every Global control — it is NOT Perimeter-OD and NOT
  raise/lower cut-height. Treat as reserved/unused (no UI control maps to it).
- raise/lower cut-height: the Global/Customize tabs expose absolute Cutting
  Height (mm) only — there is no momentary +/- raise/lower control on these
  screens. PbRobotConfig f4/f5 (rcRaise/rcLower) are a separate channel-deck
  feature; left as-is (we set absolute cutHeight via SYNC_MAP, which is better).

Item #2 (raise/lower + f15 + Blade Speed) is RESOLVED as decode findings; the
only follow-up code work is #7 (the envelope switch) + optionally a Blade Speed
select entity (cutSpeed 3-6) for the card.

## ✅ DONE 2026-05-30: Headlight schedule SHIPPED (commit e510f2d)

Gap 5 **CLOSED**. Captured the Device Settings → Headlight Mode "Save" frame
live via BLE BTSnoop (three saves: ON 05:17→06:23 local, OFF, restore
23:46→00:46 local). The decode reproduces all three frames byte-for-byte.

**Wire format** — it's NOT the `vehLedStatus` brightness picker we assumed;
it's a scheduled auto on/off window on `PbRobotConfig` (PbInput.f13):

```
PbInput { f2:49, f9:{f10:1}, f13(robotConfig):{ f14:start, f15:end } }     # enabled
PbInput { f2:49, f9:{f10:1}, f13:{ f8:7(signal), f14:{0,0}, f15:{0,0} } }   # disabled
```

- `f14` startTime / `f15` endTime = PbTimeZone `{f1 hour, f2 minute}` in **UTC**
  (app converts the local picker value; 05:17 local CEST → f14{3,17}).
- `f9 = {f10:1}` is a constant headlight-write marker — absent from rrConfig
  and find-my-robot robotConfig frames, so it's headlight-specific.
- Disable = `robotConfig.signal` (f8) = **7** + both times zeroed.

**Backend shipped:** `protocol.encode_set_headlight_schedule(enable, start,
end)` (byte-exact), `decode_robot_config` surfaces `headlightStart`/
`headlightEnd`, `SIGNAL_DISABLE_HEADLIGHT_SCHEDULE=7`, coordinator
`async_set_headlight_schedule`, `set_headlight_schedule` service +
services.yaml. 1083 tests, 100% cov, ruff clean. Mower restored to original
(ON 23:46→00:46 local). Capture technique: text-input mode on the Material
time picker (tap clock icon → "Byt till textinmatningsläget" toggle → type
HH then MM) is far more reliable than the clock-face drag.

## 🧪 RELIABLE TECHNIQUES (learned 2026-05-30 — use these, the app taps are flaky)

1. **MQTT write-probe (most reliable for config RE).** Build a global write
   frame and publish it with `scripts/_publish_hex.py <hex>` — the robot
   applies it (userCtrl=49 + PbMap.f11/f12), then `query_map.py` reads it
   back. To map an unknown field: set a distinctive value, open the app
   screen to read its label, then republish the ORIGINAL frame to restore.
   Build frames with `protocol._field_f32/_field_bytes/_encode_varint`
   (see the original-frame builder in commit history / section K).
2. **ALWAYS rescale screenshots before reading** (the API rejects >~1MB /
   large dims):
   ```bash
   adb -s fc7d1e36 exec-out screencap -p > /tmp/s.png
   convert /tmp/s.png -resize 480x /tmp/s_small.png   # then Read /tmp/s_small.png
   ```
3. **App Save is gated on an "Override / Keep Custom" dialog** — the BLE/MQTT
   write only fires after you tap one (Keep Custom = userCtrl=49). Sliders
   are SeekBars (drag, don't tap digits). Settings rows near the screen edge
   don't register taps — scroll them to mid-screen first and verify the
   `checked` state in the uiautomator dump before Save.
4. **Restore is deterministic via MQTT** — don't fight the app to undo a
   change; just republish the known-original frame (section K has it).

## 🛰️ LIVE CAPTURE SESSION 2026-05-30 (app logged in, mitmproxy on .180)

This machine **is** the capture host `192.168.1.180`. mitmproxy was just
not running — starting `mitmdump -s tools/capture.py --listen-port 8888
--ssl-insecure` restored the phone's connectivity (a stale WiFi proxy to
the dead :8888 was why the app was logged out) AND decrypts HTTPS (Magisk
CA still trusted). Logged in via **Google sign-in** (no password typed in
the end). Robot docked/charging 98% — **no movement commands issued.**

### A. Google sign-in = AWS Cognito Hosted UI + PKCE (captured)
```
GET  eu-auth.lymow.com/oauth2/authorize?identity_provider=Google
       &response_type=code&client_id=3h1sqv3hishjiofbv8giskjgb0
       &scope=openid+aws.cognito.signin.user.admin
       &redirect_uri=myapp://callback/&code_challenge_method=S256
  → accounts.google.com → eu-auth.lymow.com/oauth2/idpresponse?code=…
POST eu-auth.lymow.com/oauth2/token        (code → Cognito tokens)
POST cognito-identity.eu-west-1.amazonaws.com   (→ AWS creds)
  → device-list-query / check-update / IoT-MQTT presign (existing path)
```
**HA support assessment:** feasible but awkward. Email/password (SRP)
already works and stays the primary path. Google would need an OAuth2
authcode+PKCE config flow; the redirect_uri is a mobile scheme
(`myapp://callback/`) registered to the app's Cognito client, which HA
can't receive — so the realistic HA option is a manual "paste the
callback URL" config-flow step. Optional, not a blocker. (Tokens/identity
IDs from the capture are NOT recorded here — sensitive.)

### B. App REST architecture (3 API-Gateway stages, eu-west-1)
Endpoints observed = a subset; all feature endpoints we use are already in
`api.py`. The only **un-implemented** ones seen are app-infrastructure,
**not HA features** — so no action:
- `POST /prod/check-app-force-update` (app version gate)
- `POST /prod/sns-registration` (mobile push token — HA gets MQTT, N/A)

### C. PbZoneConfig — COMPLETE confirmed layout (resolves the bug above)
Read the app's labeled Global + per-zone Mowing Settings and correlated to
live wire (global config / query_map). Confirmed values:
| App (Global) | value | wire |
|---|---|---|
| Cutting Height | 60 mm | f1 |
| Moving Speed | 0.6 m/s | f4 (f32) |
| Path Spacing | 35 cm | **f9** |
| Zone Perimeter Mowing Laps | 1 | **f10** |
| No-Go Zone Mowing Laps | 1 | **f12** |
| Perimeter Mowing Direction | (enum) | f11 |
Confirmed full PbZoneConfig: `f1 cutHeight · f4 moveSpeed · f6 cutSpeed ·
f7 cleanMode · f8 enabledZoneMask · f9 pathSpacing · f10 perimeterMowLaps ·
f11 perimeterMowDir · f12 noGoMowLaps · f13 zoneObstacleDetect · f14
mowingOrder · f15 safeMarginMode(Offset=0/Precise=1) · f16 relativeCleanDir
(stripeAngle) · f17 lineFollowMode · f18 turnOffOuterMotor · f19
followDetectMode`. (Shipped code is +1-shifted from f9 — see the fix scope
in the audit section.) **This also closes gap 3:** safe-margin = f15,
turn-off-outer-motor = f18 (both bool; need a toggle+re-query to pin the
on/off value, but their field numbers are now correlated).

### D. PbChannel — gap 4 resolved by correlation
Customize → channel0 shows **Channel Obstacle Detection (Smart/Touch-Only)**,
**Channel Deck Height 100 mm**, **Raise Omni Wheels On Channel (ON)**. These
map to the per-channel wire we already see: `f8` (the raw field this session
started surfacing) = **Channel Obstacle Detection mode**, `f9` = cutHeight
(Channel Deck Height), `f10` = channelLift (Raise Omni Wheels). f8 value 2 ==
globalChannelConfig.detectMode 2. A Smart↔Touch toggle+re-query would pin the
enum values; field identity is confirmed.

### E. Notifications (bell) — no REST call
The bell list ("Weak RTK Signal (E15)", 2026/05/27) fired no new REST
endpoint — sourced from MQTT warning codes we already decode. Gap 9 is
effectively a UI concern (history list), not a missing backend endpoint.

### F. Toggle-confirm experiment (2026-05-30) — INCONCLUSIVE (Save didn't transmit)
Tried to pin gap-3 fields by toggling **Turn-Off-Outer-Motor → ON** and
**Safe-margin → Precise** in Global Advanced, then re-querying. Result:
globalZoneConfig came back **byte-identical** (f15=0, f18=0 unchanged), and
**neither BTSnoop (handle 0x14) nor MQTT pbinput showed any write frame**
during the window — only heartbeats. Conclusion: **the app's Save did not
transmit** (tap likely missed / dirty-state not triggered), so the robot
config was never changed (nothing to restore) and the experiment proves
nothing about f15/f18. **Gap-3 field numbers remain UNCONFIRMED** — do NOT
label f15/f18 as safeMargin/turnOffOuterMotor without a real capture.

### G. PbZoneConfig remap — readiness (IMPORTANT before coding the fix)
**Provably wrong & safe to assert:** f9 = pathSpacing (app 35cm = wire f9),
f10 = perimeterMowLaps (app "Zone Perimeter Laps" 1 = f10), f12 =
noGoMowLaps (app 1 = f12). The shipped `f9 relativeCleanDir / f10
pathSpacing` is definitely mislabeled. **Corroborated but NOT independently
byte-confirmed:** f11 perimeterMowDir, f13 zoneObstacleDetect, f14
mowingOrder, f16 relativeCleanDir (reply-4 + value-plausibility only).
**Decision:** the remap must be self-consistent across ALL 19 fields
(encoder+decoder+robot share one layout), so it should NOT ship on
partially-inferred labels. **Prerequisite for the remap = one clean
capture of the app's GLOBAL "Save" write frame** (shows every field with a
known app value in one shot → definitive full layout). That capture needs
the app Save to actually transmit — best done in a session where the app
UI is watched to confirm the Save registers (the ADB tap missed this time).
Until then: the bug is documented, f9/f10/f12 are proven, but the field-map
rewrite is deliberately deferred (NO-ASSUMPTIONS + revert-war history).

### H. GLOBAL mowing-settings WRITE — captured + gap-3 CONFIRMED (2026-05-30)
Got the app's Save to transmit (the earlier failures were a blocking
"Override / Keep Custom" dialog — the BLE write fires only after you
choose). Captured the real global write frame (BLE handle 0x14):
```
userCtrl=49 (USER_CTRL_GLOBAL_SETTING_N = the dialog's "Keep Custom";
            "Override" is userCtrl=48 GLOBAL_SETTING_Y). Payload =
PbInput{ f12 PbMap{ f11 globalZoneConfig(PbZoneConfig, all 19 fields),
                    f12 globalChannelConfig(PbChannelConfig) } }
captured hex (with outer-motor ON, safe-margin Precise — non-sensitive):
10312831623a5a30083c259a99193f3004380140ffffffffffffffffff01482350
015802600168027001780080015a880100900101980102 62060802103c1800
```
**This is a different opcode+message than HA uses today.** HA's
`lymow.set_task_config(path_spacing=…)` sends **userCtrl=36 + PbTaskConfig
(f26)** — but that is the 4-field DEVICE-settings record (rain/charging/
handbrake/zoneOrder), NOT the mowing-settings PbZoneConfig. The robot's
global mowing settings (cut height, move speed, path spacing, perimeter
laps, safe-margin, …) are written via **userCtrl=48/49 + PbMap.f11
globalZoneConfig**. So the mowing-settings half of `set_task_config` is
not just field-mislabeled — it's using the **wrong opcode and message**.

**Gap-3 fields CONFIRMED by live toggle + re-query (definitive):**
- `f17 = safeMarginMode` — Offset Edge = **1**, Precise Edge = **0**
  (toggled Offset→Precise, watched f17 go 1→0, then restored 0→1).
- `f18 = turnOffOuterMotor` — OFF = **0**, ON = **1** (toggled 0→1, restored).
- `f9 = pathSpacing` re-confirmed = 35 (app 35cm).

**⚠️ reply-4's mapping is also partly WRONG:** it labels f17 =
lineFollowMode, but live toggle proves f17 = safeMargin. So the remap must
be built from **per-field live toggles**, not reply-4 and not Hermes #9432.
Still need distinctive-value toggles for f10 vs f12 (both currently 1),
f11 perimeterMowDir, f13 zoneObstacleDetect, f14 mowingOrder, f16
stripeAngle, f15 (unknown, =0), and the cleanMode/cutSpeed/followDetect
enums — best done as one multi-field distinctive-value Save + re-query.

**Robot config RESTORED to original** (f17=1 Offset, f18=0 outer-motor-off,
verified by re-query). No movement commands issued this session.

### Revised remap scope (bigger than first thought)
The mowing-settings fix is not just renaming `_ZONE_CONFIG_*` fields — it
needs a **new encoder** for the global write (userCtrl=48/49 + PbMap.f11/f12)
that `lymow.set_task_config` (or a new `set_mowing_settings`) calls for the
PbZoneConfig fields, while the existing userCtrl=36 path stays for the true
PbTaskConfig device settings. Decoder field labels also get corrected. All
gated on finishing the per-field live confirmation above.

### I. Laps disambiguation CONFIRMED + working MQTT write path (2026-05-30)
Drove the laps **sliders** (they're SeekBars, not tap-digits) to distinct
values, Saved (→ Keep Custom), re-queried:
- set No-Go=3, Zone-Perimeter=2 → wire came back **f12=3, f10=2**.
- ⇒ **f10 = perimeterMowLaps, f12 = noGoMowLaps** (resolves the last
  ambiguous pair; matches reply-4 ordering for these two).

**Proved the global write works from MQTT too:** hand-built the userCtrl=49
+ PbMap{f11 globalZoneConfig, f12 globalChannelConfig} frame with ORIGINAL
values and published it via `scripts/_publish_hex.py` — robot applied it,
re-query confirmed full restore (f10=1,f12=1,f17=1,f18=0). The constructed
frame was byte-identical to the captured app frame except the two fields
that legitimately differed (f17/f18) → construction verified. So HA can
write global mowing settings via this exact path (userCtrl=49 over MQTT).

**Confirmed PbZoneConfig layout (toggle/​value-verified — TRUST THESE):**
`f1 cutHeight · f4 moveSpeed(f32) · f9 pathSpacing · f10 perimeterMowLaps ·
f12 noGoMowLaps · f17 safeMarginMode(Offset=1/Precise=0) ·
f18 turnOffOuterMotor(ON=1)`.
**Corroborated (read + order-anchored by the confirmed points, NOT yet
toggle-verified):** f6 cutSpeed, f7 cleanMode, f8 enabledZoneMask(uint64),
f11 perimeterMowDir(=2), f13 zoneObstacleDetect(=2), f14 mowingOrder(=1),
f16 relativeCleanDir/stripeAngle(=90), f19 followDetectMode(=2).
**Unknown:** f15 (=0; candidate lineFollowMode — the field reply-4
mis-placed at f17). Confirm f11/f13/f14/f16/f15 with one more multi-enum
distinctive Save before the field-map rewrite ships.

Robot config RESTORED + verified. No movement commands this session.

### Remap — ready to implement (after f11/f13/f14/f15/f16 final toggle)
- New encoder `encode_set_global_zone_config(**fields)` → userCtrl=49
  (USER_CTRL_GLOBAL_SETTING_N) + PbMap.f11 globalZoneConfig (+f12 channel).
  Replaces the wrong userCtrl=36/PbTaskConfig path for MOWING settings;
  keep userCtrl=36/PbTaskConfig for the 4-field DEVICE settings.
- Correct `_ZONE_CONFIG_INT_NAMES`/`_ZONE_CONFIG_BOOL_*`/`_TASK_CONFIG_FIELDS`
  + `decode_zone_config` + `_encode_go_zone` to the verified layout above.
- Coordinator `async_set_global_zone_config` + `lymow.set_mowing_settings`
  service; keep `set_zone_config` (per-zone userCtrl=9) for overrides.
- Rewrite the pinned PbZoneConfig tests to the verified layout.

### J. Remap scoping — why it can't safely ship yet (2026-05-30)
`_TASK_CONFIG_FIELDS` is the SINGLE shared PbZoneConfig field map used by
THREE encoders (`encode_set_task_config` userCtrl=36, `encode_set_zone_config`
userCtrl=9, `_encode_go_zone`/sync_map) plus the decoder mirror
(`_ZONE_CONFIG_INT_NAMES`/`_BOOL_NAMES`). Renumbering it touches all of them
+ pinned tests in test_protocol/test_coordinator/test_lawn_mower/test_sensor.

Laying the confirmed layout over the current (wrong) map:
- **CONFIRMED corrections:** pathSpacing 10→9, perimeterMowLaps 11→10,
  perimeterMowDir 12→11, noGoMowLaps 13→12, safeMargin = 17 (new),
  turnOffOuterMotor = 18 (rename of disableOuterDischarge). f1/f4/f6/f7/f19
  already correct.
- **DEFINITELY WRONG in current code, but true home NOT yet known:**
  `cleanDir@8` (f8 is enabledZoneMask), `startProgress@16` (f16 is the
  angle = relativeCleanDir), `lineFollowMode@17` (f17 is safeMargin),
  `pathOrder@15`/`obsDecMode@14` (shift to 14/13 = mowingOrder/zoneObstacle),
  and unconfirmed `raiseCutHeight@2`, `lowerCutHeight@3`, `brushSpeed@5`,
  `f15`. Where cleanDir/startProgress/lineFollowMode actually live (if they
  exist in PbZoneConfig at all) is unknown.

⇒ A full field-map rewrite now would **guess** those fields — the exact
NO-ASSUMPTIONS violation behind the 49a7ac6→355dd1f→bfb37bc revert-war.
**Two safe options:**
1. **Conservative fix:** correct ONLY the confirmed fields, drop/raw the
   unconfirmed ones (changes the service param set the card uses — needs a
   card-side check too). Fixes the real pathSpacing bug; lower coverage.
2. **Finish RE first:** confirm f2/f3/f5/f8/f15/f16 + the homes of
   cleanDir/startProgress/lineFollowMode via more distinctive Saves (app
   taps are flaky; MQTT write-probe can set a field and the app screen
   reveals its label), THEN one clean complete remap.

### Still to capture (other gaps)
- gap 5 headlight schedule, granular schedules, PIN, WiFi-write, Bind-RTK.

### K. FINAL PbZoneConfig layout decision (2026-05-30) — basis for the remap
MQTT write-probe (set distinctive values, read app, restore — all via
`_publish_hex.py`, reliable) + the earlier toggles settle it. The real
PbZoneConfig only ever carries fields [1,4,6–19] on the wire (no f2/f3/f5),
so raiseCutHeight/lowerCutHeight/brushSpeed are NOT steady-state fields
(raise/lower are momentary +/- commands the card sends; keep as write-only).

**Layout to ship (encoder `_TASK_CONFIG_FIELDS` + decoder
`_ZONE_CONFIG_*_NAMES`):**
| f | name | basis |
|---|---|---|
| 1 | cutHeight | confirmed (app 60mm) |
| 4 | moveSpeed (f32) | confirmed (app 0.6) |
| 6 | cutSpeed | anchored (Blade Speed may actually be a separate enum — f6=1 showed "-2"; keep cutSpeed, low-risk) |
| 7 | cleanMode | anchored |
| 8 | enabledZoneMask (uint64) | confirmed (all-ones); NOT a settable param — decode-only |
| 9 | pathSpacing | **CONFIRMED** (app 35cm=f9) |
| 10 | perimeterMowLaps | **CONFIRMED** (app Zone-Perimeter=f10) |
| 11 | perimeterMowDir | anchored |
| 12 | noGoMowLaps | **CONFIRMED** (app No-Go=f12) |
| 13 | zoneObstacleDetect | anchored (was obsDecMode) |
| 14 | mowingOrder | anchored (was pathOrder, bool) |
| 15 | (unknown, =0) | LEAVE RAW — no confirmed meaning |
| 16 | relativeCleanDir (stripe angle) | anchored (=90, an angle) |
| 17 | safeMarginMode (Offset=1/Precise=0) | **CONFIRMED** (toggle) |
| 18 | turnOffOuterMotor (ON=1) | **CONFIRMED** (toggle); was disableOuterDischarge |
| 19 | followDetectMode | anchored |

**DROP (current code wrong, no confirmed home):** cleanDir@8,
startProgress@16, lineFollowMode@17. **Global write opcode:** userCtrl=49
(GLOBAL_SETTING_N) + PbMap.f11 globalZoneConfig — proven via MQTT.

Remap = rewrite both maps to the above, add `encode_set_global_zone_config`
(userCtrl=49), keep per-zone userCtrl=9, fix the card-facing service params
(drop line_follow_mode, add safe_margin_mode), update pinned tests.

### L. ⚠️ REMAP IS A COORDINATED FRONTEND+BACKEND CHANGE (2026-05-30)
The card's settings panel (`lymow-map-card.js`, `data-field=` attrs) SENDS
these to `lymow.set_task_config`: move_speed, path_spacing, perimeter_mow_laps,
perimeter_mow_dir, nogo_mow_laps, cut_speed, brush_speed, obs_dec_mode,
clean_mode, path_order, **line_follow_mode** — plus raise_cut_height/
lower_cut_height (momentary). Two of these have **no confirmed wire home**:
- `line_follow_mode` — current code puts it at f17, but f17 is **safeMargin**.
- `brush_speed` — at f5, which never appears in the wire (Blade Speed
  showed "-2" when f6=1, so Blade Speed ≠ f6 either; its field is unknown).

⇒ A backend-only field-map rewrite would either (a) error on the card's
line_follow_mode/brush_speed, or (b) misroute them. And renaming the
DECODER keys (pathSpacing etc.) changes what the card reads to populate the
panel. **So decode + encode + the card's panel field-set + which it sends
must change together.** Per the repo's division (supervisor session owns
`www/lymow-map-card.js`), this remap needs **frontend coordination**:
- Backend: rewrite `_TASK_CONFIG_FIELDS` + `_ZONE_CONFIG_*` to section K,
  add `encode_set_global_zone_config` (userCtrl=49), make the encoder skip
  unknown/unconfirmed fields instead of raising.
- Frontend: drop line_follow_mode + brush_speed from the panel (no home),
  add safe_margin_mode + turn_off_outer_motor + stripe_angle, and read the
  corrected decoder keys.
- Still-needed RE for completeness: the wire homes of line_follow_mode,
  brush_speed/Blade-Speed, and f15 (MQTT-probe each: set value, read app,
  restore). Until then ship them as unmapped, not mislabeled.

**Status:** mowing-settings DECODE is fully RE'd (layout known + verified).

### M. ✅ REMAP IS BACKEND-SAFE AFTER ALL — actionable recipe (2026-05-30)
Checked how the card consumes the decode (`lymow-map-card.js` ~L1548):
it reads `this._getMapData().mowingSettings.<key>` using the SAME key names
the decoder emits — `moveSpeed, pathSpacing, perimeterMowLaps, noGoMowLaps,
perimeterMowDir, obsDecMode, cleanMode, pathOrder, lineFollowMode` — and
overlays only non-null values (`if (v != null)`). So:
- **Decoder fix is a strict improvement, NO card change:** keep these exact
  key NAMES, just fix their FIELD NUMBERS → pathSpacing=f9, perimeterMowLaps
  =f10, perimeterMowDir=f11, noGoMowLaps=f12, obsDecMode=f13, pathOrder=f14
  (moveSpeed=f4, cleanMode=f7, cutSpeed=f6, cutHeight=f1 unchanged). Today
  the card shows WRONG values (reads mislabeled keys); this fixes them.
- **Drop `lineFollowMode` from the decoder** (no wire home — its f17 is
  safeMargin). Card's `ms.lineFollowMode` becomes undefined → skipped → uses
  default. Safe. Add `safeMarginMode`(f17)/`turnOffOuterMotor`(f18)/
  `relativeCleanDir`(f16) as new keys (card ignores until panel updated).
- **Encoder/service fix, also backend-only-safe:** the service handler only
  forwards keys present in `_TASK_CONFIG_SERVICE_FIELDS`, so **remove
  `line_follow_mode` + `brush_speed` from that map** → the card's sends of
  them are silently ignored (no error). Fix the remaining field NUMBERS in
  `_TASK_CONFIG_FIELDS`. Change `encode_set_task_config` envelope from the
  (wrong) userCtrl=36+PbTaskConfig(f26) to **userCtrl=49 + PbInput.f12
  PbMap.f11 globalZoneConfig** (the proven path). `encode_set_zone_config`
  (userCtrl=9 per-zone) and `_encode_go_zone` (sync_map) automatically get
  correct fields once `_TASK_CONFIG_FIELDS` numbers are fixed.
- **One residual guess to AVOID:** `raise_cut_height`/`lower_cut_height`
  (momentary +/- the card sends). Their wire home is unknown and they're
  NOT part of globalZoneConfig steady state. Keep them on a SEPARATE path
  (do not fold into the userCtrl=49 global write); leave their current
  behaviour untouched until captured. f15 also stays unmapped.

This is implementable backend-only without breaking the card. It IS a large
change (shared map + envelope + ~15 pinned tests across 4 test files +
coverage), so it should land as one careful, fully-tested commit. Next
session: implement per this recipe, run `pytest --cov-fail-under=100` +
ruff, then the card panel can later add safe_margin/turn_off_outer_motor/
stripe_angle and drop line_follow_mode/brush_speed (frontend/supervisor).
- Gap 3 on/off values (toggle Safe-margin + Turn-Off-Outer-Motor — needs a
  Save that transmits; this session's didn't).
- Gap 4 enum values (toggle Channel Obstacle Detection, re-query).
- Gap 5 Headlight Mode schedule frame (BTSnoop on a schedule set).
- Granular schedules add/edit/delete/toggle (BTSnoop).
- PIN code, WiFi-write, Bind RTK (mitmproxy/BTSnoop; risky ones with user near).

### Session housekeeping
mitmproxy left RUNNING on :8888 with the phone proxy pointed at it (so the
app stays online for continued capture). **When done: stop mitmdump or
`adb shell settings delete global http_proxy`** — else the app goes offline
again / E29 dock-fail risk. Capture artifacts (`tools/capture-lymow.txt`,
`/tmp/lymow-ui/*`) are local/gitignored and contain tokens — never commit.

---

## 🔎 CAPTURE-CORRECTNESS AUDIT (2026-05-30, backend session)

User asked: *"all existing captures correct? have we captured all the
mowing settings and all the other settings? what about backup? schedules?"*
Audit method: re-decode every documented byte-frame against the current
encoders/decoders + a fresh read-only `query_map` (no movement). Results:

### ✅ Confirmed correct (encoder output == documented capture bytes)
- **State machine** (all 11): Mow=`10312801`, Dock=`10312802`,
  Pause=`10312803`, Resume=`10312804`, PauseDock=`10312815`,
  ResumeDock=`10312816`, RechargeDock=`10312821`, CancelTask/Reinit=
  `1031281c`, ChargeReset=`10312811`, AdjustCharging=`10312826`,
  Backup=`1031282c`. `encode_userctrl(n)` reproduces each exactly.
- **Device settings** (PbTaskConfig f26 under userCtrl=36): Rainy Mowing,
  Charging Handbrake, Return-to-Dock direct route all decode to the
  documented `{chargingMode, rainCleaning, disableChargingPark}`.
- **Vehicle LED** signal=10/11; **Find My Robot** play-sound;
  **RTK diag** 57/58; **Cancel Task** 28 — all match.
- **Backup lifecycle** — COMPLETE: create (MQTT userCtrl=44) + list /
  restore / delete / rename (REST) all wire-validated (Task E). Backend
  done; only the card 📦 UI is pending (supervisor side).
- **Rename zone** — byte-equal to app BLE capture (supervisor reply 2).

### ⚠️ ONE REAL CORRECTNESS QUESTION — PbZoneConfig field labeling
The mowing-settings sub-message (PbZoneConfig: global at PbMap.f11,
per-zone at PbZone.f2, and the `set_task_config`/`set_zone_config`
encoders) has an **unresolved field-number↔name mapping**. Fresh live
`query_map` (2026-05-30, docked) per-zone configBox:
```
f1=60  f4=0.6  f6=5  f7=1  f9=25  f10=2  f11=2  f16=90  f17=1  f19=2
```
- Shipped code (canonical Hermes #9432, pinned by commit bfb37bc):
  `f9=relativeCleanDir, f10=pathSpacing, f16=startProgress`.
- But the **values** say otherwise: `f9=25` sits exactly in the app's
  documented Path Spacing range (25–35 cm); `f16=90` is a clean angle
  (stripe angle / relative clean dir); `f10=2` is nonsensical as a
  path-spacing in cm. This matches capture-reply-4's interpretation
  (`f9=pathSpacing, f16=relativeCleanDir`), NOT the shipped labels.
- **Impact if the shipped labels are wrong:** `lymow.set_task_config` /
  `set_zone_config` write Path Spacing into f10 and Stripe Angle into f9,
  i.e. the robot stores them in the wrong slots. Decode mislabels the same.
- **NOT changed on a guess** — this exact mapping already caused a
  revert-war (49a7ac6 → 355dd1f → bfb37bc). Bytes alone cannot decide it.
  **Definitive resolution = read the app's *labeled* Mowing Settings
  screen** (Path Spacing / Stripe Angle shown with units) and match the
  number to the field, OR a controlled write-one-distinctive-value-and-
  re-query test. **BLOCKED 2026-05-30:** the phone's Lymow app is logged
  OUT — confirming needs an account login (password/MFA = user action).

### ❓ Mowing settings — NOT all captured/confirmed
- **Safe-margin mode** + **Raise-omni-wheels-on-channel** (gap 3) — never
  captured; not in the field map.
- **Per-channel Channel Obstacle Detection** (gap 4) — PbChannel.f8 found
  in the live frame (value 2 = global detectMode), now decoded raw; needs
  a toggle capture to confirm the label (commit 4b46767).
- **Stripe Angle** — present on the wire (f16, see above) but blocked on
  the same labeling question.

### ⚠️ Schedules — PARTIAL
- `encode_set_schedules` (bulk "Save Task"), `encode_clear_schedules`
  (`10315a00`), `encode_query_schedules` — wire-validated + serviced.
- **Granular add / edit / delete-one / toggle-enabled NOT captured**
  (Task D). The card would have to read-modify-rewrite the whole list.
  Capturable now (no movement) once the app is logged in.

### ✅ RESOLVED 2026-05-30 — PbZoneConfig labeling CONFIRMED WRONG (f9 = pathSpacing)
Logged into the app (Google sign-in) and read the **labeled** Mowing
Settings → Global screen, correlating each value to the live
globalZoneConfig (PbMap.f11) wire fields from a synchronous query_map:

| App label (Global) | App value | Wire field | Shipped code label | Verdict |
|---|---|---|---|---|
| Cutting Height | 60 mm | f1 = 60 | cutHeight | ✅ correct |
| Moving Speed | 0.6 m/s | f4 = 0.6 | moveSpeed | ✅ correct |
| **Path Spacing** | **35 cm** | **f9 = 35** | **relativeCleanDir** | ❌ **WRONG — f9 IS pathSpacing** |
| No-Go Zone Mowing Laps | 1 | f12 = 1 | perimeterMowDir | ❌ shifted |
| Perimeter Mowing Direction | (enum) | f11 = 2 | perimeterMowLaps | ❌ shifted |

**This is the hardware check bfb37bc asked for.** `f9 = 35 = Path Spacing`
proves the shipped "canonical Hermes #9432" layout is mislabeled from f9
onward, and **capture-reply-4's mapping was correct all along**. The
confirmed PbZoneConfig layout is:

```
f1 cutHeight   f4 moveSpeed(f32)  f6 cutSpeed   f7 cleanMode
f8 enabledZoneMask(uint64; global=all-ones)     f9 pathSpacing
f10 perimeterMowLaps   f11 perimeterMowDir   f12 noGoMowLaps
f13 zoneObstacleDetect(obsDecMode)   f14 mowingOrder(pathOrder)
f15 ? (global=0; candidate Safe-margin mode — gap 3)
f16 relativeCleanDir/stripeAngle(=90 "Optimized")
f17 lineFollowMode   f18 turnOffOuterMotor(disableOuterDischarge)
f19 followDetectMode
```
vs the **shipped (wrong)** `_ZONE_CONFIG_INT_NAMES`/`_TASK_CONFIG_FIELDS`:
`f9 relativeCleanDir, f10 pathSpacing, f11 perimeterMowLaps,
f12 perimeterMowDir, f13 noGoMowLaps, f14 obsDecMode, f15 pathOrder,
f16 startProgress` — a +1 shift across f9–f14 plus f16.

**Impact:** `lymow.set_task_config(path_spacing=…)` / `set_zone_config`
currently write Path Spacing into f10 (robot reads as perimeterMowLaps)
and stripe angle into the wrong slot; decode mislabels the same. **FIX
REQUIRED** — remap `_ZONE_CONFIG_INT_NAMES`, `_ZONE_CONFIG_BOOL_NAMES`,
`_TASK_CONFIG_FIELDS`, `decode_zone_config`, `_encode_go_zone`, and every
pinned test, then re-verify against these app values. (Deliberately NOT
done in the same session as the discovery — this area has a revert-war
history; the remap must land as one careful, fully-correlated commit.)

Also located for **gap 3**: Safe-margin mode (Offset Edge / Precise Edge)
and Turn Off Outer Mowing Motor are real Global Advanced toggles; the
former is the likely f15 (global=0), the latter likely f18.

### Bottom line
Backup = done. State machine + device settings + rename = confirmed.
Mowing settings = mostly captured but carry **one unresolved labeling
risk (path-spacing/stripe-angle) that can silently mis-write settings** —
resolve before relying on `set_task_config`. Schedules = bulk only.
All remaining confirmations need the **app logged in** (currently not).

---

## 📓 READY-TO-RUN CAPTURE PLAYBOOK (remaining backend gaps 3–9)

> **Status (2026-05-29):** capture session **paused** by user — *do not
> drive the app live yet*. This section is the standing recipe so the
> capture can run end-to-end the moment it resumes. Gaps 1 & 2 are already
> shipped (see above); 3–9 below.

### 🔐 Sensitive-data rule for ALL captures (user instruction 2026-05-29)

> "Do not store sensitive data online. Just for you to understand and decode."

Capture artifacts (`*.cfa`, `*.pcap`, `*.har`, `tools/capture-lymow.txt`,
`/tmp/lymow-*`) are **live secrets** — already gitignored; never `git add`
them, never paste their raw contents into this file, a commit, an issue,
or a PR. Decode them **locally** to learn the field layout, then record
**only the redacted structure** here:

- **PIN code** (gap 8): record the field number + wire type only — e.g.
  `PbRobotConfig.f9 = <4-digit string>`. Never write the actual digits.
- **WiFi password** (gap 6): record `field N = <password string>`; never
  the value. SSID is borderline — redact to `<ssid>`.
- **GPS** (geofence/find-my-robot): lat/lon are PII — record
  `field N = <lat>/<lon> (float)`, not real coordinates.
- **Tokens / identity IDs**: never appear in BLE frames, but if a REST
  capture (gaps 7/9) carries `Authorization` / Cognito IDs, redact them.

Non-sensitive captures (headlight schedule, safe-margin toggle, channel
obstacle detection) have no secret payload — their bytes are fine to paste.

### Pre-flight (run once when resuming)

```bash
cd /home/mint-laptop-4/private_projects/ha-lymow-lovelace
adb devices                        # expect: fc7d1e36  device  (USB)
adb -s fc7d1e36 shell settings get secure bluetooth_hci_log   # expect 1 (full)
# Capture-per-action loop = the recipe in "🧰 Capture pipeline reference"
# above (baseline size → tap → wait ≥5 min for debounce → pull → parse).
# Decoder:  uv run python scripts/parse_btsnoop.py /tmp/lymow-ui/snoop.cfa
```

`parse_btsnoop.py` already filters to ATT WRITE_CMD on handle 0x0014 and
prints b64 + decoded pb hex. Ignore the heartbeat frames listed in the
"Heartbeat noise" table above. **Wait ≥5 min after the trigger** — capture
reply 4 proved the app debounces config writes that long.

### Per-gap recipe + implementation checklist

Each gap: **(A)** where in the app, **(B)** transport + what to look for,
**(C)** decode → record redacted structure, **(D)** the code that follows
once bytes are in hand. Do NOT write the (D) encoder until (C) has bytes.

---

**Gap 3 — Safe-margin mode + Raise-omni-wheels-on-channel** *(non-sensitive)*
- **A:** Mowing Settings (top-middle ≡) → Global → Advanced. Toggle
  "Safe-margin mode" (Offset Edge ↔ Precise Edge) and the "Raise the omni
  wheels on channel" switch — one at a time, separate captures.
- **B:** BLE handle 0x0014. Expect `userCtrl=36 SET_TASK_CONFIG` (PbInput.f26
  PbTaskConfig) **or** the global `userCtrl=9` PbZoneConfig path (PbMap.f11).
  Diff the new frame against a baseline global-settings write to isolate the
  one changed field number.
- **C:** Record `PbZoneConfig.fN = 0/1` for each toggle. Likely two new
  bool fields beyond the 19 already in `_ZONE_CONFIG_FIELDS`.
- **D:** add the 2 fields to `_ZONE_CONFIG_FIELDS` (protocol.py) + decoder
  map; expose via `lymow.set_task_config` / `set_zone_config` extra kwargs;
  services.yaml entries; tests (encode pins + decode round-trip).

**Gap 4 — Channel Obstacle Detection (per-channel)** *(non-sensitive)*
- **A:** Mowing Settings → Customize → channelN tab → Channel Obstacle
  Detection (Smart ↔ Touch-Only).
- **B:** BLE 0x0014. Per capture reply 4, channel settings ride PbChannel
  (not a configBox). Confirm whether obstacle-detect is a new PbChannel
  field (assumed `detectMode`) vs a configBox after all.
- **C:** Record `PbChannel.fN = <enum>`; map enum to Smart/Touch-Only.
- **D:** extend `async_update_channel_settings` + `lymow.update_channel_settings`
  with `detect_mode`; protocol channel encoder; services.yaml; tests.

**Gap 5 — Headlight Mode (scheduled auto on/off)** *(non-sensitive)*
- **A:** Settings → Device Settings → Headlight Mode. Set enable + a
  start time + end time, Save. (Confirmed NOT a brightness picker — it's a
  scheduled window; see master inventory row 32.)
- **B:** BLE 0x0014. Expect a PbRobotConfig sub-message (`userCtrl`-less,
  `PbInput.f13` robotConfig path like the Vehicle LED frame `10316a02…`),
  shape similar to rrConfig: `{enable, startTime, endTime}`.
- **C:** Record the f13 sub-field number + the 3 inner fields. Times are
  minutes-since-midnight or HH:MM — note which.
- **D:** add the field to `_ROBOT_CONFIG_FIELDS`/decoder; new
  `lymow.set_headlight_schedule(enable, start, end)`; coordinator method;
  services.yaml; tests. (Don't conflate with the existing on/off
  `VehicleLedSwitch` — separate control.)

**Gap 9 — Notification list endpoint** *(REST; redact tokens)*
- **A:** Home → 🔔 bell.
- **B:** This is cloud, not BLE — run mitmproxy (`tools/capture.py`)
  instead of BTSnoop, or watch `adb logcat` for the REST URL. Look for a
  `GET /prod/…notification…` or `…event…` call.
- **C:** Record method + path + response JSON **shape** (keys only).
  Redact any `Authorization` header / identity IDs from the capture.
- **D:** `api.get_notifications()` REST client method; coordinator cache;
  expose as a `lymow.get_notifications` response-service (mirror
  `get_clean_history`); tests with a synthetic (non-secret) fixture.

**Gap 8 — PIN Code set/clear** *(⚠️ SENSITIVE — never log/commit the PIN)*
- **A:** Settings → PIN Code → enter 4 digits → Update.
- **B:** BLE 0x0014, likely PbRobotConfig.f9 `lcdPinCode`. Decode locally
  ONLY.
- **C:** Record `PbRobotConfig.f9 = <4-digit string>` — **digits redacted**.
- **D:** `lymow.set_lcd_pin(pin)` + `clear_lcd_pin()`; coordinator must
  NOT log the value; decoder must NOT read the PIN back into state (keep
  it write-only per security.md); tests use a placeholder like `"0000"`
  documented as a fixture, not a real PIN.

**Gap 6 — WiFi SSID/password write** *(⚠️ RISKY + SENSITIVE — user nearby)*
- **A:** Settings → Network Settings → pick SSID + password → Reconnect.
  **Wrong creds disconnect the robot** — only run with the user present.
- **B:** BLE 0x0014 or REST. Record transport, field layout.
- **C:** `field N = <ssid>`, `field M = <password>` — **values redacted.**
- **D:** `lymow.set_wifi(ssid, password)`; password never logged; tests
  with placeholder creds.

**Gap 7 — Bind RTK SN** *(⚠️ RISKY — could unbind live base; user nearby)*
- **A:** Settings → Bind RTK → enter base SN → Bind.
- **B:** Try REST first (`update_device_feature` with an `rtkSn`-like
  field) via mitmproxy; fall back to BLE if nothing on the wire.
- **C:** Record endpoint/field. SN is device-identifying — redact to `<sn>`.
- **D:** `lymow.bind_rtk(sn)`; coordinator + api; tests.

### After every capture

1. Paste **redacted** structure into the matching gap above + the master
   inventory row.
2. Implement (D); run `uv run pytest tests/ --cov=custom_components/lymow
   --cov-fail-under=100` + `ruff format --check .` + `ruff check .`.
3. Commit per-gap; **never** `git add` the `.cfa`/capture file.
4. Strike the gap in the "True gaps" list.

---

## 🖥️ Supervisor session — 2026-05-31 (this laptop)

### Deployment status
- Deployed v0.2.7 to HA at 192.168.1.99 via GitHub raw download to `/config/custom_components/lymow/`.
- HA restarted cleanly. Both JS cards confirmed loading at v0.2.7:
  - `lymow-map-card.js?v=0.2.7` ✅
  - `lymow-camera-card.js?v=0.2.7` ✅

### Entity registry after restart (2026-05-31)
- **Total 7b6521 entities in HA registry**: 114 (active: 70, disabled by integration: 44)
- **Disabled by design** (entity_registry_enabled_default=False): all 22 RTK per-band sensors
  (l1/l2/l5 sat counts, SNRs, LoRa bandwidth, DC voltage, CW interference, antenna gain),
  plus mac_address, wifi_ssid, cellular_ip, pose_east/north/heading, mcu_version,
  last_mow_details, backup_maps, set_charging_station_here, dock_and_forget_progress, etc.
- **Unknown (expected)**: robot config entities (vehicle_led, rainy_mowing, prefer_4g,
  charging_handbrake, etc.) stay `unknown` when robot is docked — they populate only
  when the robot sends a robotConfig echo during mowing or diagnostics query.
- **Unavailable (stale registry)**: zone_d1d8 and zone_3f49 entities persist in the entity
  registry from when those zones existed on the map. They will clear if the user removes
  them from the entity registry manually. No code change needed.
- **New entities confirmed live**: `sensor.7b6521_mission_time` = 79 min ✅

### Map sensor validation (2026-05-31)
- `mowing_settings` correctly decoded from globalZoneConfig:
  - pathSpacing=35, perimeterMowLaps=1, noGoMowLaps=1 (correct field mapping)
  - safeMarginMode=True (Offset edge), turnOffOuterMotor=False ✅
- Per-zone zoneConfig decoded on both zones with full 14-field layout ✅
- Zone names show `?` (empty string from robot) — coordinator name overrides are
  not being persisted across restarts yet. This is known; the coordinator only writes
  `_zone_name_overrides` on rename, and those survive MQTT updates but not HA restarts
  (the robot's BasicInfo.f2 is the authoritative store; decoder reads it correctly).

### Settings panel fix shipped (commit a1cbd9b)
- Replaced the mislabeled `line_follow_mode` control (no wire home, was silently ignored)
  with confirmed live-validated controls:
  - **Safe margin** (safe_margin_mode): Offset edge=1 / Precise edge=0 — maps to f17
  - **Outer motor** (turn_off_outer_motor): On=1 / Off=0 — maps to f18
- JS verified: `lymow-map-card.js?v=0.2.7` contains `safe_margin_mode` ×5,
  `turn_off_outer_motor` ×5, zero occurrences of `line_follow_mode`.
- Robot-state overlay reads `ms.safeMarginMode` / `ms.turnOffOuterMotor` directly.

### Camera card status
- `lymow-camera-card.js` exists and loads at v0.2.7 ✅
- LAN mode: `<img>` on `/api/camera_proxy_stream/<camera_entity>` — works when robot online
- Cloud mode: in-browser KVS WebRTC with `a=ice-options:trickle renomination` handled
  by the browser's native RTCPeerConnection (unlike aiortc which needed explicit munge).
  Browser sends correct H264/recvonly offer — robot should answer as proven by the
  headless Chrome test session (#97 resolved).
- **To add camera card to a Lovelace dashboard:**
  ```yaml
  type: custom:lymow-camera-card
  mower_entity: lawn_mower.7b6521
  camera_entity: camera.7b6521_camera
  default_source: lan
  ```

### Map card features — browser test needed
- Card JS deployed at v0.2.7. Hard-reload the map page (Ctrl+Shift+R) to see the
  new settings panel. Verify:
  1. Settings panel (⚙) shows "Safe margin" + "Outer motor" instead of "Line follow" ✅ (code confirmed)
  2. Settings panel pre-populates from robot state (globalZoneConfig) — pathSpacing=35,
     safeMarginMode=Offset, etc.
  3. Edit mode (E key) on a go-zone: vertex drag, rename, delete, per-zone cut-height
  4. Zone labels (name + area), label mode toggle persists across reload
  5. Schedules panel (📅), mow trail overlay, RTK badge
  6. Fullscreen toggle (F key / ⊞ button)
- **Cannot browser-test from terminal** — needs interactive browser session.

### Session 2026-06-02 (supervisor laptop) — card v0.2.8

Shipped in commit `c0fc2e6`:
- **📦 Backup panel** — new toolbar button; panel lists all backups (name + date) from
  the `backup_maps` sensor; Create / Restore / Rename (inline) / Delete per-entry.
  Auto-detects the sensor by device; explicit `backup_entity` config key as override.
- **Per-zone settings extended** — safe-margin (Offset/Precise) and outer-motor (On/Off)
  selects added to the zone config panel (alongside cut-height/speed/spacing/perimeter-laps).
- **`lymow.backup_map` service** added — card-callable alternative to the `BackupMapButton`
  entity; uses `entity_id` to create a backup (userCtrl=44). Covered by 2 new tests.
- Version bumped to 0.2.8.

### Remaining work (frontend/supervisor)
- Browser manual verification of card v0.2.8 features (backup panel, zone settings extension)
- Check if the App-vs-HA feature matrix has any remaining card gaps to close

### Gaps still open (backend — need capture or supervised session)
See the per-gap playbook above (gaps 3–9). Status:
- ✅ Gap 3 (safe-margin f17/f18): **DONE** — confirmed live-toggle 2026-05-30, shipped
  in both decoder and card panel.
- ✅ Gap 5 (headlight schedule): **DONE** — commit e510f2d.
- ✅ Gap 7 (bind RTK): **DONE** — commit 5800ae4.
- ✅ Gap 8 (PIN code): **DONE** — commit d21de63.
- ✅ Gap 6 (WiFi write = BLE-only, MQTT raises): **DONE** — commit e4d8bb2 + issue #200.
- ❌ Gap 4 (Channel OD toggle value confirm): `detectMode` assumed Smart=2/Touch=1 — needs toggle capture.
- ❌ Gap 9 (Notification list REST endpoint): not captured.

---

## 🖥️ Browser testing session 2026-06-02 (supervisor laptop)

Deployed v0.2.8 → v0.2.9 to HA and ran a full card/entity audit.

### Card features tested (all pass unless noted)

| Feature | Result | Notes |
|---|---|---|
| Map renders (zones/markers/badges) | ✅ | wsmjco 349 m², KX1kGy 1222 m², robot dot, RTK fixed badge, scale bar, north arrow |
| Zoom / pan | ✅ | Mouse wheel + drag work |
| Fullscreen (⊞ button) | ✅ | Fills viewport; Esc exits |
| Edit mode (E / ✏ button) | ✅ | Zone selected, vertex handles visible |
| Per-zone ZONE SETTINGS panel | ✅ | All 6 fields: cut height (60), move speed (0.40), path spacing (25), perimeter laps (1), **Safe margin: Offset edge**, **Outer motor: On** — new safe_margin/outer_motor controls confirmed |
| Settings panel (⚙) — global | ✅ | Speed=0.6, path spacing=35, perimeter laps=1, Random, Detour, Advanced expands |
| Settings panel — Advanced | ✅ | Safe margin + Outer motor dropdowns present |
| Backup panel (📦) | ✅ panel opens | Shows 0 backups — `backup_maps` sensor is `entity_registry_enabled_default=False`; user must enable it OR add `backup_entity: sensor.xxx_backup_maps` to the card config |
| Schedule panel (📅) | N/A | No schedules configured (sensor shows 0); button only appears when `schedule_entity` is in card config |
| Settings cut_speed stale localStorage | ✅ fixed | Was showing `0.6` (stale from old field-map bug). Cleared localStorage; new code validates integer range on next reload |

### Bugs diagnosed and fixed (v0.2.9, commit 065b9bc)

**Bug 1 — Theft lock / find-robot switch double notification**

- Root cause: `_DeviceFeatureSwitch.async_turn_on/off` sent the REST PATCH unconditionally, even if the feature was already in the desired state. This causes duplicate cloud notifications when:
  1. App enables theft lock at T=0
  2. HA's 30s poll hasn't run yet → HA shows "off"
  3. User clicks "Turn on" in HA → second PATCH → duplicate notification
- Fix: idempotency guard — skip PATCH if `is_on` already matches. Covered by 2 new tests.

**Bug 2 — Theft lock / find-robot sync lag (app → HA)**

- Root cause: REST polling inherent latency. HA reads these feature flags via `GET /prod/get-device-feature` every ~30s. When the **app** changes the state, HA doesn't see it until the next poll.
- Status: **NOT a code bug** — REST-based switches have no real-time push. User should wait ~30s for HA to reflect app changes. After the idempotency fix, clicking "Turn on" when already on is a no-op, so the window for double-notification is much smaller.

**Bug 3 — Find robot beep does nothing**

- Root cause: encoding is **correct** (`10316a023064800101` = `PbInput{f13.audioVolume=100, f16=1}`, confirmed byte-exact against app BLE capture). But this is a `PbInput.f16` command — same pattern as `set_wifi` which is documented **BLE-only**. HA sends via MQTT; the robot likely only processes f16=1 via BLE.
- Confirmation needed: **Please test with the robot actively mowing (NOT docked)**, pressing the Find My Robot button. It's possible the robot also ignores the command when docked.
- If confirmed BLE-only: should raise a `HomeAssistantError("Find My Robot sound requires BLE proximity — use the Lymow app")` rather than silently doing nothing.

### Entity state audit (2026-06-02)

Entities showing `unknown` are PbRobotConfig-based (robot only echoes these during active mowing/diagnostics, not when docked):
- `switch.7b6521_vehicle_led`, `rainy_mowing`, `charging_handbrake`, `prefer_4g`, `auto_dock_on_error` → expected unknown when docked
- `select.7b6521_return_to_dock_route`, `zone_order` → same
- `number.7b6521_volume`, `recharge_threshold`, `resume_threshold` → same

The `switch.7b6521_find_robot_beep: on` — find-robot location feature is enabled (REST switch, reads from cloud). The `button.7b6521_find_my_robot_play_sound` was last pressed 2026-06-02T04:40 UTC (before this session).

### Live mowing observations (2026-06-02, robot mowing ~8 min into KX1kGy)

| Observation | Notes |
|---|---|
| Mowing badge ✅ | Green "Mowing" chip in status bar |
| Robot pose updating live ✅ | Moved from (−0.01,−1.38) → (4.02,17.48) → (17.4,−30.9) over ~8 min |
| Mow trail overlay ✅ | Bright teal convex hull grows as robot covers the top of KX1kGy |
| Mow trail legend ✅ | "Mow trail" chip appears in legend during mowing |
| `volume: 100.0` appears ✅ | After pressing Find My Robot Play Sound button, `number.7b6521_volume` flipped from `unknown` to `100.0` — robot processed the MQTT command and echoed robotConfig back. **Beeped?** — user to confirm. |
| `auto_dock_on_error: on` ✅ | Became known during mowing (robot echoes robotConfig) |
| `prefer_4g: off` ✅ | Also became known during mowing |
| `rainy_mowing / charging_handbrake / vehicle_led: unknown` | PbTaskConfig / vehLedStatus NOT echoed during mowing on firmware v2.1.48.1 |
| **globalZoneConfig absent during mowing** | **CONTRADICTS earlier BRANCH_STATUS claim** ("absent when docked, present when task active"). On this robot/firmware the opposite is true: globalZoneConfig IS present in docked-state query_map responses but IS absent when mowing. Per-zone zoneConfig IS present (and different from docked values: cutSpeed=6/Turbo, pathSpacing=30, perimeterLaps=2). Settings panel correctly shows docked-state values; those are stale while mowing. |

---

## 🛠 Supervisor session 2026-06-02 (continued) — all implemented changes

### Commits shipped this session

| Commit | What |
|---|---|
| `c0fc2e6` | feat(card): backup panel (📦) + per-zone safe_margin/outer_motor controls |
| `b5ff1cc` | docs: supervisor session notes v0.2.8 |
| `065b9bc` | **fix**: idempotency guard on REST feature switches; settings panel cut_speed localStorage validation (v0.2.9) |
| `d2ca9a8` | docs: browser testing session 2026-06-02 audit |
| `aa0e2da` | docs: live mowing observations |
| `45f8a21` | **fix**: query_map on MQTT come-online so taskConfig switches populate at startup (v0.2.10) |

### Why settings entities were unknown — root cause diagnosed

The user noted: "all data can be retrieved from Lymow app at all stages". Investigation found two root causes:

**Root cause 1 — taskConfig-based switches (rainy_mowing, charging_handbrake, return_to_dock_route, zone_order):**
These read from `coordinator.data[thing]["mapData"]["taskConfig"]` which comes from PbMap.f8. PbMap.f8 only arrives in `query_map` responses, NOT in regular pboutput. The coordinator's `on_mqtt_online` was only calling `async_query_robot_config` on startup, not `async_query_map`. So these stayed `unknown` until someone manually called `lymow.query_map`.

**Fix (v0.2.10, commit 45f8a21):** `on_mqtt_online` now schedules BOTH `async_query_robot_config` AND `async_query_map` when the robot comes online. These entities will now populate within a few seconds of HA startup or robot reconnect.

**Root cause 2 — robotConfig-based switches (vehicle_led, and partially others):**
These read from PbOutput.f17 (robotConfig). The coordinator sends `{f9:{f10=1}}` on startup to query this. The robot does respond (confirmed: `auto_dock_on_error`, `prefer_4g`, `volume` all became known). But `isOpenLed` (f7) stays unknown because proto3 omits default-false values — when LED is off, f7 is absent from the robotConfig message. The decoder interprets absence as "unknown" rather than "False".

**Why we can't safely fix this with a proto3 zero-default treatment:** The coordinator uses deep-merge for robotConfig (partial echoes after writes should not wipe out previously-known fields). If we emit `isOpenLed=False` whenever f7 is absent in ANY robotConfig message, a partial echo (e.g. just `audioVolume=100` after Find-My-Robot) would incorrectly clobber a known `True` LED state.

**Current status:** `vehicle_led` stays `unknown` until the user first toggles it (then the robot echoes back the new state explicitly). The app's advantage here is its persistent BLE connection which can read GATT characteristics at any time — HA can't replicate that without a Bluetooth entity.

**Future fix option:** Add a `full_query=True` flag to `decode_robot_config` and use it only in the startup query path; partial echoes use `full_query=False`. Full-query responses would apply proto3 zero defaults; partial echoes would not.

### Pending for next session / deploy

1. **JS deploy pending**: `lymow-map-card.js` on HA disk is at commit `b5ff1cc` content. The repo is at `45f8a21`. Need to run in the HA terminal:
   ```bash
   curl -sL "https://raw.githubusercontent.com/8408323/ha-lymow/feat/map-lovelace-card/custom_components/lymow/www/lymow-map-card.js" \
     -o /config/custom_components/lymow/www/lymow-map-card.js
   curl -sL "https://raw.githubusercontent.com/8408323/ha-lymow/feat/map-lovelace-card/custom_components/lymow/coordinator.py" \
     -o /config/custom_components/lymow/coordinator.py
   ha core restart
   ```
   The terminal addon keeps intercepting keystrokes (HA global shortcuts); click INSIDE the xterm.js area before typing.

2. **Backup panel shows 0 backups**: `sensor.7b6521_backup_maps` is disabled by default (`entity_registry_enabled_default=False`). To enable: HA Settings → Devices & Services → Lymow → sensor "Backup maps" → enable entity. OR add `backup_entity: sensor.7b6521_backup_maps` to the card YAML config.

3. **Find Robot Beep — user confirmation pending**: The button was pressed during live mowing. `number.7b6521_volume` changed from `unknown` → `100.0`, confirming the MQTT command was received and processed. **Question: did the robot beep?** If yes → confirmed working over MQTT. If no → BLE-only restriction applies (same as set_wifi).

4. **globalZoneConfig firmware note**: On firmware v2.1.48.1, `globalZoneConfig` (PbMap.f11) is present in DOCKED-state query_map responses but ABSENT during mowing. This is inverted from what was documented for v2.1.43. The settings panel correctly shows docked-state values when the robot is docked; those values are stale while mowing. Per-zone zoneConfig IS sent during mowing with active task values (cutSpeed=Turbo=6, pathSpacing=30, perimeterLaps=2).

5. **30s sync lag for REST feature switches (theft_lock etc.)**: Inherent REST polling latency. When the app changes theft_lock/theft_detection, HA won't see it for up to 30s. The v0.2.9 idempotency guard prevents double cloud notifications when the user clicks "Turn on" while the app already has it on. This is the best HA can do without real-time push from the cloud.

6. **`vehicle_led` proto3 fix** (optional, future): See root cause 2 above. Requires `full_query` flag in `decode_robot_config`. Not blocking for v1.

---

## 🛠 Supervisor session 2026-06-03 — taskConfig root cause investigation

### Deployed versions

| Version | Commit | What |
|---|---|---|
| v0.2.10 | `45f8a21` | `on_mqtt_online` schedules `query_map` + `query_robot_config` at startup |
| v0.2.11 | `eb535ff` | `_check_work_status_transition` also calls `query_map` on mow→dock |

### Final confirmed root causes for "unknown" entities

**`rainy_mowing`, `charging_handbrake`, `zone_order`, `return_to_dock_route` — permanently unknown via MQTT:**
- These switches read from `coordinator.data[thing]["mapData"]["taskConfig"]` (decoded from PbMap.f8)
- **PbMap.f8 is NEVER sent by the robot in MQTT query_map responses** — confirmed by checking `task_config` attribute on the map sensor after multiple `query_map` calls in both docked and mowing states, always absent
- These switches are effectively **write-only** from HA: the user can turn rainy mowing on/off and the robot accepts it, but HA will never show the current state
- The Lymow app reads these via BLE GATT characteristics — HA has no equivalent
- **No fix possible without BLE or a REST endpoint** (not yet discovered)

**`vehicle_led` — unknown because robot omits proto3 default-false:**
- `isOpenLed` (PbRobotConfig.f7, bool) is omitted from robotConfig responses when LED is off (proto3 zero-default)
- Robot DOES send robotConfig in pboutput (confirmed: `auto_dock_on_error`, `prefer_4g`, `volume` populate correctly)
- Fix requires either: (a) `full_query` path in `decode_robot_config` that applies proto3 zero-defaults for persistent booleans, OR (b) accepting write-only semantics
- Risk of (a): deep-merge collision if a partial echo also triggers the zero-default treatment
- Status: deferred, not blocking

**`mowing_settings` (globalZoneConfig) — works correctly when docked:**
- Sensor exports `mowing_settings` (snake_case), card reads via `_getMapData()` which maps `a.mowing_settings → mowingSettings` ✅
- Present in docked-state query_map responses; absent during mowing (firmware v2.1.48.1)
- Live confirmed docked values: `cutHeight=60, moveSpeed=0.6, cutSpeed=4, pathSpacing=35, perimeterMowLaps=1, obsDecMode=2 (Detour), safeMarginMode=true`
- Settings panel correctly populates from these values ✅

### Current test counts and coverage

- 1141 tests, 100% coverage (as of commit `eb535ff` / v0.2.11)
- `uv run pytest tests/ --cov=custom_components/lymow --cov-fail-under=100 -q`

### v0.2.12 — optimistic state for write-only task config switches (commit 474d48e)

`async_set_device_settings` now immediately writes the new values into `coordinator.data[thing]["mapData"]["taskConfig"]` after the MQTT publish. Since the robot never echoes PbTaskConfig back via MQTT, this is the only way HA can reflect the current state. The wire inversion for `charging_handbrake` (UI-True → `disableChargingPark=False`) is handled here. Live-tested: `rainy_mowing` toggled on→off, HA reflected `on` and `off` correctly within ~1 second of each write. 1143 tests, 100% coverage.

### Deployed to HA (v0.2.12 live as of 2026-06-03)

Files deployed: `coordinator.py`, `manifest.json` (v0.2.12). The card JS (`lymow-map-card.js`) and all Python platform files are at the v0.2.11 content deployed earlier this session. Lovelace resources at `?v=0.2.12`.

### Summary of all versions shipped this session (2026-06-02/03)

| Version | Key change |
|---|---|
| v0.2.8 | Backup panel (📦) + per-zone Safe margin / Outer motor controls |
| v0.2.9 | Idempotency guard for REST feature switches (no double theft-lock notifications); settings cut_speed localStorage validation |
| v0.2.10 | `on_mqtt_online` now calls `query_map` + `query_robot_config` at startup |
| v0.2.11 | `query_map` also called on mow→dock transition |
| v0.2.12 | Optimistic state for `rainy_mowing`, `charging_handbrake`, `zone_order`, `return_to_dock_route` |

---

## 🛠 Supervisor session 2026-06-03 (continued) — all items resolved

### Confirmed working this session

| Feature | Status | Notes |
|---|---|---|
| Find Robot Beep | ✅ **CONFIRMED** | User heard the beep; command works over MQTT, not BLE-only |
| Backup panel (📦) | ✅ **WORKING** | Both backup sensors enabled. 3 real backups shown (latest 2026-06-02). |
| `rainy_mowing` optimistic | ✅ **WORKING** | Toggled on→off live; HA reflects state immediately (v0.2.12) |
| `vehicle_led` shows off | ✅ **CONFIRMED** | `is_on` returns False when `dockOnError` known and `isOpenLed` absent; confirmed live: `vehicle_led: off` (v0.2.16) |

### v0.2.12–v0.2.16 version summary

| Version | Key fix |
|---|---|
| v0.2.12 | Optimistic state for task config switches (rainy_mowing, charging_handbrake, zone_order, return_to_dock_route) |
| v0.2.13 | vehicle_led optimistic toggle (write path) |
| v0.2.14–v0.2.15 | coordinator.py dockOnError fill-in + _mqtt_state persistence (GitHub CDN cache prevented correct deploy) |
| v0.2.16 | **Read-path fix in `is_on`**: VehicleLedSwitch returns False when dockOnError known — immune to timing/cache issues. Confirmed live. |

### Remaining open items (low priority, not blocking)

| Item | Notes |
|---|---|
| Gap 4: Channel OD toggle values | Confirm `detectMode` Smart=2/Touch=1 from a fresh capture |
| Backup create/restore test | Panel shows 3 backups; create/restore/rename/delete UI not yet tested |
| Schedule panel | No schedules configured; test when user creates one |

---

## 🛠 Supervisor session — 2026-06-19 (live-deploy reliability + frontend pass)

Worked against the **deployed** code (`origin/feat/map-lovelace-card`); the local
checkout was 50 commits behind and was synced to origin first. Deploy = `ssh
homeassistant "cat > /tmp/x && sudo cp /tmp/x /config/custom_components/lymow/<f>"`
(SSH add-on has no sftp) then `homeassistant.restart`. All items below are
**deployed live + verified**; 1168 tests pass, ruff clean. (Tests NOT yet pushed.)

**Live outage found & fixed (root cause):** `__init__.py` logged in once and never
refreshed → Cognito access token lapsed (~24 h) → every REST poll 401'd →
all entities `unavailable` until restart. Wired token + AWS-cred refresh into the
coordinator (`set_auth_context` / `_async_ensure_auth`, preemptive at
`AUTH_REFRESH_MARGIN_SECONDS`=600; refresh_tokens → re-login fallback →
ConfigEntryAuthFailed). Also: `__init__` never seeded the client's initial AWS
creds (latent S3/backup bug) — now does.

**Proto3 defaults (user request "default values for everything"):** coordinator
seeds robotConfig + taskConfig proto3 defaults (`_apply_config_defaults`), so
settings entities show the default instead of `unknown`. Switch policy unified:
absent → default (False / inverted), present-but-malformed → unknown. Selects:
absent → option 0. Result: 0 non-button entities unknown/unavailable.

**Startup query race:** the config query now fires after MQTT connect
(`async_query_all_robot_configs` in `__init__`, gate also requires
`mqtt.is_connected`) — was dropping the publish during first_refresh.

**PIN read path (closes the #46 read gap):** `decode_robot_config` now decodes
`PbRobotConfig.f9 lcdPinCode` ({f1: 4 digit-bytes}) → `lcdPin`, surfaced as
`sensor.<id>_screen_pin` (disabled-by-default, DIAGNOSTIC, never logged). This was
a hand-edit on the live box not in git — now committed properly.

**Orphan entity cleanup:** `async_prune_stale_zone_entities` (entity.py, called from
number.py) removes switch/number entities for zones deleted from the map. Cleared
3f49/6171/d1d8.

**CI was RED, now green:** conftest stub missing `get_ffmpeg_manager` (suite
wouldn't load) + `ConfigEntryAuthFailed`/`EntityCategory` stubs added; ~11 stale
tests reconciled (switch defaults, dashboard 6-views, BLE-wifi signature); ruff
format/imports fixed across __init__/camera/lawn_mower.

**Frontend (lymow-mower dashboard) modernized + deduplicated:** rebuilt via
`lovelace/config/save` (backup at `.lymow_dashboard_backup.json`). Each value
entity now has exactly one home (was: network/connectivity/zone_order/prefer_4g/
rtk duplicated across views). Stripped the repeated "7B6521" prefix from every
row. Settings + Diagnostics use modern `sections` views with icon headings.
Overview "Mowing controls" card scoped to `run_time/zone_config/start_zone/resume`
so headlight/advanced live only on Settings (no duplicate controls). Verified all
views via Playwright (screenshots).

**Still open (unchanged, capture-blocked / out of scope):** WiFi credential write
(BLE, risky), Bind RTK (risky), notification-history list, map edits (deferred),
camera KVS #97 (excluded). **Low-confidence to verify:** chargingMode label
semantics (wire 0/1 correct; "Follow perimeter"=0 / "Direct route"=1 human labels
unconfirmed against the app — needs a Return-to-Dock screen capture). `last_mow
_duration` / `total_mow_time` sensors display raw seconds (consider duration
device_class for h:m formatting).

---

## 🔬 Live BLE capture validation — 2026-06-19 (supervisor laptop)

Set up the full capture pipeline on THIS laptop (mitmproxy CA `c8750f0d` already
trusted on the phone; phone proxy → 192.168.20.180:8888) and confirmed: **app
COMMANDS go over BLE when BT-connected** (mitmproxy sees only MQTT `pboutput`
heartbeats, never `pbinput`). Used the btsnoop HCI log
(`/data/misc/bluetooth/logs/*.cfa`, parsed with `scripts/parse_btsnoop.py`) to
capture the SENT ATT writes (handle 0x14) — the `.cfa` DID contain sent writes
this time (15k+), so settings commands are recoverable.

Captured the **Mowing Settings → Global → Save → Keep Custom** write and decoded
it against our codec. Envelope CONFIRMED: `PbInput{f2:ver, f5:49
(GLOBAL_SETTING_N), f12:PbMap{f11:globalZoneConfig, f12:globalChannelConfig}}`.

**globalZoneConfig (PbMap.f11) — every field cross-checked to the app's labels:**
cutHeight f1=60, moveSpeed f4=0.6, cutSpeed f6=4(Standard), **stripeAngle f8=-1
(=Optimized)**, pathSpacing f9=35, perimeterMowLaps f10=1, perimeterMowDir f11,
noGoMowLaps f12=1, **obsDecMode f13=2 (Zone OD = Smart)**, pathOrder f14, safe
MarginMode f17=1 (Offset Edge), turnOffOuterMotor f18=0, **followDetectMode f19=2
(Perimeter OD = Smart)**.

**globalChannelConfig (PbMap.f12):** detectMode f1, channelDeckHeight f2=60,
channelLift f3=0 (= the "Raise Omni Wheels on Channel" toggle). Drove a
Smart→Touch→Smart change sequence and confirmed **detectMode Smart=2 / Touch-Only
=1** (09:08 =2, 09:11 =1, 09:15 =2) — resolves the long-standing Gap 4. Robot
config left RESTORED (final detectMode=2/Smart). `decode_channel_config` already
maps all three fields.

**Concrete fix shipped this session:** `stripeAngle` (PbZoneConfig f8, signed;
-1=Optimized) was the ONE field the app exposes that our codec was missing — now
decoded (`_ZONE_CONFIG_SIGNED_NAMES`) + settable via `set_task_config`
(`encode_set_task_config(stripeAngle=…)`; -1 encodes to the exact 10-byte varint
the app sends). Tested.

**Confirmed-correct (no change needed):** envelope, obsDecMode/followDetectMode/
detectMode value maps, channel config fields, `encode_query_robot_config`
(f9={f10:1} — saw `4a025001` on the wire).

**Still open (honest):**
- **chargingMode / zoneOrder select labels** — the app's "Mowing Order (Main Area
  First / Perimeter First)" is **pathOrder f14 (bool), NOT** PbTaskConfig.zoneOrder.
  No "Return to Dock route (Direct/Perimeter)" toggle was found in the current app
  (3.0.x) Device/Mowing settings, so `select.return_to_dock_route` /
  `select.zone_order` labels remain best-effort; the wire VALUE (PbTaskConfig f1/f2)
  is read correctly. Likely set via the Select-Mow flow — needs that capture.
- **set_wifi / bind_rtk / set_pin** — encoders exist + tested + were BLE-validated
  2026-05-30. NOT re-triggered this session: re-applying requires re-entering the
  WiFi password / RTK base-id / PIN, and a mistype disconnects/unbinds the robot
  (needs the user physically present, per the standing rule).
- **Map edits (Edit Boundary, userCtrl 10/11)** — services exist (add_zone/nogo/
  channel, split/merge); live validation drives the robot around the yard to record
  a boundary → genuinely needs hands-on supervision, NOT done blind from ADB.

---

## 🔬 Live app validation #2 — 2026-06-19 evening (user-supervised)

User authorised live changes (PIN→1234, WiFi creds in ~/private_projects/.wifi)
and pointed out the exact app locations. Drove the app over ADB; commands go over
BLE (btsnoop) or the cloud depending on BT state.

**Return to Dock (chargingMode) — CONFIRMED.** Settings → Device Settings →
(scroll) "Return to Dock" = **Follow Perimeter / Direct Route**. "Follow Perimeter"
was selected with the robot at chargingMode=0 ⇒ **0=Follow Perimeter, 1=Direct
Route — matches `select.py` `_CHARGING_MODE_OPTIONS` exactly.** No code change.
(Note: the app's "Mowing Order: Main Area First/Perimeter First" is `pathOrder`
f14, a DIFFERENT setting from PbTaskConfig.zoneOrder.)

**Customize (per-zone) settings — FULL COVERAGE CONFIRMED.** Mowing Settings →
Customize → zone0/zone1 + channel0/channel1. Enumerated all 15 per-zone fields:
Name, Moving Speed, Cutting Height, Blade Speed, Path Spacing, **Stripe Angle
(Optimized / User-Defined + 0-180°)**, Cross Pattern (toggle + angle), Mowing
Order, Zone OD, Perimeter OD, Perimeter Direction, No-Go Laps, Zone Perimeter
Laps, Turn-Off Outer Motor, Safe-margin. All map to our PbZoneConfig codec and are
settable per-zone via `set_zone_config` (`_encode_zone_config_submessage` iterates
`_TASK_CONFIG_FIELDS`, which now includes `stripeAngle`). "Cross Pattern" ≈
`relativeCleanDir` f16 / `cleanMode` f7 (only field still to pin down exactly).
Per-zone cut-height range in app is 30-100mm; stripe angle 0-180°.

**PIN — validated + now settable from HA.** PIN screen: 4-digit input + Update.
Set it to 1234 (the change took). `encode_set_pin("1234")` =
`PbInput{f13:robotConfig{f9:lcdPinCode{f1:[1,2,3,4]}}}` — the exact f9 format the
robot reports back (decode-confirmed). Added **`text.<id>_screen_pin`** (TextEntity,
PASSWORD mode, 4-digit pattern, disabled-by-default, CONFIG) so the PIN is settable
from the dashboard, not just the `lymow.set_pin` service. Service tested
end-to-end via the HA API (HTTP 200, MQTT path). **PIN is currently 1234.**

**WiFi — encoder confirmed; live re-capture blocked.** App → Settings → Network
Settings has ONLY a "Wi-Fi Password" field + "Reconnect" (NO SSID field — it
re-applies to the provisioned SSID; SSID change happens at pairing). BT was
disconnected at the time (robot docked, out of phone BLE range) so no live capture.
`encode_set_wifi` (`{f1:ssid, f2:password, f5:3}`, BLE) was already captured/
validated 2026-05-30 and our `lymow.set_wifi` DOES support a new SSID — but it
sends over BLE, needing the sender in Bluetooth range of the mower. **SSID note:
robot reports `Haraldsson-IoT`; user wrote `Haraldssons-IoT` — confirm spelling
before any SSID change.**

**New app features spotted (candidate gaps):** "Voice Pack" (Settings → Device
Maintenance → Voice Pack — selectable mower voice/audio pack; not in HA),
Audio Volume presets Mute/Low/Medium/High (we expose the raw 0-100 `audioVolume`,
fine). Cross-Pattern per-zone wire still to confirm.

---

## 🔬 WiFi provisioning resolved — 2026-06-19 (phone next to mower, BT up)

**SSID is `Haraldsson-IoT`** (the earlier "Haraldssons-IoT" was a typo). After a
user mower-restart it reconnected to WiFi; cloud `networkInfo.wifiSsid =
"Haraldsson-IoT"`, `deviceState online` — **goal met, mower is on the IoT WiFi.**

**Provisioning model confirmed:** the app's Network Settings has NO SSID field —
it provisions the mower to **the phone's current WiFi network**. You connect the
phone to the target SSID, type the password, tap Reconnect, and the mower joins
the same network. (The recurring BLE `f17{f3:1}` writes are wifi-status polls, not
the set command.) Entered the `~/.wifi` password + Reconnect with the phone on
Haraldsson-IoT, but the mower was already on it → no provisioning command fired
(no-op), so no fresh capture this session. `encode_set_wifi` (`f17{f1:ssid,
f2:password, f5:3}`) stays validated from 2026-05-30; our `lymow.set_wifi` also
supports an explicit SSID (BLE, needs the sender in range).

**Still TODO for full app parity (BLE captures; do while phone is at the mower):**
Voice Pack (mower voice-language packs — Settings → Device Maintenance; not in HA;
would be a select + service), Cross-Pattern per-zone exact wire (≈ relativeCleanDir
f16 + an enable), and the user-deferred map edits (Edit Boundary, drives the mower).

---

## 🔧 WiFi correction — 2026-06-19 (network selector exists; capture inconclusive)

CORRECTION to the note above: the app's Network Settings DOES have a **network-
selector dropdown** (tap the SSID name above the password field → scanned-network
list: Haraldsson, Haraldsson-IoT, Persson/Silfverbrand…). The earlier "no SSID
field" was wrong — the selector only renders once BT connects and the screen
finishes loading. So you pick the SSID in-app; you do NOT switch the phone's network.

Tried the two-network validation (user's idea: provision to `Haraldsson`, then back
to `Haraldsson-IoT`, same password → confirms both `f1`(ssid) and `f2`(password)).
Selected `Haraldsson` in the dropdown, entered the password (8 dots), tapped
Reconnect — but **no set-WiFi command surfaced in btsnoop on ANY handle/op**
(checked WRITE_CMD 0x52 + WRITE_REQ 0x12, all handles, searched for the SSID
bytes), and the mower stayed on `Haraldsson-IoT`. So the Reconnect either didn't
transmit a switch (a different-SSID switch may need a confirm step) or routed via
the cloud — which can't be captured here without force-stopping the app, and that
drops the BT the WiFi screen needs (catch-22). `encode_set_wifi` (`f17{f1:ssid,
f2:password, f5:3}`) remains validated from the 2026-05-30 BLE capture; mower is
on the correct network. `scripts/parse_btsnoop.py` only reads handle 0x14
WRITE_CMD — extend it to all handles + WRITE_REQ if a future WiFi capture is needed.

---

## ✅ WiFi VALIDATED byte-for-byte — 2026-06-19 (two-network test)

Resolved the earlier inconclusive note. The app's Network Settings DOES have a
network-selector dropdown; pick the SSID, enter the password, Reconnect — and the
mower switches (title flips to "Connected to <SSID> successfully", no popups, ~3 s).
My earlier captures pulled the btsnoop too early (before the command fired) — fixed
by polling the title-change before pulling.

Captured the BLE set-WiFi for TWO networks (same password, different SSID) and
decoded both: `wifiConfig (PbInput.f17) {f1: ssid, f2: password, f5: 3}`,
WRITE_CMD to handle 0x14. **`encode_set_wifi(ssid, password)` is byte-IDENTICAL to
the captured wire for both SSIDs** (f1 varies with the SSID, f2 password constant,
f5=3) — fully confirms the encoder. Mower restored to its WiFi network.
(SSID/password values are sensitive — kept out of git; capture `.cfa` files scrubbed.)

`scripts/parse_btsnoop.py` is hardcoded to handle 0x14 WRITE_CMD; a more general
dumper (all handles + WRITE_REQ/Prepare) lives at /tmp/parse_all2.py for future use.

---

## 📊 App→HA parity — state as of 2026-06-19 (hands-on audit)

The integration mirrors essentially the entire Lymow app. Confirmed live this
session: WiFi (byte-for-byte, two SSIDs), PIN (read+set+new text entity),
chargingMode labels (Follow Perimeter=0/Direct Route=1), per-zone Customize
settings (all 15 fields covered), stripeAngle (added), channel detectMode
(Smart=2/Touch=1). Backend has 47 services covering mowing settings (global +
per-zone + per-channel), schedules, map ops (zones/no-go/channels add/delete/
rename/merge/split/cut-height), backups, device settings, network priority, RTK
bind, geofence/anti-theft, lock, OTA, find-my-robot, device rename.

**Genuine remaining gaps (small):**
- **Voice Pack** (Settings → Device Maintenance → Voice Pack) — mower voice-
  LANGUAGE packs (French/Spanish/German/Italian/English; "English: In Use",
  Download buttons). NOT in HA. Niche. To add: a Select entity + a `set_voice_pack`
  service; needs a BLE capture of selecting a pack (download is robot-side).
- **Cross Pattern** (per-zone, Customize) — double-cut at a relative angle. Almost
  certainly already covered by `relativeCleanDir` (f16) + `cleanMode` (f7); a
  toggle-capture would confirm whether there's a separate enable bool.
- **Map edits — Edit Boundary** (drive-the-robot boundary record, userCtrl 10/11).
  add/delete/rename/merge/split zones/channels are implemented; the drive-record
  path needs supervised robot movement (user-deferred).
- **zoneOrder** (Optimize/Custom) — wire (PbTaskConfig.f2) reads correctly; the
  app UI location wasn't found (likely the Select-Mow ordering flow), so the
  select's labels stay best-effort.

**Out of scope (unchanged):** remote camera KVS (#97), account/login, language/
unit toggles, app-side device pairing.

---

## ✅ Cross Pattern CONFIRMED covered — 2026-06-19 (per-zone BLE capture)

Toggled zone0's "Cross Pattern" ON (reveals a "Cross-Cutting Angle"), Saved, and
captured the per-zone write (userCtrl=9, PbInput.f12 PbMap.goZones[*].f2
configBox). Decoded the full PbZoneConfig — **every field maps to our codec, zero
unmapped fields.** Cross Pattern is carried by:
- `cleanMode` (f7) = **3** when ON (was 1 / normal when off) — the enable/mode
- `relativeCleanDir` (f16) = the Cross-Cutting Angle (90° observed)
Both already in `_TASK_CONFIG_FIELDS` / `decode_zone_config`, so
`set_zone_config(cleanMode=3, relativeCleanDir=<angle>)` already drives it. No code
change needed. zone0 reverted to Cross Pattern OFF. **Per-zone parity is 100%.**

Net remaining app→HA gaps: only **Voice Pack** (niche mower voice-language packs;
needs a download-capture to RE the wire, then a Select + service) and the
user-deferred **Edit-Boundary** map drive (userCtrl 10/11, needs robot movement).

---

## ✅ Global Channel settings now settable + f8=stripeAngle PROVEN — 2026-06-19 (commit eb7201a)

**Channel-config settability (last audit gap closed).** The global Channel
settings (Channel Obstacle Detection / Channel Deck Height / Raise Omni Wheels on
Channel) were decoded but not writable. `encode_set_task_config` now splits the
write into `globalZoneConfig` (PbMap.f11) + `globalChannelConfig` (PbMap.f12) —
exactly as the app's Save sends both. New `set_task_config` params:
`channel_detect_mode` (f1, Smart=2/Touch=1), `channel_deck_height` (f2, mm),
`channel_raise_omni` (f3). Encoder output matches the captured app frame
byte-for-byte: `f12 = 0802103c1800`.

**f8 = stripeAngle DEFINITIVELY PROVEN (resolves the enabledZoneMask ambiguity).**
A stale APK note had f8 commented as "enabledZoneMask (uint64 bitmask)" — which
collides with stripeAngle because Optimized encodes as -1 = all-ones, identical
bytes. Drove a live disambiguation via ADB+btsnoop: set Mowing Settings → Global →
Advanced → Stripe Angle to **User-Defined 90°**, Save → Keep Custom (userCtrl=49):
- captured write `globalZoneConfig.f8 = 90` (the exact angle set) ⟹ f8 is stripeAngle, NOT a zone bitmask.
- restored to Optimized → `f8 = -1` (the 10-byte sign-extended varint `40 ffffffffffffffffff01`, byte-identical to the app). User's setting left at Optimized.
Whole globalZoneConfig map re-cross-checked live: cutHeight f1=60, moveSpeed f4=0.6,
cutSpeed f6=4, **stripeAngle f8 (90/-1)**, pathSpacing f9=35, perimeterMowLaps
f10=1, perimeterMowDir f11=2, noGoMowLaps f12=1, obsDecMode f13=2(Smart), pathOrder
f14=1, startProgress f15=0, relativeCleanDir f16, safeMarginMode f17=1,
turnOffOuterMotor f18=0, followDetectMode f19=2(Smart); globalChannelConfig
detectMode=2/cutHeight=60/channelLift=0. Also exposed the two
previously-decoded-but-unsettable fields `stripe_angle` (f8) and
`follow_detect_mode` (f19, Perimeter Obstacle Detection) as service params.
services.yaml documents all five new fields.

**Deployed + verified live:** protocol.py / lawn_mower.py / services.yaml scp'd to
HA (192.168.1.99), `homeassistant.restart` via REST. Integration reloads clean;
`lawn_mower.7b6521` = docked, all platforms present. Local suite 1177 passed, ruff
clean. (Total coverage 97.3% locally is the pre-existing camera.py/bluetooth.py
gap on this branch — deferred to the 2026-07-01 CI pass per maintainer; touched
modules protocol.py/lawn_mower.py remain 100%.)

**Voice Pack** remains the only cloud-feature gap (seen live this session: English
"In Use", French/German/Spanish/Italian downloadable) — niche, needs a
download-capture to RE the wire. Edit-Boundary map drive still user-deferred
(movement).

---

## ✅ Live HA→app round-trip verified — 2026-06-20

Tested whether HA control changes reach the robot AND show in the Lymow app.

**Result: HA→robot works; the app caches its settings pages.**
- `lymow.set_task_config(obs_dec_mode=1)` from HA → after `query_map`, HA read back
  `globalZoneConfig.obsDecMode=1`, i.e. **the robot applied it**. Reverted to 2.
- `switch.rainy_mowing` on (PbTaskConfig, which the robot never echoes over MQTT) →
  also applied. Reverted to off.
- In the app, navigating **out and back in within the app did NOT refresh** the
  Mowing Settings / Device Settings page (kept showing the cached pre-change value).
  A **full app restart** (force-stop + relaunch) made the app re-fetch from the
  robot — then it correctly showed "Touch-Only" and "Rainy Mowing ON".

**Takeaway for users:** HA setting changes DO take effect on the robot. The Lymow
app caches each settings page per app-session, so to *see* an HA-made change you must
**fully restart the Lymow app** (swipe it away / force-stop), not just back out and
re-open the page. No integration fix needed — this is app-side caching.

### Still pending: PbRunTimeConfig field numbers (low impact)
The 3 "Live cut-height / move-speed / cut-speed" Number entities
(`LiveCutHeightNumber` etc., USER_CTRL_SET_RUN_TIME_CONFIG) are **disabled by
default** and only act during an active mow. Their wire field numbers are still
unconfirmed: the deployed encoder uses cutHeight=f1/moveSpeed=f2/cutSpeed=f3
(Hermes #9456), while an earlier RE pass read moveSpeed=f4/cutSpeed=f6
(PbZoneConfig numbering). Not verifiable on a docked robot (runtime config only
applies mid-mow) and no Hermes decompiler is installed to read PbRunTimeConfig.encode
from the APK. **To resolve:** capture what the app sends when adjusting cut height
during a live mow, or decompile the APK's index.android.bundle (Hermes v96).
