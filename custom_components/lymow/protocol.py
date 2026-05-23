"""Protobuf encode/decode for Lymow MQTT messages.

The robot publishes binary protobuf on /device/{thing}/pboutput wrapped in
a JSON envelope: {"message": "<base64>"}.  Commands are sent in the same
envelope format on /device/{thing}/pbinput.

All field numbers and wire types were determined from traffic capture of the
Android app communicating with the robot over MQTT.
"""

from __future__ import annotations

import base64
import json
import struct
from typing import Any

# Protocol version used in all outgoing PbInput messages (confirmed from ADB capture: field 2 = 49)
PB_VERSION = 49

# ---------------------------------------------------------------------------
# Varint helpers
# ---------------------------------------------------------------------------


def _encode_varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = []
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            break
    return bytes(out)


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _decode_packed_int32s(data: bytes) -> list[int]:
    """Decode a packed repeated int32 field (raw varint stream, no tags)."""
    pos = 0
    values: list[int] = []
    while pos < len(data):
        v, pos = _decode_varint(data, pos)
        values.append(_signed32(v))
    return values


# ---------------------------------------------------------------------------
# Field encoding helpers
# ---------------------------------------------------------------------------


def _field_i32(field_no: int, value: int) -> bytes:
    """Encode a signed/unsigned int32 field (wire type 0 = varint)."""
    tag = _encode_varint((field_no << 3) | 0)
    return tag + _encode_varint(value & 0xFFFFFFFFFFFFFFFF)


def _field_bytes(field_no: int, data: bytes) -> bytes:
    """Encode a length-delimited field (wire type 2)."""
    tag = _encode_varint((field_no << 3) | 2)
    return tag + _encode_varint(len(data)) + data


def _field_str(field_no: int, value: str) -> bytes:
    return _field_bytes(field_no, value.encode("utf-8"))


def _field_f32(field_no: int, value: float) -> bytes:
    """Encode a float32 field (wire type 5 = 32-bit fixed)."""
    tag = _encode_varint((field_no << 3) | 5)
    return tag + struct.pack("<f", value)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def wrap_envelope(pb_bytes: bytes) -> str:
    """Encode protobuf bytes as a JSON envelope string ready for MQTT publish."""
    return json.dumps({"message": base64.b64encode(pb_bytes).decode()})


