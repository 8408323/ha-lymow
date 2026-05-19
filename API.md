# Lymow API — Reverse Engineering Notes

Captured via mitmproxy from the official Android app (v2.1.46).
APK analysed via `strings` on `assets/index.android.bundle` (React Native bundle).

---

## Multi-Region Architecture

The app ships with **4 regions**, each with its own full stack. The region is
selected at account registration time — it is baked into the Cognito Identity
Pool ID returned after login. The integration must detect which region a user
belongs to and use the corresponding endpoints.

**How to determine the user's region:** After `InitiateAuth` / `RespondToAuthChallenge`,
call `cognito-identity` to get the Identity ID. Its prefix (`eu-west-1:`, `us-east-2:`,
etc.) tells you the region for all subsequent API calls.

| Region | Coverage |
|--------|----------|
| `eu-west-1` | Europe |
| `us-east-2` | Americas |
| `ap-southeast-2` | Australia / SE Asia |
| `ap-east-1` | East Asia (HK) |

---

## Authentication

Uses **AWS Amplify 6.15.5** with **AWS Cognito** (SRP auth). All regions share
the same auth flow but use region-local Cognito endpoints.

### Cognito Config Per Region

| Region | User Pool ID | Identity Pool ID |
|--------|-------------|-----------------|
| `eu-west-1` | `eu-west-1_6qNPbnrrd` | `eu-west-1:c905a69c-0153-401a-a879-0c50b892015b` |
| `us-east-2` | (not yet extracted) | `us-east-2:037db699-5df0-4ed2-92b8-0dd0f1843918` |
| `ap-southeast-2` | `ap-southeast-2_vNriuUNeQ` | `ap-southeast-2:87d0fe24-16af-4189-b02f-984a7ed14ee0` |
| `ap-east-1` | `ap-east-1_23Lf1WZer` | `ap-east-1:3e9265aa-f564-4083-8e1e-988e6cfdc446` |

**App Client ID (same across all regions):** `3h1sqv3hishjiofbv8giskjgb0`

Cognito Auth Domain: `mow.auth.<region>.amazoncognito.com`
S3 user data bucket pattern: `mow-user-data-<region>`

**Token lifetime:** AccessToken expires in 86400s (24h). RefreshToken is a JWE (opaque).

### Auth Flow
1. `POST https://cognito-idp.<region>.amazonaws.com/`
   - Target: `AWSCognitoIdentityProviderService.InitiateAuth`
   - Auth type: `USER_SRP_AUTH`
   - Returns a `PASSWORD_VERIFIER` challenge

2. `POST https://cognito-idp.<region>.amazonaws.com/`
   - Target: `AWSCognitoIdentityProviderService.RespondToAuthChallenge`
   - Completes SRP exchange
   - Returns `IdToken`, `AccessToken`, `RefreshToken`

3. `POST https://cognito-identity.<region>.amazonaws.com/`
   - Exchanges Cognito IdToken for temporary AWS credentials (`AccessKeyId`, `SecretKey`, `SessionToken`)
   - The returned Identity ID prefix determines which region the user belongs to
   - Credentials are used to SigV4-sign the IoT MQTT WebSocket URL

### Logout
- `POST cognito-idp` with target `RevokeToken`
- `POST <sns-api>/prod/sns-disable` (deregisters push notifications)

---

## REST API Endpoints Per Region

All endpoints are AWS API Gateway (`<id>.execute-api.<region>.amazonaws.com/prod`).
The path structure is identical across all regions — only the gateway ID differs.

### Endpoint IDs Per Region

| Purpose | eu-west-1 | us-east-2 | ap-southeast-2 | ap-east-1 |
|---------|-----------|-----------|----------------|-----------|
| Device list / validation | `asjqh5wbtj` | `zt810q0p60` | `vvikmtssjh` | `08ydw34dfj` |
| Device info & features | `6ghz1zkccg` | `6r8m5rxeth` | `7k2iuc99h7` | `m35t3px95i` |
| Firmware / OTA check | `eigc6a2ds9` | `453ahng0z4` | `2xipi98nw3` | `4gr97nlmga` |
| OTA job management | `io4nsakkt8` | `tvdfyh81d1` | `1sfa49lnl8` | `1h2q9awtqd` |
| Map | `3q1zxz98l2` | `bpath65iid` | `l2gobpcoqc` | `kdueg6qcwl` |
| Push notifications | `mu0adv3yse` | `suk4e76xe5` | `inflizu44a` | `i1pbnu30si` |
| WebSocket / misc | `eigc6a2ds9` | `6at3p6r6ce` | `19d2hfwavg` | `v2tsms5kll` |
| Live video (KVS) | `frgai1jfwg` | `xuw7gtx113` | — | `t0da44vtxf` |
| (unknown) | `l3hazobjk0` | — | — | — |

