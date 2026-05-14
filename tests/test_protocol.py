"""Unit tests for protocol.py — protobuf encode/decode."""

from __future__ import annotations

import base64
import json

import pytest
from lymow.protocol import (
    _all,
    _decode_f32,
    _decode_fields,
    _decode_packed_int32s,
    _decode_varint,
    _encode_varint,
    _field_bytes,
    _field_f32,
    _field_i32,
    _field_str,
    _first,
    _signed32,
    decode_map_response,
    decode_pboutput,
    delete_zone,
    encode_delete_zone,
    encode_start_zones,
    encode_sync_map,
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


# ---------------------------------------------------------------------------
# decode_map_response
# ---------------------------------------------------------------------------


def _pt(x: float, y: float) -> bytes:
    """Encode a map point sub-message with f1=x, f2=y as i32 floats."""
    return _field_f32(1, x) + _field_f32(2, y)


def _polygon(points: list[tuple[float, float]]) -> bytes:
    return b"".join(_field_bytes(1, _pt(x, y)) for x, y in points)


def _build_map_response(
    *,
    go_zones: list[dict] | None = None,
    nogo_zones: list[dict] | None = None,
    charging_station: dict | None = None,
    gps_origin: dict | None = None,
) -> bytes:
    """Build a minimal PbMapResponse blob for testing decode_map_response."""
    content = b""

    for zone in go_zones or []:
        bi = _field_i32(1, zone.get("type", 1))
        bi += _field_str(3, zone["hashId"])
        bi += _field_i32(4, 1 if zone.get("isEnabled", True) else 0)
        if zone.get("polygon"):
            bi += _field_bytes(5, _polygon([(p["x"], p["y"]) for p in zone["polygon"]]))

        pp = b""
        if zone.get("boundMin"):
            pp += _field_bytes(1, _pt(zone["boundMin"]["x"], zone["boundMin"]["y"]))
        if zone.get("boundMax"):
            pp += _field_bytes(2, _pt(zone["boundMax"]["x"], zone["boundMax"]["y"]))
        if zone.get("area") is not None:
            pp += _field_i32(3, zone["area"])
        if zone.get("innerPoint"):
            pp += _field_bytes(5, _pt(zone["innerPoint"]["x"], zone["innerPoint"]["y"]))

        zone_pb = _field_bytes(1, bi)
        if pp:
            zone_pb += _field_bytes(3, pp)
        if zone.get("cutHeight") is not None or zone.get("pathSpacing") is not None:
            cfg = b""
            if zone.get("cutHeight") is not None:
                cfg += _field_i32(1, zone["cutHeight"])
            if zone.get("pathSpacing") is not None:
                cfg += _field_f32(4, zone["pathSpacing"])
            zone_pb += _field_bytes(2, cfg)

        content += _field_bytes(1, zone_pb)

    for nogo in nogo_zones or []:
        bi = _field_i32(1, nogo.get("type", 2))
        bi += _field_str(3, nogo["hashId"])
        bi += _field_i32(4, 1 if nogo.get("isEnabled", True) else 0)
        if nogo.get("polygon"):
            bi += _field_bytes(5, _polygon([(p["x"], p["y"]) for p in nogo["polygon"]]))

        nogo_pb = _field_bytes(1, bi)
        if nogo.get("parentZoneHashId"):
            nogo_pb += _field_bytes(4, nogo["parentZoneHashId"].encode("utf-8"))

        content += _field_bytes(2, nogo_pb)

    if charging_station:
        cs = (
            _field_f32(1, charging_station["x"])
            + _field_f32(2, charging_station["y"])
            + _field_f32(3, charging_station["theta"])
        )
        content += _field_bytes(4, cs)

    if gps_origin:
        gps = _field_f32(1, gps_origin["lat"]) + _field_f32(2, gps_origin["lon"])
        content += _field_bytes(7, gps)

    wrapper = _field_i32(1, 1) + _field_bytes(3, content)
    outer = _field_bytes(2, wrapper)
    return _field_bytes(23, outer)


def test_decode_map_response_empty_bytes() -> None:
    result = decode_map_response(b"")
    assert result == {}


def test_decode_map_response_missing_f23() -> None:
    pb = _field_i32(2, 42)  # no f23
    result = decode_map_response(pb)
    assert result == {}


def test_decode_map_response_go_zone_basic() -> None:
    pb = _build_map_response(
        go_zones=[
            {
                "hashId": "abc12345",
                "type": 1,
                "isEnabled": True,
                "polygon": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}],
                "area": 50,
                "boundMin": {"x": 1.0, "y": 2.0},
                "boundMax": {"x": 3.0, "y": 4.0},
                "innerPoint": {"x": 2.0, "y": 3.0},
            }
        ]
    )
    result = decode_map_response(pb)
    assert len(result["goZones"]) == 1
    zone = result["goZones"][0]
    assert zone["hashId"] == "abc12345"
    assert zone["type"] == 1
    assert zone["isEnabled"] is True
    assert len(zone["polygon"]) == 2
    assert zone["area"] == 50
    assert pytest.approx(zone["boundMin"]["x"], abs=1e-4) == 1.0
    assert pytest.approx(zone["innerPoint"]["y"], abs=1e-4) == 3.0