def unwrap_envelope(payload: str | bytes) -> bytes:
    """Decode the JSON envelope from an MQTT message and return raw protobuf bytes."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    obj = json.loads(payload)
    for key in ("message", "value", "data", "payload"):
        if key in obj:
            return base64.b64decode(obj[key])
    raise ValueError(f"No known envelope key in: {list(obj)}")


# ---------------------------------------------------------------------------
# Protobuf decoder (minimal — handles varint, length-delimited, 32/64-bit)
# ---------------------------------------------------------------------------


def _decode_fields(data: bytes) -> list[tuple[int, int, Any]]:
    """Return list of (field_no, wire_type, value) tuples from a protobuf blob."""
    pos = 0
    fields: list[tuple[int, int, Any]] = []
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        field_no = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            value, pos = _decode_varint(data, pos)
            fields.append((field_no, wire_type, value))
        elif wire_type == 1:  # 64-bit
            value = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
            fields.append((field_no, wire_type, value))
        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            value = data[pos : pos + length]
            pos += length
            fields.append((field_no, wire_type, value))
        elif wire_type == 5:  # 32-bit
            value = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            fields.append((field_no, wire_type, value))
        else:
            break  # unknown wire type — stop parsing
    return fields


def _first(fields: list[tuple[int, int, Any]], field_no: int, default: Any = None) -> Any:
    for fn, _wt, val in fields:
        if fn == field_no:
            return val
    return default


def _all(fields: list[tuple[int, int, Any]], field_no: int) -> list[Any]:
    return [val for fn, _wt, val in fields if fn == field_no]


def _signed32(v: int) -> int:
    """Interpret a varint as a signed int32, handling negative 64-bit encodings."""
    v &= 0xFFFFFFFF
    if v >= 0x80000000:
        v -= 0x100000000
    return v


def _decode_f32(raw: int) -> float:
    """Decode a raw uint32 (from wire type 5) as an IEEE 754 single-precision float."""
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


def _decode_map_point(data: bytes) -> dict[str, float]:
    """Decode an x/y point sub-message where f1=x and f2=y are i32 floats."""
    f = _decode_fields(data)
    x_raw = _first(f, 1)
    y_raw = _first(f, 2)
    return {
        "x": _decode_f32(x_raw) if x_raw is not None else 0.0,
        "y": _decode_f32(y_raw) if y_raw is not None else 0.0,
    }


def _decode_map_polygon(data: bytes) -> list[dict[str, float]]:
    """Decode a polygon sub-message containing repeated f1 point sub-messages."""
    f = _decode_fields(data)
    return [_decode_map_point(p) for p in _all(f, 1) if isinstance(p, bytes)]


# ---------------------------------------------------------------------------
# PbMapResponse decoder — extracts zones, no-go zones, base station, GPS
# ---------------------------------------------------------------------------

# Map content field numbers (inside the double-wrapped f23→f2→f3 structure)
_MAP_CONTENT_GO_ZONES = 1
_MAP_CONTENT_NOGO_ZONES = 2
_MAP_CONTENT_CHANNELS = 3
_MAP_CONTENT_CHARGING_STATION = 4
_MAP_CONTENT_GPS_ORIGIN = 7


def extract_raw_map_content(pb_bytes: bytes) -> bytes | None:
    """Extract raw PbMap content bytes from a map-response PbOutput blob.

    Navigation path: PbOutput.f23 → f2 → f3 = raw PbMap content.
    Returns None if the map response is not present in the message.
    """
    top = _decode_fields(pb_bytes)
    outer_raw = _first(top, 23)
    if not isinstance(outer_raw, bytes):
        return None
    wrapper_raw = _first(_decode_fields(outer_raw), 2)
    if not isinstance(wrapper_raw, bytes):
        return None
    content_raw = _first(_decode_fields(wrapper_raw), 3)
    return content_raw if isinstance(content_raw, bytes) else None


def _zone_hash_from_raw(zone_raw: bytes) -> str:
    """Extract hashId from a raw goZone or nogoZone sub-message (f1=BasicInfo, f3=hashId)."""
    bi_raw = _first(_decode_fields(zone_raw), 1)
    if not isinstance(bi_raw, bytes):
        return ""
    h = _first(_decode_fields(bi_raw), 3)
    return h.decode("utf-8", errors="replace") if isinstance(h, bytes) else ""


def _nogo_parent_from_raw(nogo_raw: bytes) -> str:
    """Extract parentZoneHashId from a raw nogoZone sub-message (f4=parentZoneHashId)."""
    p = _first(_decode_fields(nogo_raw), 4)
    return p.decode("utf-8", errors="replace") if isinstance(p, bytes) else ""


def delete_zone_from_raw_content(content_bytes: bytes, hash_id: str) -> bytes:
    """Surgically remove a zone (and its child nogoZones) from raw PbMap content bytes.

    All other fields are preserved byte-for-byte.  modifyHashs (f9) is appended
    with the deleted zone's hash so the robot knows what changed.
    """
    result = b""
    for fn, wt, val in _decode_fields(content_bytes):
        if fn == _MAP_CONTENT_GO_ZONES and isinstance(val, bytes):
            if _zone_hash_from_raw(val) == hash_id:
                continue  # drop deleted goZone
        if fn == _MAP_CONTENT_NOGO_ZONES and isinstance(val, bytes):
            if _nogo_parent_from_raw(val) == hash_id:
                continue  # drop child nogoZone
        # Re-encode field with original wire type
        tag = _encode_varint((fn << 3) | wt)
        if wt == 0:  # varint
            result += tag + _encode_varint(val)
        elif wt == 1:  # 64-bit fixed
            result += tag + struct.pack("<Q", val)
        elif wt == 2:  # length-delimited
            result += tag + _encode_varint(len(val)) + val
        elif wt == 5:  # 32-bit fixed
            result += tag + struct.pack("<I", val)
    result += _field_str(9, hash_id)  # modifyHashs
    return result


def encode_sync_map_raw(raw_content: bytes) -> bytes:
    """Encode a sync-map command using pre-built raw PbMap content bytes.

    Use this with delete_zone_from_raw_content() to preserve every field of the
    original map response while only removing the target zone.
    """
    from .const import USER_CTRL_SYNC_MAP

    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_SYNC_MAP)
    pb += _field_bytes(23, raw_content)
    return pb


def decode_map_response(pb_bytes: bytes) -> dict[str, Any]:
    """Decode a PbMapResponse blob into go zones, no-go zones, charging station and GPS origin.

    Navigation path: PbOutput.f23 → f2 → f3 (map content).
    Field layout determined from live traffic capture (map_response.bin, 7957 B).
    """
    top = _decode_fields(pb_bytes)
    outer_raw = _first(top, 23)
    if not isinstance(outer_raw, bytes):
        return {}
    wrapper_raw = _first(_decode_fields(outer_raw), 2)
    if not isinstance(wrapper_raw, bytes):
        return {}
    content_raw = _first(_decode_fields(wrapper_raw), 3)
    if not isinstance(content_raw, bytes):
        return {}
    content = _decode_fields(content_raw)

    result: dict[str, Any] = {}

    # ---- Go zones (f1, repeated) -----------------------------------------
    go_zones: list[dict[str, Any]] = []
    for zone_raw in _all(content, _MAP_CONTENT_GO_ZONES):
        if not isinstance(zone_raw, bytes):
            continue
        zf = _decode_fields(zone_raw)
        zone: dict[str, Any] = {}

        bi_raw = _first(zf, 1)
        if isinstance(bi_raw, bytes):
            bi = _decode_fields(bi_raw)
            hash_raw = _first(bi, 3)
            poly_raw = _first(bi, 5)
            zone["hashId"] = hash_raw.decode("utf-8", errors="replace") if isinstance(hash_raw, bytes) else ""
            zone["type"] = _first(bi, 1, 0)
            zone["isEnabled"] = bool(_first(bi, 4, 1))
            zone["polygon"] = _decode_map_polygon(poly_raw) if isinstance(poly_raw, bytes) else []

        pp_raw = _first(zf, 3)
        if isinstance(pp_raw, bytes):
            pp = _decode_fields(pp_raw)
            bmin_raw = _first(pp, 1)
            bmax_raw = _first(pp, 2)
            area = _first(pp, 3)
            inner_raw = _first(pp, 5)
            if isinstance(bmin_raw, bytes):
                zone["boundMin"] = _decode_map_point(bmin_raw)
            if isinstance(bmax_raw, bytes):
                zone["boundMax"] = _decode_map_point(bmax_raw)
            if area is not None:
                zone["area"] = area
            if isinstance(inner_raw, bytes):
                zone["innerPoint"] = _decode_map_point(inner_raw)

        cfg_raw = _first(zf, 2)
        if isinstance(cfg_raw, bytes) and len(cfg_raw) > 0:
            cf = _decode_fields(cfg_raw)
            cut_h = _first(cf, 1)
            path_sp = _first(cf, 4)
            if cut_h is not None:
                zone["cutHeight"] = cut_h
            if path_sp is not None:
                zone["pathSpacing"] = _decode_f32(path_sp)

        go_zones.append(zone)
    result["goZones"] = go_zones

    # ---- No-go zones (f2, repeated) --------------------------------------
    nogo_zones: list[dict[str, Any]] = []
    for nogo_raw in _all(content, _MAP_CONTENT_NOGO_ZONES):
        if not isinstance(nogo_raw, bytes):
            continue
        nf = _decode_fields(nogo_raw)
        nogo: dict[str, Any] = {}

        bi_raw = _first(nf, 1)
        if isinstance(bi_raw, bytes):
            bi = _decode_fields(bi_raw)
            hash_raw = _first(bi, 3)
            poly_raw = _first(bi, 5)
            nogo["hashId"] = hash_raw.decode("utf-8", errors="replace") if isinstance(hash_raw, bytes) else ""
            nogo["type"] = _first(bi, 1, 0)
            nogo["isEnabled"] = bool(_first(bi, 4, 1))
            nogo["polygon"] = _decode_map_polygon(poly_raw) if isinstance(poly_raw, bytes) else []

        pp_raw = _first(nf, 3)
        if isinstance(pp_raw, bytes):
            pp = _decode_fields(pp_raw)
            area = _first(pp, 3)
            inner_raw = _first(pp, 5)
            if area is not None:
                nogo["area"] = area
            if isinstance(inner_raw, bytes):
                nogo["innerPoint"] = _decode_map_point(inner_raw)

        parent_raw = _first(nf, 4)
        if isinstance(parent_raw, bytes) and len(parent_raw) > 0:
            nogo["parentZoneHashId"] = parent_raw.decode("utf-8", errors="replace")

        nogo_zones.append(nogo)
    result["nogoZones"] = nogo_zones

    # ---- Channels (f3, repeated) — path connectors between zones ---------
    channels: list[dict[str, Any]] = []
    for chan_raw in _all(content, _MAP_CONTENT_CHANNELS):
        if isinstance(chan_raw, bytes):
            channels.append(decode_channel(chan_raw))
    result["channels"] = channels

    # ---- Charging station pose (f4) — x/y/theta as i32 floats -----------
    cs_raw = _first(content, _MAP_CONTENT_CHARGING_STATION)
    if isinstance(cs_raw, bytes):
        cs = _decode_fields(cs_raw)
        x_raw = _first(cs, 1)
        y_raw = _first(cs, 2)
        t_raw = _first(cs, 3)
        result["chargingStation"] = {
            "x": _decode_f32(x_raw) if x_raw is not None else 0.0,
            "y": _decode_f32(y_raw) if y_raw is not None else 0.0,
            "theta": _decode_f32(t_raw) if t_raw is not None else 0.0,
        }

    # ---- GPS origin (f7) — lat/lon as i32 floats -------------------------
    gps_raw = _first(content, _MAP_CONTENT_GPS_ORIGIN)
    if isinstance(gps_raw, bytes):
        gf = _decode_fields(gps_raw)
        lat_raw = _first(gf, 1)
        lon_raw = _first(gf, 2)
        result["gpsOrigin"] = {
            "lat": _decode_f32(lat_raw) if lat_raw is not None else 0.0,
            "lon": _decode_f32(lon_raw) if lon_raw is not None else 0.0,
        }

    return result


def decode_channel(data: bytes) -> dict[str, Any]:
    """Decode a PbChannel (path connector between two zones).

    Field layout from PbChannel.encode (Hermes): f1 hashId, f2 zone1, f3 zone2,
    f4 isValid, f5 polygon, f6 isDockingChannel, f9 cutHeight, f10 channelLift.
    """
    f = _decode_fields(data)
    chan: dict[str, Any] = {}
    for key, fn in (("hashId", 1), ("zone1", 2), ("zone2", 3)):
        raw = _first(f, fn)
        if isinstance(raw, bytes):
            chan[key] = raw.decode("utf-8", errors="replace")
    chan["isValid"] = bool(_first(f, 4, 0))
    chan["isDockingChannel"] = bool(_first(f, 6, 0))
    poly_raw = _first(f, 5)
    if isinstance(poly_raw, bytes):
        chan["polygon"] = _decode_map_polygon(poly_raw)
    cut_h = _first(f, 9)
    if cut_h is not None:
        chan["cutHeight"] = cut_h
    lift = _first(f, 10)
    if lift is not None:
        chan["channelLift"] = lift
    return chan


# ---------------------------------------------------------------------------
# PbOutput decoder — maps to a flat state dict
# ---------------------------------------------------------------------------


def decode_pboutput(pb_bytes: bytes) -> dict[str, Any]:
    """Decode a PbOutput protobuf blob into a flat state dict.

    Field layout (from capture analysis of the robot ↔ app protocol):
      field 2  (varint)   protocol version
      field 3  (bytes)    packed errorCodes (repeated int32)
      field 4  (bytes)    packed warningCodes (repeated int32)
      field 5  (bytes)    PbRobotInfo sub-message
      field 10 (bytes)    PbDeviceProfile sub-message
    """
    state: dict[str, Any] = {}
    fields = _decode_fields(pb_bytes)

    # Error / warning codes — packed repeated int32 (raw varint stream, no field tags)
    error_raw = _first(fields, 3)
    if isinstance(error_raw, bytes) and error_raw:
        state["errorCodes"] = _decode_packed_int32s(error_raw)
        state["errorCode"] = state["errorCodes"][0] if state["errorCodes"] else 0
    else:
        state["errorCodes"] = []
        state["errorCode"] = 0

    warning_raw = _first(fields, 4)
    if isinstance(warning_raw, bytes) and warning_raw:
        state["warningCodes"] = _decode_packed_int32s(warning_raw)
    else:
        state["warningCodes"] = []

    # PbRobotInfo (field 5)
    robot_info_raw = _first(fields, 5)
    if isinstance(robot_info_raw, bytes):
        ri = _decode_fields(robot_info_raw)
        ws_raw = _first(ri, 6)
        state["workStatus"] = _signed32(ws_raw) if ws_raw is not None else -1
        robot_state_raw = _first(ri, 1)
        if robot_state_raw is not None:
            state["robotState"] = _signed32(robot_state_raw)
        battery = _first(ri, 2)
        if battery is not None:
            state["battery"] = _signed32(battery)
        state["isCharging"] = bool(_first(ri, 8, 0))
        state["isRecharging"] = bool(_first(ri, 7, 0))
        wifi_sig = _first(ri, 3)
        if wifi_sig is not None:
            state["wifiSignalQuality"] = _signed32(wifi_sig)
        lte_sig = _first(ri, 4)
        if lte_sig is not None:
            state["lteSignalQuality"] = _signed32(lte_sig)

    # PbDeviceProfile (field 10)
    profile_raw = _first(fields, 10)
    if isinstance(profile_raw, bytes):
        dp = _decode_fields(profile_raw)
        for field_no, key in ((1, "fwVersion"), (2, "mcuVersion"), (5, "ipAddress"), (6, "macAddress"), (7, "sn")):
            val = _first(dp, field_no)
            if isinstance(val, bytes):
                state[key] = val.decode("utf-8", errors="replace")

    # GPS / RTK (field 6 of outer PbOutput):
    #   f1=satellites(int), f2=eastM(float32), f3=northM(float32), f4=rtkStatus(int)
    rtk_raw = _first(fields, 6)
    if isinstance(rtk_raw, bytes):
        rtk = _decode_fields(rtk_raw)
        sats = _first(rtk, 1)
        if sats is not None:
            state["rtkSatellites"] = _signed32(sats)
        east = _first(rtk, 2)
        if east is not None:
            state["rtkEastM"] = _decode_f32(east)
        north = _first(rtk, 3)
        if north is not None:
            state["rtkNorthM"] = _decode_f32(north)
        rtk_status = _first(rtk, 4)
        if rtk_status is not None:
            state["rtkStatus"] = _signed32(rtk_status)

    # Area / progress info (field 12):
    #   f1=mowStripCount(int), f2=totalTaskArea(float32, the current task's
    #   total area — denominator for mowProgress), f5=mowProgress(float32 0–1)
    area_raw = _first(fields, 12)
    if isinstance(area_raw, bytes):
        area_fields = _decode_fields(area_raw)
        total_area = _first(area_fields, 2)
        if total_area is not None:
            state["totalTaskAreaM2"] = _decode_f32(total_area)
        strip_count = _first(area_fields, 1)
        if strip_count is not None:
            state["mowStripCount"] = _signed32(strip_count)
        progress_raw = _first(area_fields, 5)
        if progress_raw is not None:
            state["mowProgress"] = round(_decode_f32(progress_raw) * 100, 1)

    # Wi-Fi sub-message (field 22): f6=rssiDbm (UTF-8 string like "-77")
    wifi22_raw = _first(fields, 22)
    if isinstance(wifi22_raw, bytes):
        wifi22_fields = _decode_fields(wifi22_raw)
        rssi_raw = _first(wifi22_fields, 6)
        if isinstance(rssi_raw, bytes):
            try:
                state["wifiRssiDbm"] = int(rssi_raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pass

    # Robot pose ENU (field 14): f1=eastM, f2=northM, f3=thetaRad (all float32)
    pose_raw = _first(fields, 14)
    if isinstance(pose_raw, bytes):
        pose_fields = _decode_fields(pose_raw)
        east_m = _first(pose_fields, 1)
        north_m = _first(pose_fields, 2)
        theta_rad = _first(pose_fields, 3)
        if east_m is not None:
            state["poseEastM"] = _decode_f32(east_m)
        if north_m is not None:
            state["poseNorthM"] = _decode_f32(north_m)
        if theta_rad is not None:
            state["poseThetaRad"] = _decode_f32(theta_rad)

    # Mowing schedules: PbOutput field 16 = PbSchedules { tasks(1) = [PbSchedule] }.
    # The QUERY_SCHEDULES reply carries the full list (verified against a live
    # capture of the app — the input and output PbSchedule are the same message).
    schedules_raw = _first(fields, 16)
    if isinstance(schedules_raw, bytes):
        tasks = _all(_decode_fields(schedules_raw), 1)
        state["schedules"] = [decode_schedule_entry(t) for t in tasks if isinstance(t, bytes)]

    return state


def decode_schedule_entry(data: bytes) -> dict[str, Any]:
    """Decode a PbSchedule into a flat dict.

    Field layout verified against the app's wire format (see encoder side):
    f1 dayOfWeek (packed), f2 hour (UTC), f3 minute, f4 isRepeated, f5 zonesInfo
    (PbZoneBasicInfo, hashId=f3), f6 id, f7 timeZone (UTC offset hours), f8
    isDisabled. hour/minute are UTC as stored by the robot.
    """
    f = _decode_fields(data)
    days_raw = _first(f, 1)
    days = _decode_packed_int32s(days_raw) if isinstance(days_raw, bytes) else []
    hour = _signed32(_first(f, 2, 0))
    minute = _signed32(_first(f, 3, 0))
    # PbOutput is untrusted: bound the wire values so a malformed payload can't
    # surface garbage (huge / negative) hour, minute or weekday to HA state.
    entry: dict[str, Any] = {
        "dayOfWeek": [d for d in days if 0 <= d <= 6],
        "hour": hour if 0 <= hour <= 23 else 0,
        "minute": minute if 0 <= minute <= 59 else 0,
        "isRepeated": bool(_first(f, 4, 0)),
        "isDisabled": bool(_first(f, 8, 0)),
    }
    zone_ids: list[str] = []
    for zraw in _all(f, 5):
        if isinstance(zraw, bytes):
            hash_raw = _first(_decode_fields(zraw), 3)
            if isinstance(hash_raw, bytes):
                zone_ids.append(hash_raw.decode("utf-8", errors="replace"))
    entry["zones"] = zone_ids
    sched_id = _first(f, 6)
    if sched_id is not None:
        entry["id"] = int(sched_id)
    tz = _first(f, 7)
    if tz is not None:
        entry["timeZone"] = _signed32(tz)
    return entry


# ---------------------------------------------------------------------------
# PbInput encoders — commands sent to the robot
# ---------------------------------------------------------------------------


def encode_userctrl(command: int) -> bytes:
    """Encode a simple user control command."""
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, command)
    return pb


# PbTaskConfig field map — (proto field number, wire kind) — derived from APK
# (Hermes) analysis of the app's ts-proto encoder. The message is carried in
# PbInput field 26 under USER_CTRL_SET_TASK_CONFIG.
_TASK_CONFIG_FIELDS: dict[str, tuple[int, str]] = {
    "raiseCutHeight": (2, "bool"),
    "lowerCutHeight": (3, "bool"),
    "moveSpeed": (4, "float"),
    "brushSpeed": (5, "int"),
    "cutSpeed": (6, "int"),
    "cleanMode": (7, "int"),
    "cleanDir": (8, "int"),
    "pathSpacing": (9, "int"),
    "perimeterMowLaps": (10, "int"),
    "perimeterMowDir": (11, "int"),
    "noGoMowLaps": (12, "int"),
    "obsDecMode": (13, "int"),
    "pathOrder": (14, "bool"),
    "startProgress": (15, "int"),
    "relativeCleanDir": (16, "int"),
    "lineFollowMode": (17, "bool"),
    "disableOuterDischarge": (18, "bool"),
    "followDetectMode": (19, "int"),
}


def encode_set_task_config(**fields: Any) -> bytes:
    """Encode a USER_CTRL_SET_TASK_CONFIG command setting only the given fields.

    Field names match PbTaskConfig (see ``_TASK_CONFIG_FIELDS``); ``None``
    values are skipped so only explicitly-set parameters are sent. Unknown
    field names raise ValueError.
    """
    cfg = b""
    for name, value in fields.items():
        if value is None:
            continue
        if name not in _TASK_CONFIG_FIELDS:
            raise ValueError(f"unknown task-config field: {name}")
        field_no, kind = _TASK_CONFIG_FIELDS[name]
        if kind == "bool":
            cfg += _field_i32(field_no, 1 if value else 0)
        elif kind == "float":
            cfg += _field_f32(field_no, float(value))
        else:
            cfg += _field_i32(field_no, int(value))
    from .const import USER_CTRL_SET_TASK_CONFIG

    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_SET_TASK_CONFIG)
    pb += _field_bytes(26, cfg)
    return pb


# PbRobotConfig field map — (proto field number) — derived from APK (Hermes)
# analysis of PbRobotConfig.encode (fn #9506 at offset 0x004a7ce8). Carried in
# PbInput.robotConfig (field 13) for device-config writes; the app omits
# userCtrl on these — the robot dispatches based on the submessage shape (see
# setNetworkType fn #8970). Only the bool fields we expose today are listed
# here; broaden the codec when adding non-bool fields (audioVolume int,
# rcCutHeight int, etc.).
_ROBOT_CONFIG_BOOL_FIELDS: dict[str, int] = {
    "metric_4g": 11,  # true = 4G preferred, false = WiFi preferred
}


def encode_set_robot_config(**fields: Any) -> bytes:
    """Encode a PbInput carrying only a PbRobotConfig sub-message.

    Unlike SET_TASK_CONFIG / SET_RUN_TIME_CONFIG, robotConfig writes don't set
    userCtrl — the robot routes on the submessage shape itself. Only the named
    PbRobotConfig fields are sent; ``None`` is skipped. Unknown names raise.
    """
    cfg = b""
    for name, value in fields.items():
        if value is None:
            continue
        if name not in _ROBOT_CONFIG_BOOL_FIELDS:
            raise ValueError(f"unknown robot-config field: {name}")
        cfg += _field_i32(_ROBOT_CONFIG_BOOL_FIELDS[name], 1 if value else 0)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    return pb


# PbRunTimeConfig field map — (proto field number, wire kind) — derived from APK
# (Hermes) analysis of the app's ts-proto encoder (PbRunTimeConfig.encode). The
# message is carried at PbInput.map.runTimeConfig (PbInput field 12 → PbMap
# field 13) under USER_CTRL_SET_RUN_TIME_CONFIG. ``channelConfig`` (PbChannelConfig,
# field 7) is intentionally omitted — per-channel overrides aren't exposed here.
_RUN_TIME_CONFIG_FIELDS: dict[str, tuple[int, str]] = {
    "cutHeight": (1, "int"),
    "moveSpeed": (4, "float"),
    "cutSpeed": (6, "int"),
}


def encode_set_run_time_config(**fields: Any) -> bytes:
    """Encode a USER_CTRL_SET_RUN_TIME_CONFIG command setting only the given fields.

    Field names match PbRunTimeConfig (see ``_RUN_TIME_CONFIG_FIELDS``); ``None``
    values are skipped so only explicitly-set parameters are sent. Unknown
    field names raise ValueError.
    """
    cfg = b""
    for name, value in fields.items():
        if value is None:
            continue
        if name not in _RUN_TIME_CONFIG_FIELDS:
            raise ValueError(f"unknown run-time-config field: {name}")
        field_no, kind = _RUN_TIME_CONFIG_FIELDS[name]
        if kind == "float":
            cfg += _field_f32(field_no, float(value))
        else:
            cfg += _field_i32(field_no, int(value))
    from .const import USER_CTRL_SET_RUN_TIME_CONFIG

    pb_map = _field_bytes(13, cfg)  # PbMap.runTimeConfig
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_SET_RUN_TIME_CONFIG)
    pb += _field_bytes(12, pb_map)  # PbInput.map
    return pb


def encode_query_map(queryIndex: int = 0) -> bytes:
    """Encode a query-map command (userCtrl=19)."""
    sub = _field_i32(1, queryIndex) + _field_i32(4, 1)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, 19)  # USER_CTRL_QUERY_MAP
    pb += _field_bytes(23, sub)
    return pb


def encode_query_schedules() -> bytes:
    """Encode a query-schedules command (userCtrl=20)."""
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, 20)  # USER_CTRL_QUERY_SCHEDULES
    return pb


def encode_clear_schedules() -> bytes:
    """Encode a clear-all-schedules command.

    Schedules live in PbInput field 11 (PbSchedules). Sending an empty field 11
    clears every schedule — captured verbatim from the app's delete flow
    (PbInput { version=49, schedule(11)=<empty> } == 10 31 5a 00).
    """
    return _field_i32(2, PB_VERSION) + _field_bytes(11, b"")


# PbSchedule (PbSchedules.tasks) field map — VERIFIED against a live capture of
# the app's "Save Task" flow. hour/minute are UTC; timeZone is the UTC offset in
# hours. zonesInfo is PbZoneBasicInfo (name, hashId, a selected flag, and the
# zone's representative point); config is PbScheduleConfig.
def _encode_zone_basic_info(zone: dict[str, Any]) -> bytes:
    """Encode a PbZoneBasicInfo for a schedule's zonesInfo (f5)."""
    bi = b""
    bi += _field_str(2, zone.get("name", ""))
    bi += _field_str(3, zone["hashId"])
    bi += _field_i32(8, 1)
    point = zone.get("point")
    if point is not None:
        bi += _field_bytes(9, _field_f32(1, float(point.get("x", 0.0))) + _field_f32(2, float(point.get("y", 0.0))))
    return bi


