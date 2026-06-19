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

# Map content field numbers (PbMap, inside the double-wrapped f23→f2→f3 structure;
# canonical names taken from the Hermes PbMap class declaration #9660).
_MAP_CONTENT_GO_ZONES = 1
_MAP_CONTENT_NOGO_ZONES = 2
_MAP_CONTENT_CHANNELS = 3
_MAP_CONTENT_CHARGING_STATION = 4  # PbPose: x, y, theta, z
_MAP_CONTENT_IS_INCOMPLETE = 5
_MAP_CONTENT_DIAGONAL_COORDS = 6  # repeated PbPoint (2 corners of map bbox)
_MAP_CONTENT_ENU_BASE_POINT = 7  # PbRobotLLACoords: latitude, longitude, altitude
_MAP_CONTENT_TASK_CONFIG = 8  # PbTaskConfig (4-field: chargingMode, zoneOrder, rainCleaning, disableChargingPark)
_MAP_CONTENT_MODIFY_HASHS = 9
_MAP_CONTENT_FLOOR_INFO = 10
_MAP_CONTENT_GLOBAL_ZONE_CONFIG = 11  # PbZoneConfig (19 fields — the real mowing settings)
_MAP_CONTENT_GLOBAL_CHANNEL_CONFIG = 12  # PbChannelConfig (3 fields)
_MAP_CONTENT_RUN_TIME_CONFIG = 13

# Back-compat alias — older code/tests refer to f7 as the GPS origin.
_MAP_CONTENT_GPS_ORIGIN = _MAP_CONTENT_ENU_BASE_POINT


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
            name_raw = _first(bi, 2)
            poly_raw = _first(bi, 5)
            zone["hashId"] = hash_raw.decode("utf-8", errors="replace") if isinstance(hash_raw, bytes) else ""
            if isinstance(name_raw, bytes) and name_raw:
                zone["name"] = name_raw.decode("utf-8", errors="replace")
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
            cfg = decode_zone_config(cfg_raw)
            zone["zoneConfig"] = cfg
            # Surface the two most-asked-for fields directly on the zone so
            # existing callers keep working.
            if "cutHeight" in cfg:
                zone["cutHeight"] = cfg["cutHeight"]
            if "pathSpacing" in cfg:
                zone["pathSpacing"] = cfg["pathSpacing"]

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
            name_raw = _first(bi, 2)
            poly_raw = _first(bi, 5)
            nogo["hashId"] = hash_raw.decode("utf-8", errors="replace") if isinstance(hash_raw, bytes) else ""
            if isinstance(name_raw, bytes) and name_raw:
                nogo["name"] = name_raw.decode("utf-8", errors="replace")
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

    # ---- Charging station pose (f4) — PbPose: x/y/theta + optional z ----
    cs_raw = _first(content, _MAP_CONTENT_CHARGING_STATION)
    if isinstance(cs_raw, bytes):
        cs = _decode_fields(cs_raw)
        x_raw = _first(cs, 1)
        y_raw = _first(cs, 2)
        t_raw = _first(cs, 3)
        z_raw = _first(cs, 4)
        cs_out: dict[str, float] = {
            "x": _decode_f32(x_raw) if x_raw is not None else 0.0,
            "y": _decode_f32(y_raw) if y_raw is not None else 0.0,
            "theta": _decode_f32(t_raw) if t_raw is not None else 0.0,
        }
        if z_raw is not None:
            cs_out["z"] = _decode_f32(z_raw)
        result["chargingStation"] = cs_out

    # ---- Diagonal map corners (f6, repeated PbPoint) — 2 entries = map bbox.
    diag_pts: list[dict[str, float]] = []
    for d_raw in _all(content, _MAP_CONTENT_DIAGONAL_COORDS):
        if isinstance(d_raw, bytes):
            diag_pts.append(_decode_map_point(d_raw))
    if diag_pts:
        result["diagonalCoords"] = diag_pts

    # ---- enuBasePoint (f7) — PbRobotLLACoords: lat / lon / altitude -------
    enu_raw = _first(content, _MAP_CONTENT_ENU_BASE_POINT)
    if isinstance(enu_raw, bytes):
        gf = _decode_fields(enu_raw)
        lat_raw = _first(gf, 1)
        lon_raw = _first(gf, 2)
        alt_raw = _first(gf, 3)
        gps = {
            "lat": _decode_f32(lat_raw) if lat_raw is not None else 0.0,
            "lon": _decode_f32(lon_raw) if lon_raw is not None else 0.0,
        }
        if alt_raw is not None:
            gps["altitude"] = _decode_f32(alt_raw)
        result["gpsOrigin"] = gps
        result["enuBasePoint"] = gps

    # ---- Device-settings PbTaskConfig (f8) — chargingMode/zoneOrder/etc.
    tc_raw = _first(content, _MAP_CONTENT_TASK_CONFIG)
    if isinstance(tc_raw, bytes):
        result["taskConfig"] = decode_task_config(tc_raw)

    # ---- Global mowing settings (f11) — PbZoneConfig, the real source of
    # truth for cut height / move speed / path spacing / etc.
    gzc_raw = _first(content, _MAP_CONTENT_GLOBAL_ZONE_CONFIG)
    if isinstance(gzc_raw, bytes) and len(gzc_raw) > 0:
        result["globalZoneConfig"] = decode_zone_config(gzc_raw)

    # ---- Global channel settings (f12) — PbChannelConfig (3 fields).
    gcc_raw = _first(content, _MAP_CONTENT_GLOBAL_CHANNEL_CONFIG)
    if isinstance(gcc_raw, bytes) and len(gcc_raw) > 0:
        result["globalChannelConfig"] = decode_channel_config(gcc_raw)

    return result


def decode_task_config(data: bytes) -> dict[str, Any]:
    """Decode a PbTaskConfig sub-message (the four-field Device-Settings one).

    Field layout confirmed from PbTaskConfig.decode (Hermes fn #9592):
      f1 chargingMode (int32)       — 0 NORMAL / 1 QUICK
      f2 zoneOrder (int32)          — 0 OPTIMIZE / 1 CUSTOM
      f3 rainCleaning (bool)        — mow when raining
      f4 disableChargingPark (bool) — handbrake OFF in app's UI sense

    Booleans are accepted only as 0/1 — a varint of 2+ is dropped, not
    coerced to True, so a corrupted or hostile payload surfaces as unknown
    rather than silently flipping the switch on.

    This is the *same* PbTaskConfig written by ``encode_set_device_settings``;
    the 19-field PbZoneConfig record (mowing settings: cutHeight, moveSpeed,
    pathSpacing, …) is decoded by ``decode_zone_config``.
    """
    f = _decode_fields(data)
    out: dict[str, Any] = {}
    cm = _first(f, 1)
    if cm is not None:
        out["chargingMode"] = cm
    zo = _first(f, 2)
    if zo is not None:
        out["zoneOrder"] = zo
    rc = _first(f, 3)
    if rc in (0, 1):
        out["rainCleaning"] = bool(rc)
    dcp = _first(f, 4)
    if dcp in (0, 1):
        out["disableChargingPark"] = bool(dcp)
    return out


