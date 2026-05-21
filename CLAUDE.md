# ha-lymow — AI session instructions

Unofficial Home Assistant integration for Lymow robotic lawn mowers.
Reverse-engineered from Android traffic capture and APK analysis.

## Source attribution — CRITICAL

All knowledge of API endpoints, MQTT topics, protobuf field layout, AWS
configuration, and Cognito pool IDs comes from our own Android traffic
capture and APK analysis. **Never reference any third-party repository as
a source.** If asked, say the information was captured from the Android app.

## Claude tooling installed in this repo

> The "no third-party repository as a source" rule above is about the **reverse-engineering provenance** of the API/MQTT/protobuf knowledge — that always traces to our own capture, never to an external repo. It does not apply to development *tooling*: the `.claude/` setup below is ordinary tooling whose origin we can name freely.

The engine (hooks, reviewer agents, workflow skills) comes from the [dotclaude](https://github.com/8408323/dotclaude) plugin, installed via its marketplace — not copied into this repo. `.claude/settings.json` wires it up (`extraKnownMarketplaces` + `enabledPlugins`); run `/plugin update dotclaude@dotclaude` to update it. What lives in this repo is only the **project-local layer**:

- `.claude/rules/` — project-owned instruction files, tuned for this pure-Python HA integration. Always-on: `code-quality.md`, `testing.md`. Path-scoped to `custom_components/lymow/**`: `security.md`, `error-handling.md`. **Code style lives in `code-quality.md` — don't duplicate it here.** (No `frontend.md` / `database.md` — there's no web UI or DB here.)
- `.claude/settings.json` — the marketplace/plugin wiring plus this repo's permission allow/deny.

Plugin-provided, available everywhere the plugin is enabled (namespaced `/dotclaude:*`):

- **Skills**: `/dotclaude:debug-fix`, `:tdd`, `:ship`, `:pr-review`, `:refactor`, `:explain`, `:test-writer`, `:context-budget`, and `:init` (rescaffolds the project-local layer / pulls base-config updates).
- **Agents**: `@code-reviewer`, `@security-reviewer`, `@performance-reviewer`, `@doc-reviewer`.
- **Hooks** (auto): secrets scan, format-on-save (ruff), dangerous-command block, file-protection, build-artifact warn, session-start context, post-compaction recovery, notify. The format/test hooks auto-detect this repo's `uv` + `ruff` + `pytest` setup.

`CLAUDE.local.md` (gitignored) is the place for personal overrides that should not be shared.

## Repository structure

```
custom_components/lymow/   # HA integration (the actual product)
  __init__.py              # entry setup / teardown
  auth.py                  # AWS Cognito SRP authentication
  api.py                   # REST API client (device list, device info)
  coordinator.py           # DataUpdateCoordinator: REST poll + MQTT merge
  mqtt.py                  # AWS IoT WebSocket MQTT client (aiomqtt + SigV4)
  protocol.py              # Hand-rolled protobuf encode/decode (no protoc)
  const.py                 # All constants: endpoints, status codes, commands
  lawn_mower.py            # LawnMowerEntity platform
  sensor.py                # SensorEntity platform
  manifest.json            # HA integration manifest
scripts/
  cli.py                   # Dev CLI — loads scripts/.env automatically
  query_map.py             # MQTT: send userCtrl=19 and dump zone/map response
  debug_mqtt.py            # MQTT: full debug dump (IoT REST shadow + connect + raw fields)
  adb_capture.sh           # ADB: logcat + optional tcpdump capture via USB
  .env.example             # Credential template (copy to scripts/.env)
docs/
  reverse_engineering.md   # Capture methods: MQTT CLI / ADB / mitmproxy
tests/
  conftest.py              # Loads lymow modules via importlib (no HA import chain)
  test_auth.py             # SRP unit tests
  test_api.py              # API client tests (aioresponses)
```

## Running the CLI