def _encode_schedule_config(cfg: dict[str, Any]) -> bytes:
    """Encode a PbScheduleConfig (per-task zone overrides, f11)."""
    pb = b""
    if "hashId" in cfg:
        pb += _field_str(1, cfg["hashId"])
    if "cutHeight" in cfg:
        pb += _field_i32(2, int(cfg["cutHeight"]))
    if "moveSpeed" in cfg:
        pb += _field_f32(3, float(cfg["moveSpeed"]))
    if "pathSpacing" in cfg:
        pb += _field_i32(4, int(cfg["pathSpacing"]))
    return pb


def _encode_schedule_entry(entry: dict[str, Any]) -> bytes:
    """Encode one PbSchedule sub-message.

    Keys: dayOfWeek (list[int]), hour (UTC), minute, isRepeated, zones
    (list of {hashId, name?, point?}), id, timeZone (UTC offset hours),
    isDisabled, config ({hashId, cutHeight, moveSpeed, pathSpacing}).
    """
    pb = b""
    days = entry.get("dayOfWeek")
    if days:
        pb += _field_bytes(1, b"".join(_encode_varint(int(d) & 0xFFFFFFFF) for d in days))
    if "hour" in entry:
        pb += _field_i32(2, int(entry["hour"]))
    if "minute" in entry:
        pb += _field_i32(3, int(entry["minute"]))
    if entry.get("isRepeated"):
        pb += _field_i32(4, 1)
    for zone in entry.get("zones", []):
        pb += _field_bytes(5, _encode_zone_basic_info(zone))
    if "id" in entry:
        pb += _field_i32(6, int(entry["id"]))
    if "timeZone" in entry:
        pb += _field_i32(7, int(entry["timeZone"]))
    if entry.get("isDisabled"):
        pb += _field_i32(8, 1)
    if entry.get("isAngleOffset"):
        pb += _field_i32(9, 1)
    config = entry.get("config")
    if config:
        pb += _field_bytes(11, _encode_schedule_config(config))
    return pb