# PbZoneConfig wire layout — field numbers LIVE-CONFIRMED 2026-05-30 by
# correlating the app's labeled Mowing Settings to globalZoneConfig (PbMap.f11)
# wire values + toggle + re-query (see BRANCH_STATUS sections C/I/K/M). This is
# the *mowing-settings* record: globally on PbMap.f11 globalZoneConfig and
# per-zone as PbZone.f2 (zone-level override). Wire fields:
#   f1  cutHeight (int, mm)                  — confirmed (app 60mm)
#   f4  moveSpeed (float32, m/s)             — confirmed (app 0.6)
#   f6  cutSpeed (int)                       — anchored
#   f7  cleanMode (int)                      — anchored
#   f8  stripeAngle (signed int32)           — CONFIRMED live 2026-06-19: set
#       Stripe Angle "User-Defined 90°" → f8=90; "Optimized" → f8=-1 (the
#       10-byte sign-extended varint the app sends). NOT enabledZoneMask.
#   f9  pathSpacing (int, cm)                — CONFIRMED (app 35cm = f9)
#   f10 perimeterMowLaps (int)               — CONFIRMED (app Zone-Perimeter)
#   f11 perimeterMowDir (int)                — anchored
#   f12 noGoMowLaps (int)                    — CONFIRMED (app No-Go)
#   f13 obsDecMode (int, zone obstacle)      — anchored
#   f14 pathOrder/mowingOrder (bool)         — anchored
#   f15 startProgress (proto name; we leave it raw, =0)
#   f16 relativeCleanDir (int, stripe angle) — anchored (=90)
#   f17 safeMarginMode (bool, Offset=1/Precise=0) — CONFIRMED (toggle).
#       Proto name is `lineFollowMode` (APK, Hermes v96); we keep the UI-derived
#       name on purpose (per maintainer choice) — field NUMBER 17 is correct.
#   f18 turnOffOuterMotor (bool, ON=1)       — CONFIRMED (toggle).
#       Proto name is `disableOuterDischarge` (APK); UI-derived name kept; #18 correct.
#   f19 followDetectMode (int)               — anchored
# NOTE: the prior layout mislabeled f9 as relativeCleanDir / f10 as pathSpacing
# (a +1 shift). The full PbZoneConfig map is now APK-verified (Hermes v96):
# startProgress=f15, brushSpeed=f5 exist but we don't surface them (no HA use).
# raiseCutHeight/lowerCutHeight are momentary +/- commands, kept as-is. All
# field NUMBERS we DO use are confirmed correct (f8 stripeAngle live-confirmed).
_ZONE_CONFIG_BOOL_FIELDS = {14, 17, 18}
_ZONE_CONFIG_INT_NAMES: dict[int, str] = {
    1: "cutHeight",
    6: "cutSpeed",
    7: "cleanMode",
    9: "pathSpacing",
    10: "perimeterMowLaps",
    11: "perimeterMowDir",
    12: "noGoMowLaps",
    13: "obsDecMode",
    16: "relativeCleanDir",
    19: "followDetectMode",
}
_ZONE_CONFIG_BOOL_NAMES: dict[int, str] = {
    14: "pathOrder",
    17: "safeMarginMode",
    18: "turnOffOuterMotor",
}
# Signed int32 fields: stripeAngle is -1 for "Optimized" (auto), else 0-179°.
# Live-confirmed 2026-06-19 from a globalZoneConfig capture (f8 = -1 ⟺ app
# "Stripe Angle: Optimized").
_ZONE_CONFIG_SIGNED_NAMES: dict[int, str] = {
    8: "stripeAngle",
}


def decode_zone_config(data: bytes) -> dict[str, Any]:
    """Decode a PbZoneConfig sub-message (the 19-field mowing-settings record).

    Same shape is used for ``PbMap.f11 globalZoneConfig`` and the per-zone
    override at ``PbZone.f2``. Missing fields are simply absent from the
    returned dict — a value of None never appears.
    """
    out: dict[str, Any] = {}
    for fn, _wt, val in _decode_fields(data):
        if fn in _ZONE_CONFIG_BOOL_NAMES:
            if val in (0, 1):
                out[_ZONE_CONFIG_BOOL_NAMES[fn]] = bool(val)
            continue
        if fn == 4:  # moveSpeed (float32)
            out["moveSpeed"] = _decode_f32(val)
            continue
        if fn in _ZONE_CONFIG_SIGNED_NAMES and isinstance(val, int):
            out[_ZONE_CONFIG_SIGNED_NAMES[fn]] = _signed32(val)
            continue
        if fn in _ZONE_CONFIG_INT_NAMES and isinstance(val, int):
            out[_ZONE_CONFIG_INT_NAMES[fn]] = val
    return out