```
cp scripts/.env.example scripts/.env   # fill in LYMOW_USER / LYMOW_PASS
uv run python scripts/cli.py
```

Always use `uv run` — the project manages Python via uv, not system Python.

## Running tests

```
uv run pytest tests/ -v
```

## Key technical facts

- **Auth**: AWS Cognito USER_SRP_AUTH flow; eu-west-1 is the primary region
- **API**: REST via API Gateway; uses `AccessToken` (not `IdToken`) in the Authorization header, no "Bearer" prefix
- **MQTT**: AWS IoT over WebSocket with SigV4 presigned URL; temporary credentials from Cognito Identity Pool
- **Protocol**: Binary protobuf wrapped in `{"message": "<base64>"}` JSON envelope
  - Incoming: `/device/{thing}/pboutput` — robot state (workStatus, battery, errorCodes…)
  - Outgoing: `/device/{thing}/pbinput` — commands (USER_CTRL_CLEAN=1, PAUSE=3, RECHARGE_DOCK=33…)
  - Online notifications: `/device/{thing}/notify-app` — JSON `{"robotState": "online"|"offline"}`
- **Thing name key**: `deviceThingName` (confirmed from real API response)

## PR / branch workflow

- Branch per feature group, named `feat/<topic>`
- Stack branches on each other when they share dependencies to avoid
  cross-PR "missing symbol" false alarms from AI reviewers
  Correct merge order: mqtt-protocol → coordinator-commands → entities → auth-refresh
- After pushing, resolve the comment thread for each fixed comment using the
  GitHub GraphQL API:
  ```
  # Get thread node IDs
  gh api repos/<owner>/<repo>/pulls/<n>/comments --jq '.[] | {id, node_id, body}'
  # Resolve a thread
  gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "THREAD_NODE_ID"}) { thread { isResolved } } }'
  ```
- Then comment `@copilot please review again` and `@codex[agent] review`
  on each PR; wait ~5 minutes for re-review
- Cross-PR false alarms (reviewer sees each PR in isolation): explain in a comment,
  do not duplicate code across PRs

## Traffic capture / reverse engineering

Full instructions: **[docs/reverse_engineering.md](docs/reverse_engineering.md)**

Three methods — pick what fits:

1. **MQTT CLI** (no phone needed): `uv run python scripts/query_map.py`
2. **ADB + logcat** (USB, no proxy cert): `bash scripts/adb_capture.sh`
3. **mitmproxy** (full HTTPS): `mitmdump -s tools/capture.py --listen-host 0.0.0.0 --listen-port 8888 --ssl-insecure`

### ADB quick reference (WSL2)
- usbipd-win installed at `C:\Program Files\usbipd-win\`
- Run as **Administrator** in Windows PowerShell:
  ```powershell
  usbipd list                       # find phone bus ID (OnePlus = 1-3, but confirm)
  usbipd bind --busid 1-3
  usbipd attach --wsl --busid 1-3
  ```
- adb binary: wherever `adb` is in PATH (Android SDK platform-tools)
- Phone: OnePlus, USB bus **1-3**, WiFi IP **192.168.1.101** (example — your phone's LAN IP)
- If phone shows "unauthorized" after attach: tap Allow on phone screen

### mitmproxy quick reference
- Proxy: **192.168.1.100:8888** (example — your dev machine's LAN IP), phone proxy → same
- CA cert: browse to `http://mitm.it` on phone while proxy active → install Android cert
- Output: `tools/capture-lymow.txt` (gitignored)

## Sensitive data

`.env`, `*.pcap`, `*.har`, `secrets.yaml`, and `capture-*.txt` are gitignored.
Never commit credentials, tokens, or captured traffic.

## Code style

- No unnecessary comments — only add one when the WHY is non-obvious
- No multi-line docstrings; one short line max per function if needed
- Standard single-space assignments (no aligned columns)
- `from __future__ import annotations` at top of every module
- Type hints throughout; use `dict[str, Any]` not `Dict`