def test_decode_map_response_go_zone_disabled() -> None:
    pb = _build_map_response(go_zones=[{"hashId": "zonexyz1", "type": 1, "isEnabled": False}])
    result = decode_map_response(pb)
    assert result["goZones"][0]["isEnabled"] is False


def test_decode_map_response_go_zone_config() -> None:
    pb = _build_map_response(
        go_zones=[
            {
                "hashId": "cfgzone1",
                "type": 1,
                "cutHeight": 55,
                "pathSpacing": 0.35,
            }
        ]
    )
    result = decode_map_response(pb)
    zone = result["goZones"][0]
    assert zone["cutHeight"] == 55
    assert pytest.approx(zone["pathSpacing"], abs=1e-4) == 0.35


def test_decode_map_response_multiple_go_zones() -> None:
    pb = _build_map_response(
        go_zones=[
            {"hashId": "zone0001", "type": 1},
            {"hashId": "zone0002", "type": 1},
            {"hashId": "zone0003", "type": 1},
        ]
    )
    result = decode_map_response(pb)
    assert len(result["goZones"]) == 3
    hash_ids = [z["hashId"] for z in result["goZones"]]
    assert hash_ids == ["zone0001", "zone0002", "zone0003"]


def test_decode_map_response_nogo_zone() -> None:
    pb = _build_map_response(
        nogo_zones=[
            {
                "hashId": "nogo0001",
                "type": 2,
                "isEnabled": True,
                "polygon": [{"x": 0.5, "y": 1.5}, {"x": 0.6, "y": 1.6}],
                "parentZoneHashId": "zone0001",
            }
        ]
    )
    result = decode_map_response(pb)
    assert len(result["nogoZones"]) == 1
    nogo = result["nogoZones"][0]
    assert nogo["hashId"] == "nogo0001"
    assert nogo["type"] == 2
    assert len(nogo["polygon"]) == 2
    assert nogo["parentZoneHashId"] == "zone0001"


def test_decode_map_response_nogo_no_parent() -> None:
    pb = _build_map_response(nogo_zones=[{"hashId": "nogo0002", "type": 2}])
    result = decode_map_response(pb)
    nogo = result["nogoZones"][0]
    assert "parentZoneHashId" not in nogo


def test_decode_map_response_charging_station() -> None:
    pb = _build_map_response(charging_station={"x": -0.0832, "y": -0.1065, "theta": -1.5713})
    result = decode_map_response(pb)
    cs = result["chargingStation"]
    assert pytest.approx(cs["x"], abs=1e-3) == -0.0832
    assert pytest.approx(cs["y"], abs=1e-3) == -0.1065
    assert pytest.approx(cs["theta"], abs=1e-3) == -1.5713