def decode_channel_config(data: bytes) -> dict[str, Any]:
    """Decode a PbChannelConfig sub-message (Hermes class #9444).

    Wire layout:
      f1 detectMode (int, oneOf)
      f2 cutHeight (int, mm)
      f3 channelLift (int)
    """
    f = _decode_fields(data)
    out: dict[str, Any] = {}
    dm = _first(f, 1)
    if isinstance(dm, int):
        out["detectMode"] = dm
    ch = _first(f, 2)
    if isinstance(ch, int):
        out["cutHeight"] = ch
    lift = _first(f, 3)
    if isinstance(lift, int):
        out["channelLift"] = lift
    return out


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
    # NOTE: f8 appears only on channels that carry per-channel overrides (next
    # to cutHeight/channelLift) and its value mirrors globalChannelConfig
    # detectMode in our captured frame — strong candidate for per-channel
    # Channel Obstacle Detection (gap 4). Surfaced raw (unlabeled) per the
    # NO-ASSUMPTIONS rule until a toggle capture confirms the semantics.
    f8 = _first(f, 8)
    if f8 is not None:
        chan["f8"] = f8
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

    # PbRobotInfo (field 5) — field map verified against PbRobotInfo.encode
    # (Hermes fn #9734 at offset 0x004b842c): f1 robotStatus, f2 battery,
    # f3 wifiSignalQuality, f4 lteSignalQuality, f5 btSignalQuality,
    # f6 workStatus, f7 isRecharging, f8 isCharging, f9 wifiWorking,
    # f10 lteWorking. All int32 / bool varints.
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
        # f5 btSignalQuality, f9 wifiWorking, f10 lteWorking — names APK-verified
        # (Hermes v96). The "working" flags say which link is currently active.
        bt_sig = _first(ri, 5)
        if bt_sig is not None:
            state["btSignalQuality"] = _signed32(bt_sig)
        wifi_working = _first(ri, 9)
        if wifi_working is not None:
            state["wifiWorking"] = bool(wifi_working)
        lte_working = _first(ri, 10)
        if lte_working is not None:
            state["lteWorking"] = bool(lte_working)

    # PbDeviceProfile (field 10). f3 softwareVersion ("v2.1.48.1") live-confirmed
    # 2026-05-30 (matches REST get-device-info.softwareVersion). f10 wheelVer /
    # f11 knifeVer are hardware-component version strings (diagnostic). f4 wifiSsid,
    # f8 rtkBaseId, f9 simIccid are also present here but deliberately NOT
    # surfaced — they are sensitive identifiers (see security rules).
    profile_raw = _first(fields, 10)
    if isinstance(profile_raw, bytes):
        dp = _decode_fields(profile_raw)
        for field_no, key in (
            (1, "fwVersion"),
            (2, "mcuVersion"),
            (3, "softwareVersion"),
            (5, "ipAddress"),
            (6, "macAddress"),
            (7, "sn"),
            (10, "wheelVer"),
            (11, "knifeVer"),
        ):
            val = _first(dp, field_no)
            if isinstance(val, bytes):
                state[key] = val.decode("utf-8", errors="replace")

    # Advanced RTK / localization diagnostic — carried in iotCmd (field 8, per
    # the APK PbOutput map): {f1: type, f2: JSON}.
    # LIVE-CONFIRMED 2026-05-30 — the "Advanced Diagnostics (Technical Support)"
    # blob. The RTK base sends corrections over LoRa, so the most actionable
    # fields are the fix quality, differential-correction age, position precision
    # and the error reason (e.g. ERTK_LORA_DATA_ERROR_RATE — a noisy LoRa link is
    # what degrades accuracy). The per-error positions (x/y/z) are not surfaced.
    diag_raw = _first(fields, 8)
    if isinstance(diag_raw, bytes) and diag_raw:
        js = _first(_decode_fields(diag_raw), 2)
        if isinstance(js, bytes):
            try:
                d = json.loads(js.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                d = None
            if isinstance(d, dict):
                adv: dict[str, Any] = {}
                if isinstance(d.get("precision"), (int, float)):
                    adv["precisionM"] = round(float(d["precision"]), 4)
                if isinstance(d.get("quality"), int):
                    adv["quality"] = d["quality"]
                if isinstance(d.get("diff_age"), (int, float)):
                    adv["diffAgeS"] = float(d["diff_age"])
                if d.get("primary_error_desc"):
                    adv["primaryError"] = str(d["primary_error_desc"])
                errs = d.get("error_desc_list")
                if isinstance(errs, list) and errs:
                    adv["errors"] = [str(e) for e in errs]
                if adv:
                    state["rtkDiagnostic"] = adv

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
    #   f1=missionTimeMin(int — elapsed mission/cleaning time in MINUTES; this is
    #   the app's "Mission time". LIVE-CONFIRMED 2026-05-30: matched the app's
    #   Mission-time across a 46-min run, 1:1. Was previously mislabeled
    #   "mowStripCount".), f2=totalTaskArea(float32, the current task's
    #   total area — denominator for mowProgress), f3=currentTaskZone
    #   (PbZoneBasicInfo, hashId=f2 — which go-zone is being mowed now; present in
    #   the QUERY_PATH reply during an active task), f5=mowProgress(float32 0–1).
    #   NOTE: the QUERY_PATH reply we captured carried only this task/progress
    #   summary. The coverage-path geometry DOES exist as PbOutput.path
    #   (PbPath = {poses[], cleanFinishedZones[]}, per APK) but wasn't populated
    #   in our reply — decoding it is an open gap. The live trail can also be
    #   built from the robot pose (field 14) accumulated over time.
    area_raw = _first(fields, 12)
    if isinstance(area_raw, bytes):
        area_fields = _decode_fields(area_raw)
        total_area = _first(area_fields, 2)
        if total_area is not None:
            state["totalTaskAreaM2"] = _decode_f32(total_area)
        mission_min = _first(area_fields, 1)
        if mission_min is not None:
            state["missionTimeMin"] = _signed32(mission_min)
        # Bound the decoded fraction so a NaN/inf from a misaligned/corrupt
        # payload can't surface as a garbage sensor state.
        progress_raw = _first(area_fields, 5)
        if isinstance(progress_raw, int):
            pct = _decode_f32(progress_raw)
            if 0.0 <= pct <= 1.0:
                state["mowProgress"] = round(pct * 100, 1)
        task_zone_raw = _first(area_fields, 3)
        if isinstance(task_zone_raw, bytes):
            hash_raw = _first(_decode_fields(task_zone_raw), 2)
            if isinstance(hash_raw, bytes):
                state["currentTaskZoneHashId"] = hash_raw.decode("utf-8", errors="replace")
        remain_raw = _first(area_fields, 4)
        if remain_raw is not None:
            state["remainCleanTimeSec"] = _signed32(remain_raw)
        map_area_raw = _first(area_fields, 6)
        if map_area_raw is not None:
            state["mapAreaM2"] = _decode_f32(map_area_raw)

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

    # Coverage path (field 13 = PbPath {poses: repeated PbPose, cleanFinishedZones}).
    # Field names APK-verified (Hermes v96); each PbPose is {f1 east, f2 north,
    # f3 theta} float32 (same shape as the live pose). Surfaced as a list the map
    # card can draw. Empty/absent in normal heartbeats — populated on demand.
    path_raw = _first(fields, 13)
    if isinstance(path_raw, bytes) and path_raw:
        poses: list[dict[str, float]] = []
        for praw in _all(_decode_fields(path_raw), 1):
            if isinstance(praw, bytes):
                pf = _decode_fields(praw)
                e, n = _first(pf, 1), _first(pf, 2)
                if isinstance(e, int) and isinstance(n, int):
                    poses.append({"east": _decode_f32(e), "north": _decode_f32(n)})
        if poses:
            state["coveragePoses"] = poses
    # Live charging-station pose (PbOutput field 24 = PbPose — same sub-message
    # type as f14, per PbOutput.encode in the APK). The map-query path already
    # decodes the dock under ``mapData.chargingStation`` as ``{x, y, theta}``;
    # this is the LIVE update channel — pushed whenever the dock moves or is
    # re-detected without re-querying the full map. Surfaced under
    # ``chargingStationLoc`` (top-level state, same ``{x, y, theta}`` shape as
    # the map-derived entry) so a card can pick whichever is fresher.
    dock_raw = _first(fields, 24)
    if isinstance(dock_raw, bytes):
        dock_fields = _decode_fields(dock_raw)
        d_east = _first(dock_fields, 1)
        d_north = _first(dock_fields, 2)
        d_theta = _first(dock_fields, 3)
        dock: dict[str, float] = {}
        # f1/f2/f3 are wire-type 5 (fixed32) per PbPose.encode, so ``_first``
        # should return an int — but the wire is untrusted, so a malformed
        # payload could send the same field number with a length-delimited
        # wire type and surface bytes here. ``_decode_f32`` would then raise
        # on ``struct.pack`` of bytes; explicit ``isinstance(int)`` keeps
        # the decoder robust to wire-type drift.
        if isinstance(d_east, int):
            dock["x"] = _decode_f32(d_east)
        if isinstance(d_north, int):
            dock["y"] = _decode_f32(d_north)
        if isinstance(d_theta, int):
            dock["theta"] = _decode_f32(d_theta)
        if dock:
            state["chargingStationLoc"] = dock

    # Mowing schedules: PbOutput field 16 = PbSchedules { tasks(1) = [PbSchedule] }.
    # The QUERY_SCHEDULES reply carries the full list (verified against a live
    # capture of the app — the input and output PbSchedule are the same message).
    schedules_raw = _first(fields, 16)
    if isinstance(schedules_raw, bytes):
        tasks = _all(_decode_fields(schedules_raw), 1)
        state["schedules"] = [decode_schedule_entry(t) for t in tasks if isinstance(t, bytes)]

    # Clean report (PbOutput field 28 = PbCleanReport) — the robot pushes this at
    # the end of a mow. Field semantics confirmed 2026-05-31 by cross-checking 5
    # captured reports against the REST get-clean-history record (same numbers):
    #   f1 date(epoch s)  f2 PbCleanSummary {1 cleanTimeMin, 2 cleanAreaM2(f32),
    #   3 {2: mapHashId}, 5 percent(f32 0–1), 6 mapTotalAreaM2(f32)}
    #   f3 startType  f4 [error_list {1 code, 2 percent(f32)}]  f6 usedBatteryPct
    clean_raw = _first(fields, 28)
    if isinstance(clean_raw, bytes):
        cr = _decode_fields(clean_raw)
        report: dict[str, Any] = {}
        date_raw = _first(cr, 1)
        if date_raw is not None:
            report["date"] = _signed32(date_raw)
        summary_raw = _first(cr, 2)
        if isinstance(summary_raw, bytes):
            sm = _decode_fields(summary_raw)
            time_min = _first(sm, 1)
            if time_min is not None:
                report["cleanTimeMin"] = _signed32(time_min)
            area = _first(sm, 2)
            if area is not None:
                report["cleanAreaM2"] = round(_decode_f32(area), 2)
            percent = _first(sm, 5)
            if percent is not None:
                report["percent"] = round(_decode_f32(percent) * 100, 1)
            total_area = _first(sm, 6)
            if total_area is not None:
                report["mapTotalAreaM2"] = round(_decode_f32(total_area), 2)
            map_id_raw = _first(sm, 3)
            if isinstance(map_id_raw, bytes):
                hid = _first(_decode_fields(map_id_raw), 2)
                if isinstance(hid, bytes):
                    report["mapHashId"] = hid.decode("utf-8", errors="replace")
        start_type = _first(cr, 3)
        if start_type is not None:
            report["startType"] = _signed32(start_type)
        used_battery = _first(cr, 6)
        if used_battery is not None:
            report["usedBatteryPct"] = _signed32(used_battery)
        errors: list[dict[str, Any]] = []
        for eraw in _all(cr, 4):
            if isinstance(eraw, bytes):
                ef = _decode_fields(eraw)
                code = _first(ef, 1)
                if code is not None:
                    entry: dict[str, Any] = {"code": _signed32(code)}
                    epct = _first(ef, 2)
                    if epct is not None:
                        entry["percent"] = round(_decode_f32(epct) * 100, 1)
                    errors.append(entry)
        if errors:
            report["errorList"] = errors
        if report:
            state["cleanReport"] = report

    # Robot config (PbOutput field 17 = PbRobotConfig — from PbOutput.encode tag
    # 138 = (17<<3)|2). Carries the device-settings the app shows on its
    # Settings/Network screens. Each field is optional in the reply; we surface
    # only what's present so a partial response doesn't blow away existing state.
    robot_config_raw = _first(fields, 17)
    if isinstance(robot_config_raw, bytes):
        state["robotConfig"] = decode_robot_config(robot_config_raw)

    # ---- Network info (PbOutput field 34) — populated by USER_CTRL_QUERY_RTK_
    # DIAGNOSTIC_L1 (#57). Live capture 2026-05-27 against docked robot:
    #   f1 connState   varint
    #   f2 wifiSsid    bytes (UTF-8)
    #   f3 ipAddress   bytes ("192.168.1.85")
    #   f4 wifiRssiDbm varint (negative)
    #   f5 wifiState   varint
    #   f6 cellularIp  bytes ("100.116.126.140")  — Tailscale / 4G-NAT'd IPv4
    #   f7 lteRssiDbm  varint (negative)
    #   f8 cellularState varint
    #   f9 ?           varint (1)
    #   f10 simId      bytes (e.g. " 89320420000094505458")
    net_raw = _first(fields, 34)
    if isinstance(net_raw, bytes):
        nf = _decode_fields(net_raw)
        net: dict[str, Any] = {}
        for fno, key in ((2, "wifiSsid"), (3, "ipAddress"), (6, "cellularIp"), (10, "simId")):
            v = _first(nf, fno)
            if isinstance(v, bytes):
                net[key] = v.decode("utf-8", errors="replace").strip()
        for fno, key in (
            (4, "wifiRssiDbm"),
            (7, "lteRssiDbm"),
            (1, "connState"),
            (5, "wifiState"),
            (8, "cellularState"),
        ):
            v = _first(nf, fno)
            if v is not None:
                net[key] = _signed32(v)
        if net:
            state["networkInfo"] = net

    # ---- RTK diagnostic L1 (PbOutput field 35) — populated by USER_CTRL_
    # QUERY_RTK_DIAGNOSTIC_L1 (#57). Field labels cross-referenced live
    # 2026-05-27 against the app's Settings → RTK Diagnostic UI.
    l1_raw = _first(fields, 35)
    if isinstance(l1_raw, bytes):
        lf = _decode_fields(l1_raw)
        l1: dict[str, Any] = {}
        for fno, _wt, v in lf:
            label = _RTK_L1_LABELS.get(fno)
            if label is None:
                continue
            if _wt == 5 and isinstance(v, int):
                l1[label] = round(_decode_f32(v), 4)
            elif isinstance(v, int):
                l1[label] = _signed32(v)
        if l1:
            state["rtkL1"] = l1

    # ---- RTK diagnostic L2 (PbOutput field 36) — populated by USER_CTRL_
    # QUERY_RTK_DIAGNOSTIC_L2 (#58). Labels per the same live correlation.
    l2_raw = _first(fields, 36)
    if isinstance(l2_raw, bytes):
        lf = _decode_fields(l2_raw)
        l2: dict[str, Any] = {}
        for fno, _wt, v in lf:
            label = _RTK_L2_LABELS.get(fno)
            if label is None:
                continue
            if _wt == 5 and isinstance(v, int):
                l2[label] = round(_decode_f32(v), 4)
            elif isinstance(v, int):
                l2[label] = _signed32(v)
        if l2:
            state["rtkL2"] = l2

    # Anti-theft lock live state (PbOutput field 27 = bool — from PbOutput.encode
    # tag 216 = (27<<3)|0 writing writer.bool). Surfaced under a distinct key
    # from the REST ``theftLock`` (which is the user-set feature toggle written
    # by ``TheftLockSwitch`` via /update-device-feature). PbOutput.f27 is the
    # robot's report of whether the lock is *currently engaged*; the REST flag
    # is whether the feature is *enabled* — both must coexist without one
    # overwriting the other when MQTT updates land between REST polls.
    theft_lock = _first(fields, 27)
    if isinstance(theft_lock, int):
        state["theftLockEngaged"] = bool(theft_lock)

    # Camera-lens heater fire count (PbOutput.f37 = uint32 — per PbOutput.encode
    # tag 296 = (37<<3)|0 writing ``writer.uint32``). The lens heater fires when
    # the camera detects condensation / fog. A monotonically-increasing counter
    # — useful as a maintenance metric ("the heater has fired N times this
    # install") and as a coarse weather/condition indicator.
    #
    # ``_decode_varint`` always returns an unsigned int, so a sign-extended
    # int32 (e.g. -1 encoded as a 10-byte varint = 0xFFFFFFFFFFFFFFFF) would
    # surface as a 4-billion+ counter. Interpret through ``_signed32`` first
    # so the unsigned wrap-around becomes a negative int (which we then
    # reject), and cap at int32-max to keep the counter in a sensor-friendly
    # range.
    heated_lens = _first(fields, 37)
    if isinstance(heated_lens, int):
        signed = _signed32(heated_lens)
        if 0 <= signed <= 2_147_483_647:
            state["heatedLensTimes"] = signed

    # Camera auto-exposure gear (PbOutput.f38 = int32 enum, tag 304 =
    # (38<<3)|0 writing ``writer.int32``). Values 0..7 per PbOutput.verify
    # (Hermes fn #9066) — labels extracted from PbOutput.fromObject
    # (Hermes fn #9067) around the aeRangeLevel name-and-value branch.
    # The label is surfaced (not the raw int) so a plain LymowSensor reads
    # it directly — out-of-range wire data drops the key rather than
    # rendering a phantom "AE Gear 42".
    ae_raw = _first(fields, 38)
    if isinstance(ae_raw, int):
        from .const import AE_RANGE_LEVELS

        signed = _signed32(ae_raw)
        if signed in AE_RANGE_LEVELS:
            state["aeRangeLevel"] = AE_RANGE_LEVELS[signed]

    # outputCtrl (PbOutput.f18 = varint enum, tag 144 = (18<<3)|0 with
    # ``writer.uint32``). Tells you what the robot is replying to — e.g. a
    # pboutput with outputCtrl=QUERY_MAP is the answer to a userCtrl=19
    # query. Surfaced as the label string via OUTPUT_CTRLS so a plain
    # LymowSensor reads it directly. An unknown code (firmware drift)
    # drops the key rather than rendering a phantom label.
    out_ctrl = _first(fields, 18)
    if isinstance(out_ctrl, int):
        from .const import OUTPUT_CTRLS

        signed = _signed32(out_ctrl)
        if signed in OUTPUT_CTRLS:
            state["outputCtrl"] = OUTPUT_CTRLS[signed]

    return state


# RTK diagnostic field labels — correlated live 2026-05-27 against the app's
# Settings → RTK Diagnostic page (basic + Advanced Diagnostics). Field
# numbers come from the wire capture; semantic names come from the UI labels
# that displayed the same values seconds before/after the capture.
_RTK_L1_LABELS: dict[int, str] = {
    2: "locationPrecisionM",
    3: "gnssSatellites",
    4: "l1SatCount",
    5: "l2SatCount",
    6: "l5SatCount",
    7: "l1SnrMedian",
    8: "l2SnrMedian",
    9: "l5SnrMedian",
    10: "dataErrorRatePct",
}
_RTK_L2_LABELS: dict[int, str] = {
    1: "differentialAgeSec",
    2: "loraBandwidthL1Bps",
    3: "loraBandwidthL2Bps",
    4: "loraBandwidthL5Bps",
    5: "hwDcVoltageL1V",
    6: "hwDcVoltageL2V",
    7: "hwDcVoltageL5V",
    8: "cwInterferenceL1",
    9: "cwInterferenceL2",
    10: "cwInterferenceL5",
    11: "antennaGainL1",
    12: "antennaGainL2",
    13: "antennaGainL5",
}


def decode_robot_config(data: bytes) -> dict[str, Any]:
    """Decode a PbRobotConfig sub-message into a flat dict.

    Field map from PbRobotConfig.encode (Hermes fn #9506 at offset 0x004a7ce8):
    f2 rcCutSpeed int, f3 rcCutHeight int, f4 rcRaiseCutHeight bool,
    f5 rcLowerCutHeight bool, f6 audioVolume int, f7 isOpenLed bool,
    f8 signal int, f9 lcdPinCode submessage {f1: 4 digit-bytes} → lcdPin
    (decoded for an opt-in diagnostic sensor; never logged — PIN is sensitive),
    f10 cmdCellularSwitch bool, f11 metric_4g bool,
    f14 headlightStart / f15 headlightEnd PbTimeZone {hour, minute} UTC,
    f18 rrConfig PbRRConfig,
    f21 timezoneOffset int (seconds east of UTC, what setTimezone #9036 writes),
    f22 dockOnError bool.

    Untrusted wire data: only fields we read are decoded; unknown values are
    left absent rather than coerced.
    """
    f = _decode_fields(data)
    out: dict[str, Any] = {}
    for field_no, name in (
        (6, "audioVolume"),
        (8, "signal"),
        (21, "timezoneOffset"),
    ):
        v = _first(f, field_no)
        if v is not None:
            out[name] = _signed32(v)
    for field_no, name in (
        (4, "rcRaiseCutHeight"),
        (5, "rcLowerCutHeight"),
        (7, "isOpenLed"),
        (10, "cmdCellularSwitch"),
        (11, "metric_4g"),
        (22, "dockOnError"),
    ):
        v = _first(f, field_no)
        if v is not None:
            out[name] = bool(v)
    for field_no, name in ((14, "headlightStart"), (15, "headlightEnd")):
        raw = _first(f, field_no)
        if isinstance(raw, bytes):
            sub = _decode_fields(raw)
            hour = _first(sub, 1)
            minute = _first(sub, 2)
            if isinstance(hour, int) and isinstance(minute, int) and 0 <= hour <= 23 and 0 <= minute <= 59:
                out[name] = {"hour": hour, "minute": minute}
    rr_raw = _first(f, 18)
    if isinstance(rr_raw, bytes):
        rr = decode_rr_config(rr_raw)
        if rr:
            out["rrConfig"] = rr
    # f9 lcdPinCode: submessage {f1: 4 bytes, one digit (0-9) per byte}. The
    # 4-digit screen-unlock PIN. Surfaced only via a disabled-by-default
    # diagnostic sensor; never logged (security rule: PIN is sensitive).
    pin_raw = _first(f, 9)
    if isinstance(pin_raw, bytes):
        pin_sub = _decode_fields(pin_raw)
        digits = _first(pin_sub, 1)
        if isinstance(digits, bytes) and len(digits) == 4 and all(0 <= b <= 9 for b in digits):
            out["lcdPin"] = "".join(str(b) for b in digits)
    return out


def decode_rr_config(data: bytes) -> dict[str, Any]:
    """Decode a PbRRConfig (Recharge & Resume) sub-message.

    Field layout from PbRRConfig.encode (Hermes fn #9494 at offset 0x004a6f9b);
    mirrors :func:`encode_set_recharge_resume`:
      f1 enableRr (bool) — only 0/1 accepted to avoid hostile non-zero ints
                           silently flipping the switch on.
      f2 resumePeriodStart PbTimeZone {f1 hour, f2 minute}
      f3 resumePeriodEnd   PbTimeZone {f1 hour, f2 minute}
      f4 rechargeBat int32 — battery % at which the mower returns to dock
      f5 resumeBat   int32 — battery % at which the mower resumes after charging

    Battery percentages are bounded to 0-100 and hour/minute to 0-23 / 0-59;
    anything outside the wire's documented range is dropped rather than
    surfaced as garbage HA state.
    """
    f = _decode_fields(data)
    out: dict[str, Any] = {}
    enable = _first(f, 1)
    if enable in (0, 1):
        out["enable"] = bool(enable)
    for field_no, name in ((2, "periodStart"), (3, "periodEnd")):
        raw = _first(f, field_no)
        if isinstance(raw, bytes):
            sub = _decode_fields(raw)
            hour = _first(sub, 1)
            minute = _first(sub, 2)
            if isinstance(hour, int) and isinstance(minute, int) and 0 <= hour <= 23 and 0 <= minute <= 59:
                out[name] = {"hour": hour, "minute": minute}
    for field_no, name in ((4, "rechargeBat"), (5, "resumeBat")):
        v = _first(f, field_no)
        if isinstance(v, int) and 0 <= v <= 100:
            out[name] = v
    return out


def decode_clean_report(data: bytes) -> dict[str, Any]:
    """Decode a PbCleanReport sub-message — the QUERY_CLEANING_SUMMARY reply.

    Field map from PbCleanReport.encode (Hermes fn #9794):
      f1 cleanStartTime (varint, unix seconds — Long on the wire),
      f2 cleanInfo PbCleanInfo (skipped here — already decoded for live session),
      f3 mowEndType enum (0=MOW_END_NONE, 1=MOW_END_100, 2=MOW_END_USER_CANCEL),
      f4 errorList repeated PbErrorList — each entry surfaces as
                  ``{code: int, percent: int 0-100}``. On the wire f2 is a
                  float32 fraction (0..1) per PbErrorList.encode #9782; the
                  decoder scales it to 0..100 so the attribute matches
                  ``mowProgress``.
      f5 statusTimes packed repeated int32 — seconds spent in each workStatus,
                     indexed by the enum value (array[i] = seconds at status i),
      f6 usedBattery (varint int32, percent).

    Only present-fields surface so a partial payload doesn't clobber state.
    """
    f = _decode_fields(data)
    out: dict[str, Any] = {}
    start = _first(f, 1)
    # cleanStartTime is a Long on the wire — a malformed (or sign-extended)
    # huge varint could overflow ``datetime.fromtimestamp`` downstream, so
    # cap at the POSIX-portable int32 epoch ceiling (year 2038). Anything
    # beyond it is almost certainly garbage from a misaligned decode.
    if isinstance(start, int) and 0 < start <= 2_147_483_647:
        out["cleanStartTime"] = start
    end_type = _first(f, 3)
    if isinstance(end_type, int) and 0 <= end_type <= 2:
        out["mowEndType"] = end_type
    # PbErrorList entries (f4): non-packed repeated sub-messages — every
    # occurrence is its own length-delimited segment.
    errors = [_decode_error_list_entry(seg) for seg in _all(f, 4) if isinstance(seg, bytes)]
    errors = [e for e in errors if e]
    if errors:
        out["errorList"] = errors
    # Concatenate every f5 segment before unpacking — protobuf permits a
    # packed-repeated field to be split across multiple key/value occurrences,
    # which decoders must rejoin (using ``_first`` would drop later segments).
    status_times_segments = [seg for seg in _all(f, 5) if isinstance(seg, bytes) and seg]
    if status_times_segments:
        decoded = _decode_packed_int32s(b"".join(status_times_segments))
        # Clamp each entry to [0, one year of seconds] so a misaligned or
        # sign-extended varint can't surface a wildly large duration — but
        # preserve positional semantics: statusTimes[i] still maps to
        # workStatus == i, so we clamp rather than drop.
        out["statusTimes"] = [max(0, min(s, 31_536_000)) for s in decoded]
    used = _first(f, 6)
    if isinstance(used, int) and 0 <= used <= 100:
        out["usedBattery"] = used
    return out


def _decode_error_list_entry(data: bytes) -> dict[str, Any]:
    """Decode a PbErrorList sub-message — wire from PbErrorList.encode #9782.

    Fields: f1 code (varint int32), f2 percent (float32 — session progress
    at which the error fired). Both are optional; an entry with neither
    surfaces as ``{}`` which the caller drops.
    """
    f = _decode_fields(data)
    out: dict[str, Any] = {}
    code = _first(f, 1)
    if isinstance(code, int):
        out["code"] = _signed32(code)
    # f2 is wire-type 5 (fixed32) per the encoder, so ``_first`` should
    # return an int — but the wire is untrusted, so a malformed payload
    # could send the same field number with a length-delimited wire type
    # and surface bytes here. ``_decode_f32`` would then raise on
    # ``struct.pack`` of bytes; explicit isinstance(int) keeps the decoder
    # robust to wire-type drift.
    pct_raw = _first(f, 2)
    if isinstance(pct_raw, int):
        pct = _decode_f32(pct_raw)
        # Wire is fraction 0..1 mirroring PbCleanInfo.mowProgress; bound it
        # so a misaligned float can't surface a -inf / NaN attribute.
        if 0.0 <= pct <= 1.0:
            out["percent"] = round(pct * 100, 1)
    return out


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


def decode_path_response(pb_bytes: bytes) -> dict[str, Any]:
    """Decode a userCtrl=23 (QUERY_PATH) response into per-zone track polylines.

    Returns {"goZones": [{"hashId": str, "trackPoints": [{"x": float, "y": float}], "stripsDone": int}]}
    The track points are ENU metres from the RTK base station, same coordinate
    space as the zone polygons returned by decode_map_response.

    Wire layout (confirmed from live capture while robot is mowing):
      pboutput.f23 → f2 → f3 → repeated f1 (go-zone path entries)
      Each f1 entry:
        .f1 sub-message: .f3=hashId(str), .f5 repeated PbPoint sub-messages
          Each PbPoint: f1=x(float32), f2=y(float32)
        .f2 sub-message: .f1=stripsDone(int)
    """
    outer = _first(_decode_fields(pb_bytes), 23)
    if not isinstance(outer, bytes):
        return {"goZones": []}
    content = _first(_decode_fields(outer), 2)
    if not isinstance(content, bytes):
        return {"goZones": []}
    path_data = _first(_decode_fields(content), 3)
    if not isinstance(path_data, bytes):
        return {"goZones": []}

    go_zones: list[dict[str, Any]] = []
    for gz_raw in _all(_decode_fields(path_data), 1):
        if not isinstance(gz_raw, bytes):
            continue
        zone_info = _first(_decode_fields(gz_raw), 1)
        hash_id: str | None = None
        track_points: list[dict[str, float]] = []
        if isinstance(zone_info, bytes):
            for fn, _wt, val in _decode_fields(zone_info):
                if fn == 3 and isinstance(val, bytes):
                    hash_id = val.decode("utf-8", errors="replace")
                elif fn == 5 and isinstance(val, bytes):
                    for pfn, _pwt, pval in _decode_fields(val):
                        if pfn == 1 and isinstance(pval, bytes):
                            pt = _decode_map_point(pval)
                            if pt["x"] is not None and pt["y"] is not None:
                                track_points.append(pt)
        if hash_id is None:
            continue
        strips_done: int | None = None
        stats = _first(_decode_fields(gz_raw), 2)
        if isinstance(stats, bytes):
            sv = _first(_decode_fields(stats), 1)
            if sv is not None:
                strips_done = int(sv)
        entry: dict[str, Any] = {"hashId": hash_id, "trackPoints": track_points}
        if strips_done is not None:
            entry["stripsDone"] = strips_done
        go_zones.append(entry)
    return {"goZones": go_zones}


# ---------------------------------------------------------------------------
# PbInput encoders — commands sent to the robot
# ---------------------------------------------------------------------------


def encode_userctrl(command: int) -> bytes:
    """Encode a simple user control command."""
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, command)
    return pb