Confirmed `frgai1jfwg` (eu-west-1) is the **Kinesis Video Streams command** gateway via live capture (2026-05-19, app v3.0.6). Mappings for other regions are inferred and unverified.

### API Paths (same across all regions)

**Authorization:** All requests use the Cognito `AccessToken` (JWT) as a bare `Authorization` header (no `Bearer` prefix needed — the API Gateway Cognito authorizer accepts it directly).

**Device List** (`device-list` gateway):
| Method | Path | Description |
|--------|------|-------------|
| POST | `/prod/check-app-force-update` | Checks if app update is required |
| GET | `/prod/device-list-query?p=validation` | Session keep-alive (polled frequently) |
| GET | `/prod/device-list-query?p=devices&identityId=<id>` | Lists all devices for the user |

**Device List response** (confirmed):
```json
[{
  "deviceThingName": "device_<mac>",
  "deviceType": "Lymow one",
  "deviceName": "",
  "deviceBluetooth": "Lymow_<suffix>",
  "createdAt": "<ISO8601>",
  "fwMinVersion": "<version>",
  "deviceState": "online|offline",
  "deviceLocked": false,
  "sn": "<serial>",
  "simId": "<sim>",
  "geoFence": [{"name":"","latitude":0.0,"longitude":0.0,"radius":150}],
  "theftDetectionSwitch": true,
  "findRobotSwitch": true,
  "mobileNotificationSwitch": 2,
  "theftLock": false
}]
```

**Device Info & Features** (`device-info` gateway):
| Method | Path | Description |
|--------|------|-------------|
| GET | `/prod/get-device-info?deviceThingName=<thing>` | Connectivity info, location, versions |
| GET | `/prod/get-device-feature?deviceThingName=<thing>` | Feature flags / geofence / settings |
| PATCH | `/prod/update-device-feature` | Update device feature settings |

**get-device-info response** (confirmed — no battery/mowing state, those are MQTT-only):
```json
{
  "deviceThingName": "device_<mac>",
  "robotLocation": [<lat>, <lon>],
  "stolenStatus": false,
  "sn": "<serial>",
  "deviceState": "online|offline",
  "deviceBluetooth": "Lymow_<suffix>",
  "ipAddress": "<ip>",
  "simId": "<sim>",
  "mcuVersion": "<version>",
  "softwareVersion": "<version>",
  "macAddress": "<mac>",
  "cleanSchedules": ""
}
```

**get-device-feature response** (confirmed):
```json
{
  "deviceThingName": "device_<mac>",
  "geoFence": [{"name":"","latitude":0.0,"longitude":0.0,"radius":150}],
  "theftDetectionSwitch": true,
  "mobileNotificationSwitch": 2,
  "theftLock": false,
  "findRobotSwitch": true,
  "utcTime": "<ISO8601>"
}
```

**Firmware / OTA**:
| Method | Path | Description |
|--------|------|-------------|
| GET | `<ota-check>/prod/check-update?deviceThingName=<thing>` | Check for firmware update |
| GET | `<ota-job>/prod/create-ota-job?deviceThingName=<thing>&objectKey=<version>` | Trigger OTA update |
| GET | `<ota-job>/prod/get-ota-job-summary?deviceThingName=<thing>&jobId=<id>` | Poll OTA job status |

**check-update response** (confirmed eu-west-1 capture 2026-05-19):
```json
{
  "latestVersion": "v2.1.48_20260518",
  "prefix": "",
  "releaseNote": "Optimized camera exposure...\\nFixed positioning drift..."
}
```
`prefix` was empty in the captured call; `objectKey` for `create-ota-job` is assumed to be `prefix + latestVersion` (still untested live since an upgrade install would interrupt mowing).

**Update device feature** (`device-info` gateway):
| Method | Path | Description |
|--------|------|-------------|
| PATCH | `/prod/update-device-feature` | Set theft / find-robot / mobile-notification / theft-lock |

Request body (confirmed): `{"deviceThingName": "device_<mac>", "<fieldName>": <value>}` — a single feature field per call. Response returns the full feature state (geoFence, theftDetectionSwitch, mobileNotificationSwitch, theftLock, findRobotSwitch, utcTime).

`mobileNotificationSwitch` is an integer, **not a boolean** — observed values are `0` (off) and `2` (on); the wire format leaves room for a third state but only those two were seen.