def test_decode_map_response_gps_origin() -> None:
    pb = _build_map_response(gps_origin={"lat": 59.6824, "lon": 16.7594})
    result = decode_map_response(pb)
    gps = result["gpsOrigin"]
    assert pytest.approx(gps["lat"], abs=1e-3) == 59.6824
    assert pytest.approx(gps["lon"], abs=1e-3) == 16.7594


def test_decode_map_response_full() -> None:
    pb = _build_map_response(
        go_zones=[
            {"hashId": "gozone01", "type": 1, "polygon": [{"x": 1.0, "y": 2.0}], "area": 100},
            {"hashId": "gozone02", "type": 1, "cutHeight": 40, "pathSpacing": 1.0},
        ],
        nogo_zones=[
            {"hashId": "nogo0001", "type": 2, "parentZoneHashId": "gozone01"},
            {"hashId": "nogo0002", "type": 2, "parentZoneHashId": "gozone02"},
        ],
        charging_station={"x": -0.08, "y": -0.11, "theta": -1.57},
        gps_origin={"lat": 59.68, "lon": 16.76},
    )
    result = decode_map_response(pb)
    assert len(result["goZones"]) == 2
    assert len(result["nogoZones"]) == 2
    assert "chargingStation" in result
    assert "gpsOrigin" in result
    assert result["goZones"][0]["hashId"] == "gozone01"
    assert result["nogoZones"][1]["parentZoneHashId"] == "gozone02"


# ---------------------------------------------------------------------------
# delete_zone helpers
# ---------------------------------------------------------------------------


def _sample_map() -> dict:
    """Build a minimal map_data dict for delete_zone tests."""
    pb = _build_map_response(
        go_zones=[
            {"hashId": "gozone01", "type": 1, "polygon": [{"x": 1.0, "y": 2.0}]},
            {"hashId": "gozone02", "type": 1, "polygon": [{"x": 3.0, "y": 4.0}]},
        ],
        nogo_zones=[
            {"hashId": "nogo0001", "type": 2, "parentZoneHashId": "gozone01"},
            {"hashId": "nogo0002", "type": 2, "parentZoneHashId": "gozone02"},
        ],
        charging_station={"x": -0.08, "y": -0.11, "theta": -1.57},
        gps_origin={"lat": 59.68, "lon": 16.76},
    )
    return decode_map_response(pb)


# ---------------------------------------------------------------------------
# delete_zone tests
# ---------------------------------------------------------------------------


def test_delete_go_zone_removes_zone() -> None:
    """Deleting a goZone removes it from goZones."""
    m = _sample_map()
    result = delete_zone(m, "gozone01")
    hash_ids = [z["hashId"] for z in result["goZones"]]
    assert "gozone01" not in hash_ids
    assert "gozone02" in hash_ids


def test_delete_go_zone_cascades_nogo() -> None:
    """Deleting a goZone also removes its child nogoZones."""
    m = _sample_map()
    result = delete_zone(m, "gozone01")
    nogo_ids = [n["hashId"] for n in result["nogoZones"]]
    assert "nogo0001" not in nogo_ids  # cascade-deleted
    assert "nogo0002" in nogo_ids  # unrelated — kept


def test_delete_nogo_zone() -> None:
    """Deleting a nogoZone by its hashId works independently."""
    m = _sample_map()
    result = delete_zone(m, "nogo0002")
    nogo_ids = [n["hashId"] for n in result["nogoZones"]]
    assert "nogo0002" not in nogo_ids
    assert "nogo0001" in nogo_ids


def test_delete_nonexistent_zone_raises() -> None:
    """Raises ValueError for unknown hash_id."""
    m = _sample_map()
    with pytest.raises(ValueError, match="not found"):
        delete_zone(m, "does_not_exist")


