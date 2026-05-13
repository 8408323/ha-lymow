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

# Protocol version used in all outgoing PbInput messages (v4.9 = 40)
PB_VERSION = 40

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

    return state


# ---------------------------------------------------------------------------
# PbInput encoders — commands sent to the robot
# ---------------------------------------------------------------------------


def encode_userctrl(command: int) -> bytes:
    """Encode a simple user control command."""
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, command)
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
