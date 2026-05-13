"""Unit tests for protocol.py — protobuf encode/decode."""

from __future__ import annotations

import base64
import json

import pytest
from lymow.protocol import (
    _decode_fields,
    _decode_packed_int32s,
    _decode_varint,
    _encode_varint,
    _field_bytes,
    _field_i32,
    _field_str,
    _signed32,
    decode_pboutput,
    encode_start_zones,
    encode_userctrl,
    unwrap_envelope,
    wrap_envelope,
)

# ---------------------------------------------------------------------------
# Varint round-trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 127, 128, 255, 300, 16383, 16384, 2**21, 2**28])
def test_varint_roundtrip(value: int) -> None:
    encoded = _encode_varint(value)
    decoded, pos = _decode_varint(encoded, 0)
    assert decoded == value
    assert pos == len(encoded)


def test_varint_negative_wrapped_as_uint64() -> None:
    # -1 encoded as two's-complement uint64 → 10-byte varint
    encoded = _encode_varint(-1)
    assert len(encoded) == 10
    decoded, _ = _decode_varint(encoded, 0)
    # The raw uint64 value is 2^64 - 1
    assert decoded == (1 << 64) - 1


def test_varint_single_byte() -> None:
    assert _encode_varint(0) == b"\x00"
    assert _encode_varint(1) == b"\x01"
    assert _encode_varint(127) == b"\x7f"


def test_varint_two_bytes() -> None:
    assert _encode_varint(128) == b"\x80\x01"
    assert _encode_varint(300) == b"\xac\x02"


# ---------------------------------------------------------------------------
# _signed32
# ---------------------------------------------------------------------------


def test_signed32_positive() -> None:
    assert _signed32(0) == 0
    assert _signed32(1) == 1
    assert _signed32(0x7FFFFFFF) == 2147483647


def test_signed32_negative() -> None:
    assert _signed32(0x80000000) == -2147483648
    assert _signed32(0xFFFFFFFF) == -1


def test_signed32_truncates_high_bits() -> None:
    # -1 encoded as 64-bit varint gives a huge uint; must be masked to 32 bits first
    raw_neg1_64bit = (1 << 64) - 1
    assert _signed32(raw_neg1_64bit) == -1


# ---------------------------------------------------------------------------
# _decode_packed_int32s
# ---------------------------------------------------------------------------


def test_decode_packed_empty() -> None:
    assert _decode_packed_int32s(b"") == []


def test_decode_packed_single_positive() -> None:
    data = _encode_varint(7)
    assert _decode_packed_int32s(data) == [7]


def test_decode_packed_multiple() -> None:
    data = _encode_varint(1) + _encode_varint(2) + _encode_varint(3)
    assert _decode_packed_int32s(data) == [1, 2, 3]


def test_decode_packed_negative_int32() -> None:
    # Negative int32 like -1 is encoded as 10-byte uint64 varint in protobuf
    data = _encode_varint((1 << 64) - 1)  # raw encoding of int32 -1
    assert _decode_packed_int32s(data) == [-1]


def test_decode_packed_mixed_signs() -> None:
    neg1 = _encode_varint((1 << 64) - 1)
    pos = _encode_varint(5)
    data = pos + neg1
    result = _decode_packed_int32s(data)
    assert result == [5, -1]


# ---------------------------------------------------------------------------
# Field encoding helpers
# ---------------------------------------------------------------------------


def test_field_i32_zero() -> None:
    out = _field_i32(1, 0)
    fields = _decode_fields(out)
    assert fields == [(1, 0, 0)]


def test_field_i32_roundtrip() -> None:
    for field_no, value in [(1, 0), (2, 1), (5, 127), (10, 300), (15, 2**16)]:
        out = _field_i32(field_no, value)
        fields = _decode_fields(out)
        assert len(fields) == 1
        assert fields[0][0] == field_no
        assert fields[0][2] == value


def test_field_bytes_roundtrip() -> None:
    payload = b"\x01\x02\x03\xff"
    out = _field_bytes(3, payload)
    fields = _decode_fields(out)
    assert len(fields) == 1
    assert fields[0][0] == 3
    assert fields[0][1] == 2  # wire type length-delimited
    assert fields[0][2] == payload


def test_field_str_roundtrip() -> None:
    text = "hello-zone"
    out = _field_str(7, text)
    fields = _decode_fields(out)
    assert fields[0][2] == text.encode()


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_wrap_envelope_produces_valid_json() -> None:
    pb = b"\x01\x02\x03"
    env = wrap_envelope(pb)
    obj = json.loads(env)
    assert "message" in obj
    assert base64.b64decode(obj["message"]) == pb