def test_delete_zone_does_not_mutate_original() -> None:
    """delete_zone must not modify the original map_data dict."""
    m = _sample_map()
    original_go_count = len(m["goZones"])
    delete_zone(m, "gozone01")
    assert len(m["goZones"]) == original_go_count


# ---------------------------------------------------------------------------
# encode_sync_map tests
# ---------------------------------------------------------------------------


def test_encode_sync_map_has_pb_version() -> None:
    """Field 2 of the encoded message must equal PB_VERSION (49)."""
    raw = encode_sync_map({})
    fields = {fn: v for fn, _wt, v in _decode_fields(raw)}
    assert fields.get(2) == 49


def test_encode_sync_map_has_correct_command_number() -> None:
    """Field 5 of the encoded message must equal USER_CTRL_SYNC_MAP."""
    from lymow.const import USER_CTRL_SYNC_MAP

    raw = encode_sync_map({})
    fields = {fn: v for fn, _wt, v in _decode_fields(raw)}
    assert fields.get(5) == USER_CTRL_SYNC_MAP


def test_encode_sync_map_has_f23_wrapper() -> None:
    """Field 23 must be present (btMap/PbMap field) even for empty map_data."""
    raw = encode_sync_map({})
    fields = {fn: v for fn, _wt, v in _decode_fields(raw)}
    assert 23 in fields


def test_encode_sync_map_decode_roundtrip() -> None:
    """Encoding then decoding must recover the same go/nogo zones, charging station, and GPS origin.

    PbInput.btMap (field 23) now carries PbMap bytes directly — no f2→f3 wrapper.
    Decode by reading f23 raw bytes as PbMap content.
    """
    map_data = _sample_map()
    raw = encode_sync_map(map_data)

    # Decode PbInput → f23 = raw PbMap bytes
    top = _decode_fields(raw)
    content_raw = _first(top, 23)
    assert isinstance(content_raw, bytes)
    content = _decode_fields(content_raw)

    go_zone_raws = _all(content, 1)
    nogo_zone_raws = _all(content, 2)

    def _get_hash(zone_raw: bytes) -> str:
        zf = _decode_fields(zone_raw)
        bi_raw = _first(zf, 1)
        if not isinstance(bi_raw, bytes):
            return ""
        bi = _decode_fields(bi_raw)
        h = _first(bi, 3)
        return h.decode() if isinstance(h, bytes) else ""

    assert len(go_zone_raws) == len(map_data["goZones"])
    assert len(nogo_zone_raws) == len(map_data["nogoZones"])
    assert {_get_hash(z) for z in go_zone_raws} == {z["hashId"] for z in map_data["goZones"]}
    assert {_get_hash(n) for n in nogo_zone_raws} == {n["hashId"] for n in map_data["nogoZones"]}

    cs_raw = _first(content, 4)
    assert isinstance(cs_raw, bytes)
    cs_fields = _decode_fields(cs_raw)
    x_raw = _first(cs_fields, 1)
    y_raw = _first(cs_fields, 2)
    t_raw = _first(cs_fields, 3)
    orig_cs = map_data["chargingStation"]
    assert pytest.approx(_decode_f32(x_raw), abs=1e-3) == orig_cs["x"]
    assert pytest.approx(_decode_f32(y_raw), abs=1e-3) == orig_cs["y"]
    assert pytest.approx(_decode_f32(t_raw), abs=1e-3) == orig_cs["theta"]

    gps_raw = _first(content, 7)
    assert isinstance(gps_raw, bytes)
    gps_fields = _decode_fields(gps_raw)
    lat_raw = _first(gps_fields, 1)
    lon_raw = _first(gps_fields, 2)
    orig_gps = map_data["gpsOrigin"]
    assert pytest.approx(_decode_f32(lat_raw), abs=1e-3) == orig_gps["lat"]
    assert pytest.approx(_decode_f32(lon_raw), abs=1e-3) == orig_gps["lon"]