def encode_set_schedules(entries: list[dict[str, Any]]) -> bytes:
    """Encode a set-schedules command (PbInput.schedule = PbSchedules{tasks}).

    Like clear-schedules, this carries no userCtrl — the robot acts on the
    presence of field 11. Each entry becomes one PbSchedule in tasks (field 1).
    """
    tasks = b"".join(_field_bytes(1, _encode_schedule_entry(e)) for e in entries)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(11, tasks)
    return pb


def encode_delete_channel(hash_id: str) -> bytes:
    """Encode a delete-channel command (USER_CTRL_DELETE_CHANNEL).

    Mirrors the app: PbInput.map (f12) = PbMap { channels (f3) = [PbChannel{hashId}] }.
    """
    from .const import USER_CTRL_DELETE_CHANNEL

    channel = _field_str(1, hash_id)  # PbChannel.hashId = f1
    pb_map = _field_bytes(3, channel)  # PbMap.channels = f3
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_DELETE_CHANNEL)
    pb += _field_bytes(12, pb_map)
    return pb


def encode_start_zones(zone_hash_ids: list[str]) -> bytes:
    """Encode a start-zones command targeting specific zone hash IDs."""
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, 1)  # USER_CTRL_CLEAN

    map_pb = b""
    for i, hash_id in enumerate(zone_hash_ids, start=1):
        basic_info = _field_str(3, hash_id) + _field_i32(8, i)
        zone = _field_bytes(1, basic_info)
        map_pb += _field_bytes(1, zone)

    if map_pb:
        pb += _field_bytes(12, map_pb)

    return pb