def test_unwrap_envelope_message_key() -> None:
    pb = b"\xde\xad\xbe\xef"
    env = json.dumps({"message": base64.b64encode(pb).decode()})
    assert unwrap_envelope(env) == pb


def test_unwrap_envelope_bytes_input() -> None:
    pb = b"\x01"
    env = json.dumps({"message": base64.b64encode(pb).decode()}).encode()
    assert unwrap_envelope(env) == pb


def test_unwrap_envelope_fallback_keys() -> None:
    pb = b"\x42"
    for key in ("value", "data", "payload"):
        env = json.dumps({key: base64.b64encode(pb).decode()})
        assert unwrap_envelope(env) == pb


def test_unwrap_envelope_unknown_key_raises() -> None:
    env = json.dumps({"unknown": "abc"})
    with pytest.raises(ValueError):
        unwrap_envelope(env)


def test_wrap_unwrap_roundtrip() -> None:
    pb = bytes(range(256))
    assert unwrap_envelope(wrap_envelope(pb)) == pb


# ---------------------------------------------------------------------------
# encode_userctrl
# ---------------------------------------------------------------------------


def test_encode_userctrl_structure() -> None:
    from lymow.protocol import PB_VERSION

    pb = encode_userctrl(1)
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[2] == PB_VERSION
    assert by_field[5] == 1


def test_encode_userctrl_all_commands() -> None:
    from lymow.const import (
        USER_CTRL_CLEAN,
        USER_CTRL_PAUSE,
        USER_CTRL_PAUSE_DOCK,
        USER_CTRL_RECHARGE_DOCK,
        USER_CTRL_RESUME,
        USER_CTRL_RESUME_DOCK,
    )

    for cmd in (
        USER_CTRL_CLEAN,
        USER_CTRL_PAUSE,
        USER_CTRL_RESUME,
        USER_CTRL_RECHARGE_DOCK,
        USER_CTRL_PAUSE_DOCK,
        USER_CTRL_RESUME_DOCK,
    ):
        pb = encode_userctrl(cmd)
        fields = _decode_fields(pb)
        by_field = {fn: val for fn, _wt, val in fields}
        assert by_field[5] == cmd


# ---------------------------------------------------------------------------
# encode_start_zones
# ---------------------------------------------------------------------------


def test_encode_start_zones_empty() -> None:
    from lymow.protocol import PB_VERSION

    pb = encode_start_zones([])
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[2] == PB_VERSION
    assert by_field[5] == 1  # USER_CTRL_CLEAN
    assert 12 not in by_field  # no map sub-message when no zones


def test_encode_start_zones_single() -> None:
    pb = encode_start_zones(["abc123"])
    fields = _decode_fields(pb)
    by_field = {fn: (wt, val) for fn, wt, val in fields}
    assert 12 in by_field
    map_bytes = by_field[12][1]
    assert b"abc123" in map_bytes


def test_encode_start_zones_multiple() -> None:
    zone_ids = ["zone-a", "zone-b", "zone-c"]
    pb = encode_start_zones(zone_ids)
    fields = _decode_fields(pb)
    by_field = {fn: (wt, val) for fn, wt, val in fields}
    assert 12 in by_field
    map_bytes = by_field[12][1]
    for zid in zone_ids:
        assert zid.encode() in map_bytes


# ---------------------------------------------------------------------------
# decode_pboutput — full integration
# ---------------------------------------------------------------------------


