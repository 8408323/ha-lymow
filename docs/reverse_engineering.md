# Reverse Engineering — Traffic Capture Guide

Three methods for capturing Lymow Android app traffic, ordered by ease of setup.
All three produce the same data; choose based on what's convenient.

---

## Method 1 — MQTT CLI (no phone required)

The integration connects directly to AWS IoT MQTT. Use the debug scripts to
send commands and dump raw protobuf responses without touching the phone at all.

```bash
# From the repo root:

# Query current robot state + try all map query variants
uv run python scripts/query_map.py

# Full debug dump: IoT REST shadow + MQTT connect + userCtrl=19
uv run python scripts/debug_mqtt.py

# 60-min focused capture — logs unknown sub-fields alongside workStatus/isCharging context
# (requires robot to be online; writes tools/mow_focus_<timestamp>.jsonl)
uv run python tools/capture_focus.py [seconds]

# Raw capture — logs every pboutput message as decoded JSON
uv run python tools/capture.py [seconds]
```

**What you get:** raw protobuf hex + decoded field tree for every message the
robot sends. The scripts save the largest response to `scripts/map_response.bin`.

**Limitation:** The robot only sends map/zone data in response to MQTT commands
while actively mowing, or when the app opens the map screen. If the robot is
docked and idle, `userCtrl=19` triggers only a state update, not zone polygons.

---

## Method 2 — ADB + logcat (USB, no proxy cert needed)

Captures Android app logs and optionally raw network traffic via tcpdump.
Does not require installing a proxy cert — works on stock/non-rooted phones.

### One-time setup

**On Windows (run as Administrator in PowerShell):**
```powershell
# Install usbipd-win if not already present:
#   winget install --interactive --exact dorssel.usbipd-win
#   (or download from https://github.com/dorssel/usbipd-win/releases)

# Find the phone's bus ID (look for OnePlus / Samsung / etc.)
usbipd list

# Bind and attach to WSL (replace 1-3 with your bus ID)
usbipd bind --busid 1-3
usbipd attach --wsl --busid 1-3
```

**In WSL (once per session after attaching):**
```bash
adb devices          # should show the phone serial
```

If `adb` is not in PATH, add the Android SDK platform-tools directory to PATH first.

If the phone shows "unauthorized" — tap "Allow" on the phone's USB debugging prompt.

### Environment details
- usbipd-win: installed at `C:\Program Files\usbipd-win\`
- Phone (OnePlus): USB bus ID **1-3** (confirm with `usbipd list` each session — bus IDs can change)
- Phone WiFi IP: **192.168.1.45** (may vary — check router if stale)

### Run the capture script

```bash
# From the repo root:
bash scripts/adb_capture.sh
```

Then open the Lymow app on the phone and navigate to the map screen. Press Ctrl-C when done.

**What it captures:**
- `tools/logcat-lymow.txt` — filtered Android logs (mqtt, map, zone keywords)
- `tools/capture-adb.pcap` — raw TCP (if tcpdump is on the device)

### Controlling the app via UIAutomator (tap automation)

```bash
# Find the Lymow app package name
adb shell pm list packages | grep -i lymow

# Launch the app
adb shell monkey -p <package.name> -c android.intent.category.LAUNCHER 1

# Dump current UI hierarchy (find button coordinates)
adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml /tmp/ui.xml
grep -i "map\|zone\|area" /tmp/ui.xml

# Tap a screen coordinate (x y)
adb shell input tap 540 1200
```

### tcpdump on device (captures raw MQTT WebSocket bytes)

If the device has `tcpdump` (rooted, or install via Magisk):
```bash
IOT_HOST="a3j5zqqo5iuph9-ats.iot.eu-west-1.amazonaws.com"

# Start capture
adb shell "tcpdump -i any -w /sdcard/lymow.pcap host $IOT_HOST" &

# ... use the app ...

# Pull and inspect
adb pull /sdcard/lymow.pcap tools/capture-adb.pcap
tshark -r tools/capture-adb.pcap -Y "tcp.port==443" -x | head -100
```

---

## Method 3 — mitmproxy (HTTPS interception)

Intercepts all HTTPS traffic from the app including REST API calls and the
MQTT WebSocket upgrade. Requires installing the mitmproxy CA cert on the phone.

### Environment details
- Proxy host (this machine, WSL2): **192.168.1.147:8888**
- Phone WiFi IP: **192.168.1.45**
- Phone proxy setting: Manual, host `192.168.1.147`, port `8888`
- CA cert: install via Settings → Security → Install certificate → CA certificate
  (download from `http://mitm.it` while proxy is active)

### Run the proxy

```bash
# From the repo root:
mitmdump -s tools/capture.py --listen-host 0.0.0.0 --listen-port 8888 --ssl-insecure
```

Output is written to `tools/capture-lymow.txt` (gitignored) and printed live.

### Phone setup (one-time)