# ---------------------------------------------------------------------------
# Map content encoders — mirror of the decode path above
# ---------------------------------------------------------------------------


def _encode_map_point(pt: dict) -> bytes:
    """Encode an x/y point sub-message (f1=x, f2=y, both wire-type-5 float32)."""
    return _field_f32(1, pt.get("x", 0.0)) + _field_f32(2, pt.get("y", 0.0))


def _encode_map_polygon(points: list) -> bytes:
    """Encode a polygon as repeated f1 point sub-messages."""
    out = b""
    for pt in points:
        out += _field_bytes(1, _encode_map_point(pt))
    return out


def _encode_go_zone(zone: dict) -> bytes:
    """Encode a GoZone sub-message (f1=BasicInfo, f2=ZoneConfig, f3=PpBasicInfo)."""
    # f1 = BasicInfo
    bi = _field_i32(1, zone.get("type", 0))
    bi += _field_str(3, zone.get("hashId", ""))
    bi += _field_i32(4, int(zone.get("isEnabled", True)))
    if zone.get("polygon"):
        bi += _field_bytes(5, _encode_map_polygon(zone["polygon"]))

    # f2 = ZoneConfig (optional)
    cfg = b""
    if "cutHeight" in zone:
        cfg += _field_i32(1, zone["cutHeight"])
    if "pathSpacing" in zone:
        cfg += _field_f32(4, zone["pathSpacing"])

    # f3 = PpBasicInfo (optional)
    pp = b""
    if "boundMin" in zone:
        pp += _field_bytes(1, _encode_map_point(zone["boundMin"]))
    if "boundMax" in zone:
        pp += _field_bytes(2, _encode_map_point(zone["boundMax"]))
    if "area" in zone:
        pp += _field_i32(3, zone["area"])
    if "innerPoint" in zone:
        pp += _field_bytes(5, _encode_map_point(zone["innerPoint"]))

    out = _field_bytes(1, bi)
    if cfg:
        out += _field_bytes(2, cfg)
    if pp:
        out += _field_bytes(3, pp)
    return out