# PbZoneConfig field map — (proto field number, wire kind). Field numbers
# LIVE-CONFIRMED 2026-05-30 (BRANCH_STATUS C/I/K/M): pathSpacing=f9,
# perimeterMowLaps=f10, noGoMowLaps=f12, safeMarginMode=f17,
# turnOffOuterMotor=f18 verified by app-label correlation + toggle/re-query;
# the rest are anchored by the confirmed positions. Must stay in sync with the
# decoder maps (`_ZONE_CONFIG_INT_NAMES`/`_BOOL_NAMES`). Used by the per-zone
# userCtrl=9 write (`encode_set_zone_config`) and the sync_map round-trip
# (`_encode_go_zone`); both build a PbZoneConfig sub-message from these numbers.
# Not surfaced (no HA use), names per the APK-verified map (Hermes v96):
# brushSpeed=f5, cleanDir=f8, startProgress=f15. Our f17/f18 use UI-derived
# names (safeMarginMode/turnOffOuterMotor); proto names are lineFollowMode/
# disableOuterDischarge — numbers correct, names kept by choice. raiseCutHeight/
# lowerCutHeight (f2/f3) are momentary +/- commands, kept as-is.
_TASK_CONFIG_FIELDS: dict[str, tuple[int, str]] = {
    "cutHeight": (1, "int"),
    "raiseCutHeight": (2, "bool"),
    "lowerCutHeight": (3, "bool"),
    "moveSpeed": (4, "float"),
    "brushSpeed": (5, "int"),
    "cutSpeed": (6, "int"),
    "cleanMode": (7, "int"),
    "stripeAngle": (8, "int"),  # signed: -1 = Optimized (auto), else 0-179°
    "pathSpacing": (9, "int"),
    "perimeterMowLaps": (10, "int"),
    "perimeterMowDir": (11, "int"),
    "noGoMowLaps": (12, "int"),
    "obsDecMode": (13, "int"),
    "pathOrder": (14, "bool"),
    "relativeCleanDir": (16, "int"),
    "safeMarginMode": (17, "bool"),
    "turnOffOuterMotor": (18, "bool"),
    "followDetectMode": (19, "int"),
}