1. Connect phone to same WiFi as this machine
2. WiFi settings → long-press network → Modify → Advanced → Proxy → Manual
   - Hostname: `192.168.1.147`
   - Port: `8888`
3. Open `http://mitm.it` in the phone browser → download Android cert
4. Settings → Security → Install certificate → CA certificate → select downloaded file
5. Trust the cert (you may need to set a screen lock first)

### What you get

`tools/capture-lymow.txt` — human-readable log of every Lymow/AWS request:
- Full REST request/response bodies (JSON, pretty-printed)
- Protobuf envelope decoded: shows hex of raw bytes and key name
- MQTT WebSocket upgrade visible (but payload is encrypted at TLS layer, not decoded)

**Previous captures:**
- `capture-pre-cert.txt` — TLS failures before cert install (not useful)
- `capture-full.txt` — working capture (467 lines, confirmed REST API calls)

---

## Decoding protobuf responses

All MQTT messages use a JSON envelope wrapping base64-encoded protobuf:
```json
{"message": "<base64-protobuf>"}
```

Use the CLI decoder:
```bash
uv run python -c "
import base64, sys, importlib.util

def _load(n, p):
    s = importlib.util.spec_from_file_location(n, p)
    m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m)

_load('lymow.const', 'custom_components/lymow/const.py')
_load('lymow.protocol', 'custom_components/lymow/protocol.py')

from lymow.protocol import decode_pboutput, _decode_fields
import json, struct

hex_str = '<paste hex here>'
pb = bytes.fromhex(hex_str)
print('Decoded state:', json.dumps(decode_pboutput(pb), indent=2))
print('Raw fields:')
for fn, wt, val in _decode_fields(pb):
    if isinstance(val, bytes):
        print(f'  f{fn}({len(val)}B): {val.hex()}')
    else:
        print(f'  f{fn}: {val}')
"
```

Or to decode from a saved binary file:
```bash
python3 -c "
data = open('scripts/map_response.bin','rb').read()
print(f'{len(data)}B: {data.hex()}')
"
```

---

## Known MQTT protocol

### Topics
| Topic | Direction | Content |
|-------|-----------|---------|
| `/device/{thing}/pboutput` | robot → app | State updates, map data |
| `/device/{thing}/pbinput` | app → robot | Commands |
| `/device/{thing}/notify-app` | robot → app | Online/offline: `{"robotState":"online"}` |

### Commands (pbinput field 5 = userCtrl)
| Code | Name | Notes |
|------|------|-------|
| 1 | CLEAN | Start fresh mow |
| 2 | DOCK | Dock + cancel task |
| 3 | PAUSE | Pause in place |
| 4 | RESUME | Resume from pause |
| 19 | QUERY_MAP | Request zone/map data — robot responds with polygon list |
| 20 | QUERY_SCHEDULES | Request schedule data |
| 23 | QUERY_PATH | Request mow path/track |
| 28 | FORCE_REINIT | Stop, reset to waiting |
| 33 | RECHARGE_DOCK | Dock + keep task progress |

### pboutput field map (confirmed from live capture)
| Field | Type | Content |
|-------|------|---------|
| 3 | packed int32s | errorCodes |
| 4 | packed int32s | warningCodes |
| 5 | sub-message | PbRobotInfo (see below) |
| 6 | sub-message | GPS/RTK: f1=satellites, f2=eastM(float32), f3=northM(float32), f4=rtkStatus, f5=rtkStatus(dup) |
| 10 | sub-message | PbDeviceProfile: f1=fwVersion, f2=mcuVersion, f5=ip, f6=mac |
| 12 | sub-message | Area/mow info: f1=mowStripCount(int), f2=totalAreaM2(float32), f5=mowProgress(float32 0–1) |
| 14 | sub-message | Robot pose ENU: f1=eastM(float32), f2=northM(float32), f3=thetaRad(float32) |
| 22 | sub-message | Extra info: f6=wifiRssiDbm(UTF-8 string e.g. "-77") |

#### PbRobotInfo sub-message (field 5)
| Sub-field | Name | Notes |
|-----------|------|-------|
| f2 | battery | % |
| f3 | wifiSignal | signal bars (int) |
| f4 | lteSignal | signal bars (int) |
| f6 | workStatus | 0=idle, 1=preparing, 2=mowing, 3=returning-to-dock, 5=paused (unconfirmed) |
| f7 | isRecharging | bool |
| f8 | isCharging | bool |
| f9 | unknown | 1 during mow+return; value at dock unconfirmed |
| f10 | unknown | 1 during mow+return; value at dock unconfirmed |

#### rtkStatus values (field 6.f4)
| Value | Meaning |
|-------|---------|
| 0 | No fix |
| 1 | Single-point GPS |
| 2 | RTK float (~30 cm) |
| 3 | RTK fixed (~2 cm) |

Map zone polygon data (field layout TBD — only returned during active mow or
in response to QUERY_MAP when zones are defined and robot is mowing).