def _encode_nogo_zone(nogo: dict) -> bytes:
    """Encode a NoGoZone sub-message (f1=BasicInfo, f3=PpBasicInfo, f4=parentZoneHashId)."""
    # f1 = BasicInfo
    bi = _field_i32(1, nogo.get("type", 0))
    bi += _field_str(3, nogo.get("hashId", ""))
    bi += _field_i32(4, int(nogo.get("isEnabled", True)))
    if nogo.get("polygon"):
        bi += _field_bytes(5, _encode_map_polygon(nogo["polygon"]))

    # f3 = PpBasicInfo (optional)
    pp = b""
    if "area" in nogo:
        pp += _field_i32(3, nogo["area"])
    if "innerPoint" in nogo:
        pp += _field_bytes(5, _encode_map_point(nogo["innerPoint"]))

    out = _field_bytes(1, bi)
    if pp:
        out += _field_bytes(3, pp)
    if nogo.get("parentZoneHashId"):
        out += _field_bytes(4, nogo["parentZoneHashId"].encode("utf-8"))
    return out


def _encode_map_content(map_data: dict) -> bytes:
    """Encode a PbMap sub-message.

    Field layout (confirmed from PbMap.encode Hermes bytecode analysis):
      f1  goZones (repeated), f2  nogoZones (repeated), f3  channels (repeated)
      f4  chargingStationLoc, f5  isIncomplete (bool), f6  diagonalCoords (repeated)
      f7  enuBasePoint (GPS origin), f8  taskConfig, f9  modifyHashs (repeated strings)
      f10 floorInfo, f11 globalZoneConfig, f12 globalChannelConfig, f13 runTimeConfig
    """
    out = b""
    for zone in map_data.get("goZones", []):
        out += _field_bytes(1, _encode_go_zone(zone))
    for nogo in map_data.get("nogoZones", []):
        out += _field_bytes(2, _encode_nogo_zone(nogo))
    cs = map_data.get("chargingStation")
    if cs:
        cs_bytes = (
            _field_f32(1, cs.get("x", 0.0)) + _field_f32(2, cs.get("y", 0.0)) + _field_f32(3, cs.get("theta", 0.0))
        )
        out += _field_bytes(4, cs_bytes)
    gps = map_data.get("gpsOrigin")
    if gps:
        gps_bytes = _field_f32(1, gps.get("lat", 0.0)) + _field_f32(2, gps.get("lon", 0.0))
        out += _field_bytes(7, gps_bytes)
    for hash_id in map_data.get("modifyHashs", []):
        out += _field_str(9, hash_id)
    return out


