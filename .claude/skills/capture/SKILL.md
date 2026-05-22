---
name: capture
description: Capture and decode Lymow traffic (MQTT / REST / BLE) for reverse engineering. Use when you need to observe device behaviour, confirm a protobuf field, or capture a new command. Project-specific to ha-lymow.
argument-hint: "[mqtt | adb | mitm | ble] [what you're trying to capture]"
disable-model-invocation: true
allowed-tools:
  - Bash(uv run *)
  - Bash(bash tools/*)
  - Read
  - Glob
  - Grep
---

# capture

Orchestrates this repo's reverse-engineering capture methods. Full reference: **[docs/reverse_engineering.md](docs/reverse_engineering.md)**. Pick the method that fits what you're after.

## 1. Pick a method

- **`mqtt`** — no phone needed. Subscribe to the device's MQTT topics from the CLI.
  - Map/zone dump: `uv run python scripts/query_map.py`
  - Watch all topics: `uv run python scripts/sniff_all_topics.py`
  - Watch outgoing commands: `uv run python scripts/sniff_pbinput.py`
  - Full debug (IoT shadow + connect + raw fields): `uv run python scripts/debug_mqtt.py`
- **`adb`** — phone on USB, no proxy cert. `bash tools/adb_capture.sh` (logcat + optional tcpdump). See the ADB/WSL2 quick reference in [CLAUDE.md](CLAUDE.md).
- **`mitm`** — full HTTPS (REST + MQTT-over-WebSocket). `mitmdump -s tools/capture.py --listen-host 0.0.0.0 --listen-port 8888 --ssl-insecure`, phone proxy → this host, install the cert via `http://mitm.it`.
- **`ble`** — manual-drive / GATT work. `uv run python tools/ble_drive_test.py`, `tools/gatt_discover.py`, `tools/hci_capture.py`, or `tools/raw_ble_drive.py` (root L2CAP). Drive captures land as `tools/drive_*.jsonl`.

## 2. Decode

- Decode protobuf payloads with the hand-rolled codec in `custom_components/lymow/protocol.py` (no protoc). For drive/turn frames, `tools/_decode_turn.py`.
- Cross-check field names against `custom_components/lymow/const.py`. Keep wire keys verbatim (`deviceThingName`, `workStatus`) — don't snake-case them.

## 3. Handle the artifacts (important)

- Capture output is **live secrets** — `*.pcap`, `*.har`, `*.cfa`, `capture-*.txt`, `tools/*.jsonl`, `tools/*.bin` are gitignored. Keep them so.
- Never paste raw capture contents (tokens, identity IDs, GPS, the device PIN) into code, commits, issues, or PRs. Redact first.
- Credentials come from `LYMOW_USER` / `LYMOW_PASS` (env / gitignored `scripts/.env`), never hardcoded.

## 4. Record the finding

When a capture confirms something new (a field meaning, a command, a topic), write it where it belongs: a decode in `protocol.py`/`const.py`, capture method notes in `docs/reverse_engineering.md`. Don't leave it only in a transient capture file.