# Global channel settings — PbChannelConfig carried at PbMap.f12 (alongside the
# PbMap.f11 globalZoneConfig). The app's global Save sends BOTH; field map
# live-confirmed 2026-06-19 from a captured global write (f1=2 detectMode Smart,
# f2=60 deck height, f3=0 raise-omni). detectMode: Smart=2 / Touch-Only=1.
_CHANNEL_CONFIG_FIELDS: dict[str, tuple[int, str]] = {
    "channelDetectMode": (1, "int"),  # obstacle detection crossing a channel (Smart=2/Touch=1)
    "channelDeckHeight": (2, "int"),  # deck module height when crossing a channel (mm)
    "channelRaiseOmni": (3, "bool"),  # raise the omni wheels on channel
}


def encode_set_task_config(**fields: Any) -> bytes:
    """Encode a global mowing-settings write setting only the given fields.

    Field names match PbZoneConfig (see ``_TASK_CONFIG_FIELDS``); ``None``
    values are skipped so only explicitly-set parameters are sent. Unknown
    field names raise ValueError.

    Envelope LIVE-CONFIRMED 2026-05-30 from the app's Mowing Settings → Global
    tab "Save → Keep Custom": ``PbInput {f2:49, f5:49(GLOBAL_SETTING_N), f12
    (PbMap):{f11: globalZoneConfig}}`` — userCtrl **49** ("Keep Custom": apply
    the global mowing settings while preserving per-zone customs), NOT the old
    userCtrl=36+PbTaskConfig(f26) which is the unrelated Device Settings page.
    The robot merges the partial globalZoneConfig (same as the per-zone
    userCtrl=9 path). Channel fields (``_CHANNEL_CONFIG_FIELDS``) ride alongside
    in the sibling PbMap.f12 globalChannelConfig — exactly as the app's Save sends
    both snapshots together.
    """

    def _encode(field_no: int, kind: str, value: Any) -> bytes:
        if kind == "bool":
            return _field_i32(field_no, 1 if value else 0)
        if kind == "float":
            return _field_f32(field_no, float(value))
        return _field_i32(field_no, int(value))

    zone_cfg = b""
    chan_cfg = b""
    for name, value in fields.items():
        if value is None:
            continue
        if name in _TASK_CONFIG_FIELDS:
            field_no, kind = _TASK_CONFIG_FIELDS[name]
            zone_cfg += _encode(field_no, kind, value)
        elif name in _CHANNEL_CONFIG_FIELDS:
            field_no, kind = _CHANNEL_CONFIG_FIELDS[name]
            chan_cfg += _encode(field_no, kind, value)
        else:
            raise ValueError(f"unknown task-config field: {name}")
    from .const import USER_CTRL_GLOBAL_SETTING_N

    pb_map = b""
    if zone_cfg:
        pb_map += _field_bytes(11, zone_cfg)  # PbMap.globalZoneConfig
    if chan_cfg:
        pb_map += _field_bytes(12, chan_cfg)  # PbMap.globalChannelConfig
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_GLOBAL_SETTING_N)
    pb += _field_bytes(12, pb_map)  # PbInput.map (f12)
    return pb