The Anti-theft sub-menu in the app is a **geofence editor** (centre coords + radius slider + Enable toggle); the Enable toggle is `theftDetectionSwitch`, and editing the radius is a separate PATCH that updates `geoFence[0].radius`.

The Network Settings screen also issues a PATCH carrying `{"simInfo": {"code": 10000, "data": {...sim card status from oapi.eiotclub.com...}, "message": "Success"}}` — the data is **uploaded** to the Lymow API for caching; subsequent `get-device-feature` GETs do **not** return `simInfo`, so it's effectively write-only on the Lymow side. The `data` block includes (snake_case): `available_days`, `expire_date`, `expire_timestamp`, `iccid`, `imei`, `remain` (bytes), `remain_str` (human), `total`, `total_str`, `used`, `used_str`, `status_str`, `throttling` (bool), `usage_alert`, `card_status_str`.

**Mowing history** (`map` gateway):
| Method | Path | Description |
|--------|------|-------------|
| GET | `/prod/get-clean-history-collect?deviceThingName=<thing>&page=0&pageSize=15` | Paginated mow history + cumulative aggregates |

Response (confirmed):
```json
{
  "clean_history": [
    {"clean_area": 345, "clean_time": 60, "date": 1779184292,
     "percent": 1, "used_battery": 49, "soc_version": "v2.1.48",
     "start_type": 1, "status_times": [60,0,4,0,0,0],
     "error_list": [{"code": 74, "percent": 0.67}],
     "history_file": "device_<mac>/clean_history/<date>/<ts>.history",
     "hash_id": "<8 hex>"},
    ...
  ],
  "page": 0,
  "has_more": false,
  "total_records": 14,
  "clean_summary": {"total_clean_time": 829, "total_clean_area": 4243}
}
```
`date` is a Unix epoch (seconds). `percent` is 0..1 (float). Pages are zero-indexed.

**Map**:
| Method | Path | Description |
|--------|------|-------------|
| GET | `/prod/get-backup-map?deviceThingName=<thing>` | Retrieve saved mowing map |

**Live video (Kinesis Video Streams + WebRTC)** (`kvs` gateway):
| Method | Path | Description |
|--------|------|-------------|
| POST | `/prod/kvs/cmd` body `{"deviceThingName": "...", "action": "start"}` | Start a viewer session — returns temporary AWS credentials + KVS channelARN |

Flow (confirmed live capture):
1. POST `frgai1jfwg.execute-api.<region>.amazonaws.com/prod/kvs/cmd` → returns `{credentials: {accessKeyId, secretAccessKey, sessionToken, expiration}, channelARN, region, deviceThingName}` (creds expire after ~15 min)
2. POST `kinesisvideo.<region>.amazonaws.com/getSignalingChannelEndpoint` with `{ChannelARN, SingleMasterChannelEndpointConfiguration: {Protocols: ["WSS", "HTTPS"], Role: "VIEWER"}}` → returns HTTPS + WSS endpoint URLs
3. POST `<https-endpoint>/v1/get-ice-server-config` → returns TURN servers for NAT traversal
4. WebSocket connect to `<wss-endpoint>?<SigV4 query>` with `X-Amz-ChannelARN`, `X-Amz-ClientId=<deviceModel>_<os>_<deviceUuid>_userId_<cognitoSub>`, `X-Amz-Expires=299` → signaling channel for SDP / ICE exchange
5. Actual video bytes flow P2P over WebRTC (UDP/TURN) — opaque to mitmproxy

`channelARN` format: `arn:aws:kinesisvideo:<region>:<accountId>:channel/<deviceThingName>_stream_channel/<timestamp>`. The same AWS account (`863518414241`) was observed in eu-west-1.

**Push Notifications**:
| Method | Path | Description |
|--------|------|-------------|
| POST | `/prod/sns-registration?p=Loading` | Register device for push notifications |
| POST | `/prod/sns-disable` | Deregister on logout |

### SIM Card (3rd party — region-independent)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `oapi.eiotclub.com/api/v3/card/getCardsInfo` | SIM card info |
| POST | `sim.eiotclub.com/eshop/api/refresh/card` | Refresh SIM data |

---

## Real-time Control — AWS IoT MQTT over WebSocket

**IoT endpoint (same host ID across all regions):** `a3j5zqqo5iuph9-ats.iot.<region>.amazonaws.com`