def test_sync_map_after_delete_roundtrip() -> None:
    """Delete a zone, encode, decode — deleted zone must not appear."""
    map_data = _sample_map()
    updated = delete_zone(map_data, "gozone01")
    raw = encode_sync_map(updated)

    # Decode PbInput → f23 = raw PbMap bytes (no wrapper)
    top = _decode_fields(raw)
    content_raw = _first(top, 23)
    assert isinstance(content_raw, bytes)
    content = _decode_fields(content_raw)

    def _get_hash(zone_raw: bytes) -> str:
        zf = _decode_fields(zone_raw)
        bi_raw = _first(zf, 1)
        if not isinstance(bi_raw, bytes):
            return ""
        bi = _decode_fields(bi_raw)
        h = _first(bi, 3)
        return h.decode() if isinstance(h, bytes) else ""

    go_ids = {_get_hash(z) for z in _all(content, 1)}
    nogo_ids = {_get_hash(n) for n in _all(content, 2)}
    assert "gozone01" not in go_ids
    assert "gozone02" in go_ids
    assert "nogo0001" not in nogo_ids  # cascade-deleted
    assert "nogo0002" in nogo_ids

    # modifyHashs (field 9) must carry the deleted zone's hash
    modify_raws = _all(content, 9)
    modify_ids = {b.decode() for b in modify_raws if isinstance(b, bytes)}
    assert "gozone01" in modify_ids


# ---------------------------------------------------------------------------
# encode_delete_zone tests
# ---------------------------------------------------------------------------


def test_encode_delete_zone_has_pb_version() -> None:
    raw = encode_delete_zone("5ilVIZvD")
    top = _decode_fields(raw)
    assert _first(top, 2) == 49  # PB_VERSION


def test_encode_delete_zone_has_ctrl_8() -> None:
    """USER_CTRL_CLEAR_ZONE = 8 (confirmed from Hermes fn 10144 / fn 8972)."""
    raw = encode_delete_zone("5ilVIZvD")
    top = _decode_fields(raw)
    assert _first(top, 5) == 8


def test_encode_delete_zone_uses_field_12_not_23() -> None:
    """map must be in field 12 (not field 23=btMap)."""
    raw = encode_delete_zone("5ilVIZvD")
    top = _decode_fields(raw)
    assert _first(top, 12) is not None, "field 12 (map) must be present"
    assert _first(top, 23) is None, "field 23 (btMap) must NOT be present"


def test_encode_delete_zone_contains_hash_id() -> None:
    """The target hashId must appear in PbMap→goZones[0]→basicInfo→f3."""
    hash_id = "5ilVIZvD"
    raw = encode_delete_zone(hash_id)
    top = _decode_fields(raw)
    pb_map_raw = _first(top, 12)
    assert isinstance(pb_map_raw, bytes)

    pb_map = _decode_fields(pb_map_raw)
    go_zones = _all(pb_map, 1)
    assert len(go_zones) == 1, "PbMap must have exactly one goZone"

    zone_raw = go_zones[0]
    assert isinstance(zone_raw, bytes)
    zone_fields = _decode_fields(zone_raw)
    basic_info_raw = _first(zone_fields, 1)  # PbZone.basicInfo = f1
    assert isinstance(basic_info_raw, bytes)

    basic_info = _decode_fields(basic_info_raw)
    h = _first(basic_info, 3)  # PbZoneBasicInfo.hashId = f3
    assert isinstance(h, bytes)
    assert h.decode() == hash_id


def test_encode_delete_zone_no_nogo_zones() -> None:
    """PbMap must have no nogoZones field (only goZones)."""
    raw = encode_delete_zone("abc")
    top = _decode_fields(raw)
    pb_map_raw = _first(top, 12)
    assert isinstance(pb_map_raw, bytes)
    pb_map = _decode_fields(pb_map_raw)
    assert _first(pb_map, 2) is None, "PbMap must not have nogoZones for a goZone delete"