def encode_sync_map(map_data: dict) -> bytes:
    """Encode a sync-map command (userCtrl=USER_CTRL_SYNC_MAP=25).

    The map_data dict must have the same structure as returned by decode_map_response().
    PbInput.btMap (field 23) carries PbMap bytes directly — no extra wrapper.
    Confirmed from PbInput.encode Hermes bytecode: btMap=field23, PbMap fields=1-13.
    """
    from .const import USER_CTRL_SYNC_MAP

    content = _encode_map_content(map_data)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_SYNC_MAP)
    pb += _field_bytes(23, content)
    return pb


def encode_delete_zone(hash_id: str) -> bytes:
    """Encode a delete-goZone command using the correct robot protocol.

    Confirmed from Hermes bytecode analysis of deleteZonePartition (fn 8972):
      - userCtrl = USER_CTRL_CLEAR_ZONE = 8  (NOT SYNC_MAP=25)
      - map goes in PbInput field 12 (map), NOT field 23 (btMap)
      - PbMap contains ONLY the target zone (not the full map)
      - Structure: PbMap { goZones: [PbZone { basicInfo: PbZoneBasicInfo { hashId } }] }
    """
    from .const import USER_CTRL_CLEAR_ZONE

    # PbZoneBasicInfo { hashId: hash_id }  — field 3 confirmed from _encode_go_zone
    basic_info = _field_str(3, hash_id)
    # PbZone { basicInfo: PbZoneBasicInfo }  — basicInfo = field 1 confirmed from _encode_go_zone
    zone = _field_bytes(1, basic_info)
    # PbMap { goZones: [zone] }  — goZones = field 1 confirmed from _encode_map_content
    pb_map = _field_bytes(1, zone)
    # PbInput { version, userCtrl, map }  — map = field 12
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_CLEAR_ZONE)
    pb += _field_bytes(12, pb_map)
    return pb