# PbRobotConfig field map — (proto field number, wire kind) — derived from APK
# (Hermes) analysis of PbRobotConfig.encode (fn #9506 at offset 0x004a7ce8).
# Carried in PbInput.robotConfig (field 13) for device-config writes; the app
# omits userCtrl on these — the robot dispatches based on the submessage shape
# (see setNetworkType fn #8970). Extend when adding new fields (only the ones
# we surface as HA entities/services need to be in the writer map).
_ROBOT_CONFIG_FIELDS: dict[str, tuple[int, str]] = {
    "isOpenLed": (
        7,
        "bool",
    ),  # vehicle (mower) status LED — settings-page write fallback (the app's runtime toggle uses signal=10/11 instead, see SIGNAL_TURN_*_VEHICLE_LIGHT)
    "audioVolume": (6, "int"),  # mower beep/voice volume 0-100
    "signal": (8, "int"),  # one-shot action signals (e.g. SIGNAL_TURN_ON_VEHICLE_LIGHT=10, _OFF=11)
    "metric_4g": (11, "bool"),  # true = 4G preferred, false = WiFi preferred
    "timezoneOffset": (21, "int"),  # seconds east of UTC; matches what the app's setTimezone (#9036) writes
    "dockOnError": (22, "bool"),  # auto-dock when the mower errors out
}

# SocSignal codes used by the codec itself. The broader SocSignal enum lives
# in const.py (Hermes value table at offset 0x00488eb0) — only the two below
# stay here because they're referenced directly from this module's encoder
# / by the matching VehicleLedSwitch in switch.py.
SIGNAL_TURN_ON_VEHICLE_LIGHT = 10
SIGNAL_TURN_OFF_VEHICLE_LIGHT = 11
# Emitted by the app on the robotConfig.signal field when the headlight auto
# schedule is turned OFF — captured live 2026-05-30 alongside zeroed start/end
# times. (The ON path sends the times with no signal.)
SIGNAL_DISABLE_HEADLIGHT_SCHEDULE = 7


def _encode_pb_timezone(hour: int, minute: int) -> bytes:
    """Encode a PbTimeZone {f1 hour:int, f2 minute:int} sub-message.

    Bounds are checked at the public API layer; here we just emit the wire form.
    """
    return _field_i32(1, int(hour)) + _field_i32(2, int(minute))