**Connection:** WebSocket upgrade to `/mqtt` with SigV4-signed query params:
- `X-Amz-Algorithm`: `AWS4-HMAC-SHA256`
- `X-Amz-Credential`: temporary Cognito identity credentials (rotated)
- `X-Amz-Date`, `X-Amz-SignedHeaders`, `X-Amz-Signature`, `X-Amz-Security-Token`

**Protocol:** MQTT 3.1.1 over WebSocket

**Device thing name format:** `device_<mac_without_colons>` (e.g. `device_7890838300cd`)

**Topics observed** (confirmed live capture 2026-05-19):
- Incoming: `/device/<thing>/pboutput` — robot state (heartbeat every ~60s, larger payloads on map/zone responses up to ~35 KB)
- Outgoing: `/device/<thing>/pbinput` — commands + queries
- Notifications: `/device/<thing>/notify-app` — online/offline JSON

**RTK diagnostic fields** (read-only, from `pboutput` field 6; rendered live in the app's RTK Diagnostic screen):
- `rtkStatus` — 0 Not ready / 1 Float fix / 2 Fixed / 3 RTK fixed
- `rtkSatellites` — total GNSS satellite count (observed: 27)
- L1 / L2 / L5 per-band counts and SNR — sub-fields not yet decoded but present in pboutput
- Location precision in metres (observed: 0.010m, i.e. sub-cm)
- Base station status (Online/Offline)
- Data error rate (%)

**Payload envelope** (both directions): `{"message": "<base64 protobuf>"}`. See `custom_components/lymow/protocol.py` for the field-level schema.

**Captured-but-not-fully-identified pbinput at app startup**:
- Field `9` (length-delimited, observed value `58 01`) — sent at app startup alongside the device-registration string (e.g. `ONEPLUSA5010_Android_<uuid>`). Purpose still unknown.

The `userCtrl=52` (`USER_CTRL_QUERY_WIFI_4G`) seen at startup is already in `const.py`; the v3.0.6 app uses it to populate the Wi-Fi / 4G signal sub-screen.

---

## Integration Architecture Plan

```
Config Flow
  └─ username + password + region selection (auto-detected from Identity ID)
       └─ Cognito SRP auth (region-local) → IdToken + RefreshToken
            └─ Cognito Identity → temp AWS creds + detect region

Coordinator (polling ~30s)
  └─ GET get-device-info → mower state

MQTT Client
  └─ AWS IoT WebSocket (SigV4, region-local, creds refreshed via Cognito Identity)
       └─ subscribe to device shadow / status topics
       └─ publish commands (start, stop, park)

Entities (REST-only, until MQTT is added)
  └─ lawn_mower (state: reflects deviceState online/offline; mowing/docked/paused needs MQTT)
  └─ sensor: connectivity (deviceState)
  └─ sensor: firmware version (softwareVersion)
  └─ sensor: MCU version (mcuVersion)
  └─ sensor: IP address (ipAddress)

Entities (after MQTT)
  └─ lawn_mower (state: mowing / docked / paused / error — from MQTT shadow)
  └─ sensor: battery level — from MQTT only (not in REST responses)
  └─ sensor: error code — from MQTT only
```

---

## Open Questions
- User Pool ID for `us-east-2` (not yet extracted from APK)
- Token refresh flow (RefreshToken grant — AccessToken expires after 24h)
- Purpose of unknown API gateways (`l3hazobjk0` (eu-west-1), `xuw7gtx113` (us-east-2 KVS?), `t0da44vtxf` (ap-east-1 KVS?))
- `cleanSchedules` field format (always empty string in captured traffic — likely populated when schedules are configured)
- The `create-ota-job` request body — `objectKey` value is assumed `prefix + latestVersion` but untested live
- Field `9` purpose in pbinput at startup (observed value `58 01`, alongside device-registration string)
- Device Settings toggles (Vehicle, Rainy, Charging mode, Return-to-base mode) — UI toggle does not appear to fire a backend request synchronously; they may be batched on screen exit or stored via a different transport

## Resolved (live-captured 2026-05-19, app v3.0.6, eu-west-1)
- `update-device-feature` PATCH body shape ✓
- `check-update` response shape ✓ (`latestVersion` + `prefix` + `releaseNote`)
- `get-clean-history-collect` response shape ✓ (snake_case fields, Unix epoch date, `clean_summary` aggregates, pagination)
- `get-device-info` matches the documented shape ✓
- MQTT topics confirmed: `/device/<thing>/pboutput`, `/device/<thing>/pbinput`, `/device/<thing>/notify-app`
- `frgai1jfwg` gateway is Kinesis Video command endpoint ✓