# ---------------------------------------------------------------------------
# decode_pboutput — RTK / GPS / pose / area fields
# ---------------------------------------------------------------------------


def _build_pboutput_with_extras(
    *,
    rtk_satellites: int | None = None,
    rtk_east_m: float | None = None,
    rtk_north_m: float | None = None,
    rtk_status: int | None = None,
    total_area_m2: float | None = None,
    pose_east_m: float | None = None,
    pose_north_m: float | None = None,
    pose_theta_rad: float | None = None,
) -> bytes:
    """Build a PbOutput blob with GPS/RTK, area and pose fields."""
    from lymow.protocol import PB_VERSION

    out = _field_i32(2, PB_VERSION)

    # GPS/RTK field (field 6 of outer PbOutput)
    if any(v is not None for v in (rtk_satellites, rtk_east_m, rtk_north_m, rtk_status)):
        rtk = b""
        if rtk_satellites is not None:
            rtk += _field_i32(1, rtk_satellites)
        if rtk_east_m is not None:
            rtk += _field_f32(2, rtk_east_m)
        if rtk_north_m is not None:
            rtk += _field_f32(3, rtk_north_m)
        if rtk_status is not None:
            rtk += _field_i32(4, rtk_status)
        out += _field_bytes(6, rtk)

    # Area info (field 12)
    if total_area_m2 is not None:
        area = _field_f32(2, total_area_m2)
        out += _field_bytes(12, area)

    # Robot pose ENU (field 14)
    if any(v is not None for v in (pose_east_m, pose_north_m, pose_theta_rad)):
        pose = b""
        if pose_east_m is not None:
            pose += _field_f32(1, pose_east_m)
        if pose_north_m is not None:
            pose += _field_f32(2, pose_north_m)
        if pose_theta_rad is not None:
            pose += _field_f32(3, pose_theta_rad)
        out += _field_bytes(14, pose)

    return out


def test_decode_pboutput_rtk_satellites() -> None:
    pb = _build_pboutput_with_extras(rtk_satellites=12)
    state = decode_pboutput(pb)
    assert state["rtkSatellites"] == 12


def test_decode_pboutput_rtk_status() -> None:
    pb = _build_pboutput_with_extras(rtk_status=2)
    state = decode_pboutput(pb)
    assert state["rtkStatus"] == 2


def test_decode_pboutput_rtk_east_north() -> None:
    pb = _build_pboutput_with_extras(rtk_east_m=1.5, rtk_north_m=2.5)
    state = decode_pboutput(pb)
    assert abs(state["rtkEastM"] - 1.5) < 0.001
    assert abs(state["rtkNorthM"] - 2.5) < 0.001


def test_decode_pboutput_total_area() -> None:
    pb = _build_pboutput_with_extras(total_area_m2=1234.5)
    state = decode_pboutput(pb)
    assert abs(state["totalAreaM2"] - 1234.5) < 1.0


def test_decode_pboutput_pose_enu() -> None:
    import math

    pb = _build_pboutput_with_extras(pose_east_m=3.0, pose_north_m=4.0, pose_theta_rad=math.pi / 2)
    state = decode_pboutput(pb)
    assert abs(state["poseEastM"] - 3.0) < 0.001
    assert abs(state["poseNorthM"] - 4.0) < 0.001
    assert abs(state["poseThetaRad"] - math.pi / 2) < 0.001


def test_decode_pboutput_no_rtk_when_absent() -> None:
    pb = _build_pboutput()
    state = decode_pboutput(pb)
    assert "rtkSatellites" not in state
    assert "rtkEastM" not in state
    assert "totalAreaM2" not in state
    assert "poseEastM" not in state