def encode_set_recharge_resume(
    *,
    enable: bool | None = None,
    period_start: tuple[int, int] | None = None,
    period_end: tuple[int, int] | None = None,
    recharge_bat: int | None = None,
    resume_bat: int | None = None,
) -> bytes:
    """Encode a PbRobotConfig.rrConfig (Recharge & Resume) write.

    Field layout from PbRRConfig.encode (Hermes fn #9494 at offset 0x004a6f9b):
      f1 enableRr bool, f2 resumePeriodStart PbTimeZone, f3 resumePeriodEnd
      PbTimeZone, f4 rechargeBat int32, f5 resumeBat int32.

    rrConfig sits at PbRobotConfig field 18 (tag 146; from PbRobotConfig.encode
    line 413258). Only set parameters are encoded so partial writes preserve
    the other R&R fields on the robot side.
    """
    rr = b""
    if enable is not None:
        rr += _field_i32(1, 1 if enable else 0)
    if period_start is not None:
        rr += _field_bytes(2, _encode_pb_timezone(*period_start))
    if period_end is not None:
        rr += _field_bytes(3, _encode_pb_timezone(*period_end))
    if recharge_bat is not None:
        rr += _field_i32(4, int(recharge_bat))
    if resume_bat is not None:
        rr += _field_i32(5, int(resume_bat))
    cfg = _field_bytes(18, rr)  # PbRobotConfig.rrConfig
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    return pb


def encode_find_my_robot_play_sound(volume: int = 100) -> bytes:
    """Encode the app's "Find My Robot → Play Sound" frame.

    Captured live 2026-05-27 from the app: ``PbInput {f2:49, f13(robotConfig):
    {f6:volume audioVolume}, f16:1}``. f16 is a one-shot trigger (no encoder
    mapping previously) — set to 1 to fire the beacon. Volume defaults to 100
    (max) to match the app.
    """
    cfg = _field_i32(6, int(volume))  # audioVolume
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    pb += _field_i32(16, 1)  # PbInput.f16 = 1 → play find-my-robot sound
    return pb


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
        if name not in _ROBOT_CONFIG_FIELDS:
            raise ValueError(f"unknown robot-config field: {name}")
        field_no, kind = _ROBOT_CONFIG_FIELDS[name]
        if kind == "bool":
            cfg += _field_i32(field_no, 1 if value else 0)
        elif kind == "int":
            cfg += _field_i32(field_no, int(value))
        else:
            # Guard against silent mis-encoding if a new kind ever lands in the map.
            raise ValueError(f"unsupported robot-config kind: {kind!r}")
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    return pb


def encode_set_headlight_schedule(
    *,
    enable: bool,
    start: tuple[int, int] | None = None,
    end: tuple[int, int] | None = None,
) -> bytes:
    """Encode the app's Device Settings → Headlight Mode "Save" frame.

    Captured live 2026-05-30 (three saves, ON twice + OFF). The headlight auto
    on/off window lives on PbRobotConfig (PbInput.f13) as two PbTimeZone
    sub-messages — f14 startTime, f15 endTime — with hour/minute stored in
    **UTC** (the app converts the local picker value before sending). Every
    headlight save also carries a constant PbInput.f9 = {f10:1} marker that
    does not appear on other robotConfig writes (rrConfig / find-my-robot).

    enable=True requires both ``start`` and ``end`` (the app always sends the
    pair). enable=False reproduces the disable frame: signal=7 plus zeroed
    times; ``start`` / ``end`` are ignored.
    """
    marker = _field_bytes(9, _field_i32(10, 1))  # PbInput.f9 = {f10:1}
    if enable:
        if start is None or end is None:
            raise ValueError("enabling the headlight schedule requires start and end")
        cfg = _field_bytes(14, _encode_pb_timezone(*start))
        cfg += _field_bytes(15, _encode_pb_timezone(*end))
    else:
        cfg = _field_i32(8, SIGNAL_DISABLE_HEADLIGHT_SCHEDULE)  # PbRobotConfig.signal
        cfg += _field_bytes(14, _encode_pb_timezone(0, 0))
        cfg += _field_bytes(15, _encode_pb_timezone(0, 0))
    pb = _field_i32(2, PB_VERSION)
    pb += marker
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    return pb


def encode_set_wifi(ssid: str, password: str) -> bytes:
    """Encode a Wi-Fi provisioning command (PbInput.wifiConfig, field 17).

    Wire format captured live 2026-05-30 over BLE (re-provisioning the mower's
    Wi-Fi): ``PbInput {f17:{f1: ssid, f2: password, f5: 3}}`` — no version
    prefix; f5=3 is a constant the app always sends (connect/auth mode). The
    robot reconnects to the named network on receipt. SECURITY: ssid/password
    are sensitive and are never logged here.
    """
    if not ssid:
        raise ValueError("ssid must not be empty")
    inner = _field_str(1, ssid) + _field_str(2, password) + _field_i32(5, 3)
    return _field_bytes(17, inner)  # PbInput.wifiConfig


def encode_bind_rtk(base_id: str) -> bytes:
    """Encode an RTK-base bind command (PbRobotConfig.rtkBind, field 17).

    Captured live 2026-05-30 (re-binding the current base): ``PbInput {f2:49,
    f13(robotConfig):{f17:{f1: baseId}}}`` — no userCtrl (robotConfig dispatch).
    Binds the mower to the RTK base station with the given ID. (Distinct from the
    Wi-Fi command, which carries f17 at the PbInput top level, not in robotConfig.)
    """
    if not base_id:
        raise ValueError("base_id must not be empty")
    cfg = _field_bytes(17, _field_str(1, base_id))  # PbRobotConfig.rtkBind
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    return pb


def encode_set_pin(pin: str) -> bytes:
    """Encode an LCD-screen PIN write (PbRobotConfig.lcdPinCode).

    ``pin`` is exactly 4 digits. Wire format captured live 2026-05-30:
    ``PbInput {f2:49, f13(robotConfig):{f9(lcdPinCode):{f1: <one byte per
    digit>}}}`` — no userCtrl (robotConfig dispatch). The PIN unlocks the
    physical keypad on the mower. SECURITY: the value is sensitive — it is never
    logged here and the ValueError carries no digits.
    """
    if not (isinstance(pin, str) and len(pin) == 4 and pin.isdigit()):
        raise ValueError("PIN must be exactly 4 digits")
    lcd = _field_bytes(1, bytes(int(c) for c in pin))  # lcdPinCode.f1 = digit bytes
    cfg = _field_bytes(9, lcd)  # PbRobotConfig.lcdPinCode (f9)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(13, cfg)  # PbInput.robotConfig
    return pb


# PbRunTimeConfig field map — pinned to Hermes class #9456: f1 cutHeight, f2
# moveSpeed, f3 cutSpeed, f4 channelConfig (PbChannelConfig — per-channel
# override, not exposed here). Carried at PbMap.runTimeConfig (PbInput.f12 →
# PbMap.f13) under USER_CTRL_SET_RUN_TIME_CONFIG. Distinct shape from
# PbZoneConfig — don't reuse those field numbers.
_RUN_TIME_CONFIG_FIELDS: dict[str, tuple[int, str]] = {
    "cutHeight": (1, "int"),
    "moveSpeed": (2, "float"),
    "cutSpeed": (3, "int"),
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


def encode_set_device_settings(
    *,
    charging_mode: int | None = None,
    zone_order: int | None = None,
    rainy_mowing: bool | None = None,
    charging_handbrake: bool | None = None,
) -> bytes:
    """Encode a USER_CTRL_SET_TASK_CONFIG write of the Device Settings page.

    PbTaskConfig (the real one, app-side fn #9588 / encoder #9590 at
    0x004aed0b) has FOUR fields, written into PbInput.taskConfig (f26)
    alongside userCtrl=USER_CTRL_SET_TASK_CONFIG=36:

      f1 chargingMode int      — "Return to Dock" route (0 NORMAL / 1 QUICK)
      f2 zoneOrder int         — 0 OPTIMIZE / 1 CUSTOM
      f3 rainCleaning bool     — "Rainy Mowing" toggle
      f4 disableChargingPark bool — *inverted* "Charging Handbrake" (true =
                                    handbrake disabled). The HA-facing
                                    ``charging_handbrake`` param follows the
                                    app's UI sense (on = handbrake engaged);
                                    we invert here.

    Note: this is a *different* PbTaskConfig from the broader 18-field map
    in ``_ZONE_CONFIG_FIELDS`` — those fields are PbZoneConfig (constructor
    Hermes #9432, encoder #9434) and are published over this same userCtrl=36
    / PbInput.taskConfig wire path by ``encode_set_task_config``; #157 tracks
    the rewire to the proper PbMap.goZones[i].zoneConfig path.

    Only the provided parameters are sent; ``None`` is skipped so partial
    writes preserve the other fields on the robot side.
    """
    from .const import USER_CTRL_SET_TASK_CONFIG

    cfg = b""
    if charging_mode is not None:
        cfg += _field_i32(1, int(charging_mode))
    if zone_order is not None:
        cfg += _field_i32(2, int(zone_order))
    if rainy_mowing is not None:
        cfg += _field_i32(3, 1 if rainy_mowing else 0)
    if charging_handbrake is not None:
        # UI sense → wire sense: handbrake engaged means disableChargingPark=false.
        cfg += _field_i32(4, 0 if charging_handbrake else 1)

    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_SET_TASK_CONFIG)
    pb += _field_bytes(26, cfg)  # PbInput.taskConfig
    return pb


