<!-- ⚠️  REMOVE THIS FILE BEFORE MERGING THE PR — it is a dev scratch document, not product docs. -->

# Branch: feat/map-lovelace-card — Supervisor Document

**This document is the interface between two sessions:**
- **Supervisor session** (this laptop, WSL2): has full codebase access, plans work, writes code
- **Capture session** (other laptop): has mitmproxy running, can control the Lymow Android app, reports raw traffic back here

Update this file when findings come in. Strike tasks when done.

---

## What has been done (69 commits vs main)

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
- **Mowing settings panel**: Cut height, move speed, path spacing, clean direction, perimeter laps/dir, clean mode, path order, line follow mode; collapsible advanced section
- **Schedules panel**: View/edit mowing schedules
- **Keyboard shortcuts**: F=fullscreen, E=enter edit, R=reset view, Esc=cancel/close; shadow DOM focus guard so typing in rename doesn't trigger shortcuts
- **Zone labels**: Name + area two-line adaptive font size; go/nogo/channel label mode persisted in localStorage; bright green go-zones (#43a047) with white text
- **Status bar**: Pin-and-go, obstacle avoidance toggle, zone enable/disable feedback
- **UI polish**: RTK status badge, auto-pause on RTK loss, channel legend, legend SVG symbols, viewport-fixed overlays, fullscreen toggle (⊞ or F), auto-register card + create Lymow dashboard on setup, JS mtime cache-buster
- **Bug fixes**: Page scroll preserved across re-renders, details state preserved, taskConfig round-trip fixed, post-layout RAF re-render, zero-length edge guard, label placement

### Integration backend (`custom_components/lymow/`)
- **New services**: `rename_zone`, `delete_zone`, `delete_nogo_zone`, `sync_map`, `move_charging_station`, `set_device_settings`, `set_recharge_resume`, `set_network_priority`, `set_run_time_config`, `resume`, `query_map` — all documented in `services.yaml`
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

### Must-have
- [ ] **Zone vertex move capture** — see "Capture session tasks" below
- [ ] **Zone name round-trip confirmed** — see "Capture session tasks" below
- [ ] **Tests at 100% coverage**: `uv run pytest tests/ -v --cov --cov-fail-under=100` must pass with zero failures

### Nice-to-have / post-merge
- [ ] Zone name server-side store — if the Lymow app fetches zone names from S3 map backups or an app-side REST endpoint (not the robot), consider fetching + merging in coordinator
- [ ] PR review cycle: resolve all Copilot/Codex comments, re-request review, iterate until clean

---

## Capture session tasks

**The capture session is on another laptop with mitmproxy + Android phone access.**
Complete these tasks in order and paste findings into the "Findings" sections below.

### Capture setup

mitmproxy CA cert is installed as a **Magisk module** at `/data/adb/modules/mitmproxy_ca/` on the phone — should survive reboots. Phone WiFi ADB at `192.168.1.45:5555`. Windows IP is `192.168.1.147`.

Start mitmdump **on Windows** (not WSL2), port 8888:
```powershell
mitmdump -s C:\temp\capture.py --listen-host 0.0.0.0 --listen-port 8888 --ssl-insecure
```
Output goes to `C:\temp\capture-lymow.txt` (the script writes it there, NOT via stdout redirect).

Phone proxy: `192.168.1.147:8888`

If port 8888 is already in use on Windows:
```powershell
netstat -ano | Select-String 8888
# Kill offending PID, or:
netsh interface portproxy delete v4tov4 listenaddress=192.168.1.147 listenport=8888
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

---

### Task A — Zone vertex move (HIGHEST PRIORITY)

**What to do in the app:**
1. Open Lymow app → Map view
2. Enter zone edit mode (tap a zone → edit)
3. Drag a single vertex of any go-zone to a slightly different position
4. Tap Save / confirm
5. Wait ~3 seconds for the app to send the update

**What to look for in capture output (`C:\temp\capture-lymow.txt`):**
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
- `encode_rename_zone(hash_id, name)` — sends CLEAR_ZONE (userCtrl=8) with name in BasicInfo.f2
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

## Findings (fill in as capture session reports)

### Task A findings
_Pending capture session_

### Task B findings
_Pending capture session_

### Task C findings
_Pending capture session_

---

## Next steps for supervisor session (after findings arrive)

1. Compare captured bytes for Tasks A/B/C against existing encode functions
2. If protocol matches: add tests in `tests/test_protocol.py` using real captured bytes
3. If protocol diverges: implement corrected encode function; update coordinator + services + map card
4. Run full test suite: `uv run pytest tests/ -v --cov --cov-fail-under=100`
5. If zone names come from a REST endpoint (Task B): implement fetch + merge in coordinator's `_async_update_map_data`
6. Remove this file, push final commits, open PR
