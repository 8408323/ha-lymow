<!-- ⚠️  REMOVE THIS FILE BEFORE MERGING THE PR — it is a dev scratch document, not product docs. -->

# Branch: feat/map-lovelace-card — Status & Merge Checklist

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
- [ ] **Zone name discrepancy**: Robot protobuf `BasicInfo.f2` (name field) is empty — the robot stores only hashIds. Zone names "Front garden" / "Back garden HA" that HA shows must have been set via HA's rename feature. The Lymow app shows different names ("Front", "Back garden") — unclear if those come from the app's own server-side store, S3 map backups, or are just older robot-stored names. Needs investigation to confirm names round-trip correctly and HA/app stay in sync.
- [ ] **Zone vertex move capture**: Capturing what the Lymow app sends when a zone vertex is dragged and saved. This is the one protocol action not yet confirmed from live traffic. Requires mitmproxy working against the phone (cert install + proxy). You volunteered to do this action in the app while a capture runs.
- [ ] **Tests passing at 100% coverage**: Run `uv run pytest tests/ -v --cov` and confirm no failures and `--cov-fail-under=100` passes.
- [ ] **Mitmproxy cert install persistent on phone**: The tmpfs approach used in this session does not survive reboots. The Magisk module created at `/data/adb/modules/mitmproxy_ca/` should survive — verify after next reboot.

### Nice-to-have / post-merge
- [ ] Zone name source investigation — if the Lymow app has a server-side zone name store (separate from robot protobuf), we may want to fetch+merge those names in the coordinator.
- [ ] Zone move confirmation — once captured, verify `encode_sync_map` (userCtrl=25) is indeed the command the app uses for vertex moves, or if there's a dedicated command.
- [ ] PR review cycle: resolve all Copilot/Codex comments, re-request review, iterate until clean.

---

## Capture setup (for next session)

mitmproxy cert is now installed as a Magisk module at `/data/adb/modules/mitmproxy_ca/` — should persist across reboots.

mitmdump runs on **Windows** (not WSL2), port **8888**:
```powershell
cd C:\
mitmdump -s C:\temp\capture.py --listen-host 0.0.0.0 --listen-port 8888 --ssl-insecure
```
Capture output: `C:\temp\capture-lymow.txt` (written by the script itself via `_write()`)
Phone proxy: `192.168.1.147:8888`

Port 8888 on Windows was previously squatted by a portproxy rule. If it fails with EADDRINUSE, check:
```powershell
netstat -ano | Select-String 8888
# Kill the offending PID, or delete the portproxy rule (needs Admin):
netsh interface portproxy delete v4tov4 listenaddress=192.168.1.147 listenport=8888
```