def encode_query_map(queryIndex: int = 0) -> bytes:
    """Encode a query-map command (userCtrl=19)."""
    sub = _field_i32(1, queryIndex) + _field_i32(4, 1)
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, 19)  # USER_CTRL_QUERY_MAP
    pb += _field_bytes(23, sub)
    return pb


def encode_query_robot_config() -> bytes:
    """Encode a getRobotConfig command using the f9 sub-message format.

    The robot does NOT respond to a plain userCtrl=35 (f5=35). The correct wire
    format confirmed from app capture is PbInput{f2=version, f9={f10=1}}.
    The f9 sub-message routes to the robot config handler; the marker f10=1
    acts as a discriminator for the 'get all' query.
    """
    pb = _field_i32(2, PB_VERSION)
    pb += _field_bytes(9, _field_i32(10, 1))  # PbInput.f9={f10=1} = getRobotConfig
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


def _encode_channel(ch: dict) -> bytes:
    """Encode a PbChannel sub-message for inclusion in a sync_map payload."""
    out = _field_str(1, ch.get("hashId", ""))
    if ch.get("zone1"):
        out += _field_str(2, ch["zone1"])
    if ch.get("zone2"):
        out += _field_str(3, ch["zone2"])
    out += _field_i32(4, int(ch.get("isValid", True)))
    if ch.get("polygon"):
        out += _field_bytes(5, _encode_map_polygon(ch["polygon"]))
    out += _field_i32(6, int(ch.get("isDockingChannel", False)))
    if "cutHeight" in ch:
        out += _field_i32(9, int(ch["cutHeight"]))
    if "channelLift" in ch:
        out += _field_i32(10, int(ch["channelLift"]))
    return out


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

    # f2 = ZoneConfig (PbZoneConfig — optional). Wire field numbers pinned to
    # canonical Hermes class #9432: f1 cutHeight (int), f10 pathSpacing (int).
    # If a full zoneConfig dict came back from the decoder we re-emit every
    # field so a vertex-move sync_map round-trip preserves the other settings.
    cfg = b""
    zc = zone.get("zoneConfig") or {}
    if "cutHeight" in zone and "cutHeight" not in zc:
        zc["cutHeight"] = zone["cutHeight"]
    if "pathSpacing" in zone and "pathSpacing" not in zc:
        zc["pathSpacing"] = zone["pathSpacing"]
    for name, (field_no, kind) in _TASK_CONFIG_FIELDS.items():
        if name not in zc:
            continue
        value = zc[name]
        if value is None:
            continue
        if kind == "bool":
            cfg += _field_i32(field_no, 1 if value else 0)
        elif kind == "float":
            cfg += _field_f32(field_no, float(value))
        else:
            cfg += _field_i32(field_no, int(value))

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
    for ch in map_data.get("channels", []):
        out += _field_bytes(3, _encode_channel(ch))
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
    tc = map_data.get("taskConfig")
    if isinstance(tc, dict) and tc:
        tc_bytes = b""
        if tc.get("chargingMode") is not None:
            tc_bytes += _field_i32(1, int(tc["chargingMode"]))
        if tc.get("zoneOrder") is not None:
            tc_bytes += _field_i32(2, int(tc["zoneOrder"]))
        if tc.get("rainCleaning") is not None:
            tc_bytes += _field_i32(3, 1 if tc["rainCleaning"] else 0)
        if tc.get("disableChargingPark") is not None:
            tc_bytes += _field_i32(4, 1 if tc["disableChargingPark"] else 0)
        if tc_bytes:
            out += _field_bytes(8, tc_bytes)
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


def encode_rename_nogo_zone(hash_id: str, name: str) -> bytes:
    """Encode a USER_CTRL_MODIFY_ZONE_INFO command renaming a no-go zone.

    Mirrors encode_rename_zone, but the zone goes in PbMap.nogoZones (field 2)
    instead of goZones (field 1) — same shape used by encode_delete_nogo_zone.
    """
    from .const import USER_CTRL_MODIFY_ZONE_INFO

    basic_info = _field_str(2, name) + _field_str(3, hash_id)
    zone = _field_bytes(1, basic_info)
    pb_map = _field_bytes(2, zone)  # PbMap.nogoZones, not goZones
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, USER_CTRL_MODIFY_ZONE_INFO)
    pb += _field_bytes(12, pb_map)
    return pb


def _encode_zone_config_submessage(cfg: dict[str, Any]) -> bytes:
    """Encode a PbZoneConfig sub-message from a dict of named fields.

    Field name → wire layout from _TASK_CONFIG_FIELDS (Hermes #9432). ``cutHeight``
    is at field 1 (not in _TASK_CONFIG_FIELDS because it's also the first field of
    PbRunTimeConfig and PbChannel); everything else routes through the map.
    """
    out = b""
    if cfg.get("cutHeight") is not None:
        out += _field_i32(1, int(cfg["cutHeight"]))
    for name, (field_no, kind) in _TASK_CONFIG_FIELDS.items():
        if name not in cfg:
            continue
        value = cfg[name]
        if value is None:
            continue
        if kind == "bool":
            out += _field_i32(field_no, 1 if value else 0)
        elif kind == "float":
            out += _field_f32(field_no, float(value))
        else:
            out += _field_i32(field_no, int(value))
    return out


def encode_set_zone_config(updates: list[dict[str, Any]]) -> bytes:
    """Encode a per-zone PbZoneConfig override via USER_CTRL_MODIFY_ZONE_INFO.

    Wire layout captured live 2026-05-27 from the app's Mowing Settings →
    Customize tab (BLE ATT WRITE_CMD handle 0x0014). Same envelope as
    ``encode_rename_zone`` but carries ``configBox`` instead of a name:

      PbInput {
        f2  version = 49
        f5  userCtrl = 9 (USER_CTRL_MODIFY_ZONE_INFO)
        f12 PbMap {
          goZones[*] = PbZone {
            f1 basicInfo = PbZoneBasicInfo {hashId, isEnabled}
            f2 configBox = PbZoneConfig {...PbZoneConfig fields...}
          }
        }
      }

    Each ``updates`` entry: ``{"hashId": str, "isEnabled": bool=True,
    ...PbZoneConfig fields}`` where the config fields use ``_TASK_CONFIG_FIELDS``
    naming (``cutHeight``, ``moveSpeed``, ``pathSpacing``, …). Multiple entries
    are sent in one frame, matching the app's bulk-update behaviour.

    Distinct from ``encode_rename_zone`` (which carries ``name`` in basicInfo)
    and from ``async_update_zone_cut_height``'s sync_map path (which re-sends
    the full map — slower but works on older robots). This is the targeted,
    bandwidth-efficient per-zone path the app itself uses.
    """
    from .const import USER_CTRL_MODIFY_ZONE_INFO

    if not updates:
        raise ValueError("encode_set_zone_config: at least one update required")

    pb_map = b""
    for entry in updates:
        hash_id = entry.get("hashId")
        if not hash_id:
            raise ValueError("encode_set_zone_config: every update needs a hashId")
        is_enabled = 1 if entry.get("isEnabled", True) else 0
        basic_info = _field_str(3, hash_id) + _field_i32(4, is_enabled)
        cfg_bytes = _encode_zone_config_submessage(entry)
        zone = _field_bytes(1, basic_info)
        if cfg_bytes:
            zone += _field_bytes(2, cfg_bytes)
        pb_map += _field_bytes(1, zone)  # PbMap.goZones (repeated)

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