def encode_delete_nogo_zone(hash_id: str) -> bytes:
    """Encode a delete-noGoZone command.

    From deleteZonePartition (fn 8972) type==1 branch: identical to the goZone
    delete (userCtrl=CLEAR_ZONE=8, PbZone{basicInfo{hashId}}) but placed in
    PbMap.nogoZones (field 2) instead of goZones (field 1). Hardware-validated;
    deleted no-go zones are recoverable by restoring a prior map backup.
    """
    from .const import USER_CTRL_CLEAR_ZONE

    basic_info = _field_str(3, hash_id)  # PbZoneBasicInfo { hashId }
    zone = _field_bytes(1, basic_info)  # PbZone { basicInfo }
    pb_map = _field_bytes(2, zone)  # PbMap { nogoZones: [PbZone] }
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_CLEAR_ZONE)
    pb += _field_bytes(12, pb_map)
    return pb


def encode_rename_zone(hash_id: str, name: str) -> bytes:
    """Encode a USER_CTRL_MODIFY_ZONE_INFO command renaming a go-zone.

    Same envelope as delete-zone (PbInput.map → PbMap.goZones → PbZone.basicInfo),
    but the basicInfo carries the new name. PbZoneBasicInfo.name = field 2
    (string) confirmed from APK/Hermes analysis of the zone encoder.
    """
    from .const import USER_CTRL_MODIFY_ZONE_INFO

    basic_info = _field_str(2, name) + _field_str(3, hash_id)
    zone = _field_bytes(1, basic_info)
    pb_map = _field_bytes(1, zone)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_MODIFY_ZONE_INFO)
    pb += _field_bytes(12, pb_map)
    return pb


def delete_zone(map_data: dict, hash_id: str) -> dict:
    """Return a deep copy of map_data with the given zone (and its child no-go zones) removed.

    Raises ValueError if hash_id is not found in goZones or nogoZones.
    When a goZone is deleted, all nogoZones whose parentZoneHashId matches it are also removed.
    """
    import copy

    all_ids = {z.get("hashId") for z in map_data.get("goZones", []) + map_data.get("nogoZones", [])}
    if hash_id not in all_ids:
        raise ValueError(f"Zone {hash_id!r} not found in map_data")

    result = copy.deepcopy(map_data)
    result["goZones"] = [z for z in result.get("goZones", []) if z.get("hashId") != hash_id]
    # Also cascade-delete no-go zones that belonged to the deleted go zone
    result["nogoZones"] = [
        n for n in result.get("nogoZones", []) if n.get("hashId") != hash_id and n.get("parentZoneHashId") != hash_id
    ]
    # Signal to the robot which zones changed (required for the robot to process the deletion)
    result["modifyHashs"] = [hash_id]
    return result


# ---------------------------------------------------------------------------
# BLE encoders — manual-drive commands sent directly over Bluetooth LE
# (not via MQTT; written to the drive characteristic on the robot)
# ---------------------------------------------------------------------------


def encode_ble_drive(linear: float, angular: float) -> bytes:
    """Encode a BLE manual-drive command payload.

    Returns ASCII bytes (base64-encoded protobuf) suitable for direct write to
    the BLE drive characteristic (UUID 12345678-1234-5678-1234-56789abcdef1,
    ATT handle 0x0014) using Write Without Response (ATT Write Command, 0x52).

    The robot samples this characteristic at ~10 Hz while the joystick is held.
    Sending a zero payload (linear=0, angular=0) stops the robot.

    Args:
        linear:  Forward/backward velocity in [-0.5, +0.5].
                 +0.5 = full forward, -0.5 = full backward.
        angular: Left/right angular velocity in [-0.6, +0.6].
                 +0.6 = full left turn (CCW), -0.6 = full right turn (CW).
                 (Confirmed from ADB capture: right-joystick LEFT = +0.6.)

    Returns:
        ASCII-encoded base64 bytes (24 bytes).  Send as the raw GATT value.

    Protocol (confirmed from HCI BTSnoop capture, 2025-05):
        Decoded 16-byte protobuf:
          field 2  (varint): PB_VERSION (49)
          field 7  (varint): 2  (message sub-type, constant)
          field 10 (bytes):  10-byte inner message:
            field 1 (float32 LE): linear velocity
            field 2 (float32 LE): angular velocity
        Outer payload = base64(protobuf), written as ASCII to the characteristic.
    """
    inner = _field_f32(1, linear) + _field_f32(2, angular)
    pb = _field_i32(2, PB_VERSION) + _field_i32(7, 2) + _field_bytes(10, inner)
    return base64.b64encode(pb)