def _build_pboutput(
    *,
    work_status: int = 2,
    battery: int = 85,
    is_charging: int = 0,
    is_recharging: int = 0,
    wifi_signal: int | None = None,
    lte_signal: int | None = None,
    error_codes: list[int] | None = None,
    warning_codes: list[int] | None = None,
    fw_version: str | None = None,
    mcu_version: str | None = None,
) -> bytes:
    """Hand-build a minimal PbOutput blob for testing."""
    from lymow.protocol import PB_VERSION

    # PbRobotInfo (sub-message, field 5)
    robot_info = _field_i32(6, work_status)  # workStatus
    robot_info += _field_i32(2, battery)
    robot_info += _field_i32(8, is_charging)
    robot_info += _field_i32(7, is_recharging)
    if wifi_signal is not None:
        robot_info += _field_i32(3, wifi_signal)
    if lte_signal is not None:
        robot_info += _field_i32(4, lte_signal)

    # PbDeviceProfile (sub-message, field 10)
    profile = b""
    if fw_version is not None:
        profile += _field_str(1, fw_version)
    if mcu_version is not None:
        profile += _field_str(2, mcu_version)

    # Top-level PbOutput
    out = _field_i32(2, PB_VERSION)

    if error_codes is not None:
        packed = b"".join(_encode_varint(c & 0xFFFFFFFFFFFFFFFF) for c in error_codes)
        out += _field_bytes(3, packed)

    if warning_codes is not None:
        packed = b"".join(_encode_varint(c & 0xFFFFFFFFFFFFFFFF) for c in warning_codes)
        out += _field_bytes(4, packed)

    out += _field_bytes(5, robot_info)

    if profile:
        out += _field_bytes(10, profile)

    return out


def test_decode_pboutput_basic() -> None:
    pb = _build_pboutput(work_status=2, battery=75)
    state = decode_pboutput(pb)
    assert state["workStatus"] == 2
    assert state["battery"] == 75
    assert state["isCharging"] is False
    assert state["isRecharging"] is False
    assert state["errorCodes"] == []
    assert state["errorCode"] == 0


def test_decode_pboutput_charging() -> None:
    pb = _build_pboutput(work_status=5, battery=100, is_charging=1)
    state = decode_pboutput(pb)
    assert state["workStatus"] == 5
    assert state["battery"] == 100
    assert state["isCharging"] is True


def test_decode_pboutput_error_codes() -> None:
    pb = _build_pboutput(error_codes=[7, 31])
    state = decode_pboutput(pb)
    assert state["errorCodes"] == [7, 31]
    assert state["errorCode"] == 7


def test_decode_pboutput_negative_error_code() -> None:
    # Some error codes may be negative int32s encoded as 64-bit varints
    pb = _build_pboutput(error_codes=[-1])
    state = decode_pboutput(pb)
    assert state["errorCodes"] == [-1]
    assert state["errorCode"] == -1


def test_decode_pboutput_warning_codes() -> None:
    pb = _build_pboutput(warning_codes=[4, 5])
    state = decode_pboutput(pb)
    assert state["warningCodes"] == [4, 5]


def test_decode_pboutput_wifi_signal() -> None:
    pb = _build_pboutput(wifi_signal=80)
    state = decode_pboutput(pb)
    assert state["wifiSignalQuality"] == 80


def test_decode_pboutput_lte_signal() -> None:
    pb = _build_pboutput(lte_signal=60)
    state = decode_pboutput(pb)
    assert state["lteSignalQuality"] == 60


def test_decode_pboutput_fw_version() -> None:
    pb = _build_pboutput(fw_version="4.9.1")
    state = decode_pboutput(pb)
    assert state["fwVersion"] == "4.9.1"


def test_decode_pboutput_mcu_version() -> None:
    pb = _build_pboutput(mcu_version="2.3.0")
    state = decode_pboutput(pb)
    assert state["mcuVersion"] == "2.3.0"


def test_decode_pboutput_empty_bytes() -> None:
    state = decode_pboutput(b"")
    assert state["errorCodes"] == []
    assert state["warningCodes"] == []
    assert state.get("workStatus") is None or state.get("workStatus") == -1


def test_decode_pboutput_work_status_all_known() -> None:
    from lymow.const import (
        WORK_STATUS_DOCKED_GROUP,
        WORK_STATUS_ERROR_GROUP,
        WORK_STATUS_MOWING_GROUP,
        WORK_STATUS_PAUSED_GROUP,
        WORK_STATUS_RETURNING_GROUP,
    )

    all_known = (
        WORK_STATUS_MOWING_GROUP
        | WORK_STATUS_RETURNING_GROUP
        | WORK_STATUS_DOCKED_GROUP
        | WORK_STATUS_PAUSED_GROUP
        | WORK_STATUS_ERROR_GROUP
    )
    for ws in all_known:
        pb = _build_pboutput(work_status=ws)
        state = decode_pboutput(pb)
        assert state["workStatus"] == ws


def test_decode_pboutput_via_envelope() -> None:
    pb = _build_pboutput(work_status=3, battery=50)
    env = wrap_envelope(pb)
    recovered = unwrap_envelope(env)
    state = decode_pboutput(recovered)
    assert state["workStatus"] == 3
    assert state["battery"] == 50
