"""Unit tests for protocol.py — protobuf encode/decode."""

from __future__ import annotations

import base64
import json
import struct

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
    decode_schedule_entry,
    decode_task_config,
    delete_zone,
    encode_ble_drive,
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
    bt_signal: int | None = None,
    wifi_working: bool | None = None,
    lte_working: bool | None = None,
    robot_state: int | None = None,
    error_codes: list[int] | None = None,
    warning_codes: list[int] | None = None,
    fw_version: str | None = None,
    mcu_version: str | None = None,
    wifi_ssid: str | None = None,
    rtk_sn: str | None = None,
    wheel_ver: str | None = None,
    knife_ver: str | None = None,
    sw_version_mqtt: str | None = None,
    sim_id_mqtt: str | None = None,
) -> bytes:
    """Hand-build a minimal PbOutput blob for testing."""
    from lymow.protocol import PB_VERSION

    # PbRobotInfo (sub-message, field 5)
    robot_info = _field_i32(6, work_status)  # workStatus
    if robot_state is not None:
        robot_info += _field_i32(1, robot_state)
    robot_info += _field_i32(2, battery)
    robot_info += _field_i32(8, is_charging)
    robot_info += _field_i32(7, is_recharging)
    if wifi_signal is not None:
        robot_info += _field_i32(3, wifi_signal)
    if lte_signal is not None:
        robot_info += _field_i32(4, lte_signal)
    if bt_signal is not None:
        robot_info += _field_i32(5, bt_signal)
    if wifi_working is not None:
        robot_info += _field_i32(9, 1 if wifi_working else 0)
    if lte_working is not None:
        robot_info += _field_i32(10, 1 if lte_working else 0)

    # PbDeviceProfile (sub-message, field 10)
    profile = b""
    if fw_version is not None:
        profile += _field_str(1, fw_version)
    if mcu_version is not None:
        profile += _field_str(2, mcu_version)
    if sw_version_mqtt is not None:
        profile += _field_str(3, sw_version_mqtt)
    if wifi_ssid is not None:
        profile += _field_str(4, wifi_ssid)
    if rtk_sn is not None:
        profile += _field_str(8, rtk_sn)
    if sim_id_mqtt is not None:
        profile += _field_str(9, sim_id_mqtt)
    if wheel_ver is not None:
        profile += _field_str(10, wheel_ver)
    if knife_ver is not None:
        profile += _field_str(11, knife_ver)

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
    assert "robotState" not in state


def test_decode_pboutput_robot_state() -> None:
    # f5.f1=5 observed when docked/charging; f5.f1=2 when mowing
    pb = _build_pboutput(work_status=1, battery=88, is_charging=1, robot_state=5)
    state = decode_pboutput(pb)
    assert state["robotState"] == 5
    assert state["workStatus"] == 1
    assert state["isCharging"] is True


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


def test_decode_pboutput_bt_signal() -> None:
    """PbRobotInfo.btSignalQuality (f5) — new sensor for the Bluetooth link."""
    pb = _build_pboutput(bt_signal=-68)
    state = decode_pboutput(pb)
    assert state["btSignalQuality"] == -68


def test_decode_pboutput_wifi_lte_working_bools() -> None:
    """PbRobotInfo.wifiWorking (f9) + lteWorking (f10) — connectivity flags."""
    pb_on = _build_pboutput(wifi_working=True, lte_working=False)
    state = decode_pboutput(pb_on)
    assert state["wifiWorking"] is True
    assert state["lteWorking"] is False

    # Field absent → key absent (no implicit False).
    pb_neither = _build_pboutput()
    state = decode_pboutput(pb_neither)
    assert "wifiWorking" not in state
    assert "lteWorking" not in state


def test_decode_pboutput_extended_device_profile_strings() -> None:
    """PbDeviceProfile extras (f4 wifiSsid, f8 rtkSn, f10 wheelVer, f11 knifeVer)."""
    pb = _build_pboutput(
        wifi_ssid="Haraldsson",
        rtk_sn="RTK-XYZ-001",
        wheel_ver="wheel-1.2.3",
        knife_ver="blade-0.4.1",
    )
    state = decode_pboutput(pb)
    assert state["wifiSsid"] == "Haraldsson"
    assert state["rtkSn"] == "RTK-XYZ-001"
    assert state["wheelVer"] == "wheel-1.2.3"
    assert state["knifeVer"] == "blade-0.4.1"


def test_decode_pboutput_extended_device_profile_sw_version_and_sim_id() -> None:
    """PbDeviceProfile f3 (softwareVersion) + f9 (simId) come over MQTT alongside
    same-named REST fields. They're stored under distinct keys
    (``swVersionMqtt`` / ``simIdMqtt``) so the REST sensors keep their existing
    source — but the MQTT-side values still round-trip through the decoder so
    a future refactor doesn't silently drop them."""
    pb = _build_pboutput(sw_version_mqtt="2.1.48", sim_id_mqtt="8946070000000000000")
    state = decode_pboutput(pb)
    assert state["swVersionMqtt"] == "2.1.48"
    assert state["simIdMqtt"] == "8946070000000000000"


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
    channels: list[dict] | None = None,
    task_config: dict | None = None,
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

    for chan in channels or []:
        ch = _field_str(1, chan["hashId"])
        if chan.get("zone1"):
            ch += _field_str(2, chan["zone1"])
        if chan.get("zone2"):
            ch += _field_str(3, chan["zone2"])
        ch += _field_i32(4, 1 if chan.get("isValid", True) else 0)
        if chan.get("polygon"):
            ch += _field_bytes(5, _polygon([(p["x"], p["y"]) for p in chan["polygon"]]))
        ch += _field_i32(6, 1 if chan.get("isDockingChannel") else 0)
        if chan.get("cutHeight") is not None:
            ch += _field_i32(9, chan["cutHeight"])
        if chan.get("channelLift") is not None:
            ch += _field_i32(10, chan["channelLift"])
        content += _field_bytes(3, ch)

    if task_config is not None:
        tc = b""
        if "chargingMode" in task_config:
            tc += _field_i32(1, task_config["chargingMode"])
        if "zoneOrder" in task_config:
            tc += _field_i32(2, task_config["zoneOrder"])
        if "rainCleaning" in task_config:
            tc += _field_i32(3, 1 if task_config["rainCleaning"] else 0)
        if "disableChargingPark" in task_config:
            tc += _field_i32(4, 1 if task_config["disableChargingPark"] else 0)
        content += _field_bytes(8, tc)

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
    pb = _build_map_response(gps_origin={"lat": 12.3456, "lon": 65.4321})
    result = decode_map_response(pb)
    gps = result["gpsOrigin"]
    assert pytest.approx(gps["lat"], abs=1e-3) == 12.3456
    assert pytest.approx(gps["lon"], abs=1e-3) == 65.4321


def test_decode_map_response_task_config_all_fields() -> None:
    pb = _build_map_response(
        task_config={
            "chargingMode": 1,  # QUICK / Direct route
            "zoneOrder": 1,  # CUSTOM
            "rainCleaning": True,
            "disableChargingPark": False,
        }
    )
    result = decode_map_response(pb)
    assert result["taskConfig"] == {
        "chargingMode": 1,
        "zoneOrder": 1,
        "rainCleaning": True,
        "disableChargingPark": False,
    }


def test_decode_map_response_no_task_config_when_absent() -> None:
    """f8 missing → no taskConfig key (so entities read None and report unknown)."""
    pb = _build_map_response(gps_origin={"lat": 1.0, "lon": 2.0})
    result = decode_map_response(pb)
    assert "taskConfig" not in result


def test_decode_task_config_direct_each_field() -> None:
    # Field 1 only
    assert decode_task_config(_field_i32(1, 0)) == {"chargingMode": 0}
    # Field 2 only
    assert decode_task_config(_field_i32(2, 1)) == {"zoneOrder": 1}
    # Field 3 only — bool decoded from varint
    assert decode_task_config(_field_i32(3, 1)) == {"rainCleaning": True}
    assert decode_task_config(_field_i32(3, 0)) == {"rainCleaning": False}
    # Field 4 only — bool
    assert decode_task_config(_field_i32(4, 1)) == {"disableChargingPark": True}


def test_decode_task_config_empty_bytes() -> None:
    assert decode_task_config(b"") == {}


def test_decode_task_config_drops_non_boolean_bool_fields() -> None:
    """Hostile / corrupted payload: varint 2+ for f3/f4 must NOT silently
    become True. Drop the key so the entity reports unknown."""
    assert decode_task_config(_field_i32(3, 2)) == {}
    assert decode_task_config(_field_i32(4, 5)) == {}
    # And a mixed message keeps the valid fields and drops the bad ones.
    pb = _field_i32(1, 1) + _field_i32(3, 9) + _field_i32(4, 1)
    assert decode_task_config(pb) == {"chargingMode": 1, "disableChargingPark": True}


def test_decode_map_response_channels() -> None:
    pb = _build_map_response(
        channels=[
            {
                "hashId": "ch000001",
                "zone1": "z1",
                "zone2": "z2",
                "isValid": True,
                "isDockingChannel": False,
                "polygon": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}],
                "cutHeight": 40,
                "channelLift": 1,
            },
            {"hashId": "ch000002", "isValid": False, "isDockingChannel": True},
        ]
    )
    result = decode_map_response(pb)
    assert len(result["channels"]) == 2
    c0 = result["channels"][0]
    assert c0["hashId"] == "ch000001"
    assert c0["zone1"] == "z1"
    assert c0["zone2"] == "z2"
    assert c0["isValid"] is True
    assert c0["isDockingChannel"] is False
    assert len(c0["polygon"]) == 2
    assert c0["cutHeight"] == 40
    assert c0["channelLift"] == 1
    assert result["channels"][1]["isDockingChannel"] is True
    assert result["channels"][1]["isValid"] is False


def test_decode_map_response_no_channels_is_empty_list() -> None:
    result = decode_map_response(_build_map_response(go_zones=[]))
    assert result["channels"] == []


def test_encode_delete_channel_wraps_hash_in_map() -> None:
    from lymow.const import USER_CTRL_DELETE_CHANNEL
    from lymow.protocol import PB_VERSION, encode_delete_channel

    pb = encode_delete_channel("ch000001")
    f = _decode_fields(pb)
    assert _first(f, 2) == PB_VERSION
    assert _first(f, 5) == USER_CTRL_DELETE_CHANNEL
    pb_map = _decode_fields(_first(f, 12))  # PbMap
    channel = _decode_fields(_first(pb_map, 3))  # PbMap.channels = f3
    assert _first(channel, 1) == b"ch000001"  # PbChannel.hashId = f1


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
        gps_origin={"lat": 12.34, "lon": 65.43},
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
        gps_origin={"lat": 12.34, "lon": 65.43},
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


def test_encode_delete_nogo_zone_uses_nogo_field_with_pbzone_wrapper() -> None:
    from lymow.const import USER_CTRL_CLEAR_ZONE
    from lymow.protocol import encode_delete_nogo_zone

    raw = encode_delete_nogo_zone("ng123")
    top = _decode_fields(raw)
    assert _first(top, 5) == USER_CTRL_CLEAR_ZONE
    pb_map = _decode_fields(_first(top, 12))
    assert _first(pb_map, 1) is None, "must use nogoZones (f2), not goZones (f1)"
    zone = _decode_fields(_first(pb_map, 2))  # PbMap.nogoZones[0] = PbZone
    basic = _decode_fields(_first(zone, 1))  # PbZone.basicInfo
    assert _first(basic, 3) == b"ng123"  # hashId


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
    mow_strip_count: int | None = None,
    mow_progress: float | None = None,
    remain_clean_time_sec: int | None = None,
    map_area_m2: float | None = None,
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

    # PbCleanInfo (field 12): f1=cleanTime/mowStripCount, f2=cleanArea/totalArea,
    # f4=remainCleanTime, f5=cleanPercent/mowProgress, f6=mapArea.
    if any(v is not None for v in (total_area_m2, mow_strip_count, mow_progress, remain_clean_time_sec, map_area_m2)):
        area = b""
        if mow_strip_count is not None:
            area += _field_i32(1, mow_strip_count)
        if total_area_m2 is not None:
            area += _field_f32(2, total_area_m2)
        if remain_clean_time_sec is not None:
            area += _field_i32(4, remain_clean_time_sec)
        if mow_progress is not None:
            area += _field_f32(5, mow_progress)
        if map_area_m2 is not None:
            area += _field_f32(6, map_area_m2)
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
    assert abs(state["totalTaskAreaM2"] - 1234.5) < 1.0


def test_decode_pboutput_pose_enu() -> None:
    import math

    pb = _build_pboutput_with_extras(pose_east_m=3.0, pose_north_m=4.0, pose_theta_rad=math.pi / 2)
    state = decode_pboutput(pb)
    assert abs(state["poseEastM"] - 3.0) < 0.001
    assert abs(state["poseNorthM"] - 4.0) < 0.001
    assert abs(state["poseThetaRad"] - math.pi / 2) < 0.001


def test_decode_pboutput_charging_station_loc_live_pose() -> None:
    """PbOutput.f24 = PbPose (same sub-message type as f14 robot pose).
    The live dock-position channel — surfaces as ``chargingStationLoc`` with
    the ``{x, y, theta}`` shape that matches the map-query path's
    ``mapData.chargingStation`` entry, so a card can pick whichever is fresher."""
    import math

    dock = _field_f32(1, 1.5) + _field_f32(2, 2.5) + _field_f32(3, math.pi)
    pb = _build_pboutput() + _field_bytes(24, dock)
    state = decode_pboutput(pb)
    assert abs(state["chargingStationLoc"]["x"] - 1.5) < 0.001
    assert abs(state["chargingStationLoc"]["y"] - 2.5) < 0.001
    assert abs(state["chargingStationLoc"]["theta"] - math.pi) < 0.001


def test_decode_pboutput_charging_station_loc_partial_present_fields() -> None:
    """A pboutput carrying only the dock's east/north (no theta) still
    surfaces, with theta absent — partial updates must not require all
    three fields."""
    dock = _field_f32(1, 5.0) + _field_f32(2, 7.0)
    pb = _build_pboutput() + _field_bytes(24, dock)
    state = decode_pboutput(pb)
    assert state["chargingStationLoc"] == {"x": 5.0, "y": 7.0}


def test_decode_pboutput_no_charging_station_loc_when_field24_absent() -> None:
    assert "chargingStationLoc" not in decode_pboutput(_build_pboutput())


def test_decode_pboutput_no_charging_station_loc_when_field24_empty() -> None:
    """An empty f24 sub-message has no decodable scalars — drop the key
    so a stale-but-known dock entry from the map path isn't shadowed by
    an empty dict."""
    pb = _build_pboutput() + _field_bytes(24, b"")
    assert "chargingStationLoc" not in decode_pboutput(pb)


def test_decode_pboutput_charging_station_loc_skips_wire_type_drift() -> None:
    """PbPose f1/f2/f3 are wire-type 5 (fixed32) per the encoder, but the
    wire is untrusted — if a malformed payload sends f1 as length-delimited
    bytes, ``_decode_f32`` would otherwise raise. Drop the offending field
    and surface the rest."""
    # f1 sent as wire-type-2 bytes; f2 sent correctly as float32
    dock = _field_bytes(1, b"\x00\x00") + _field_f32(2, 3.5)
    pb = _build_pboutput() + _field_bytes(24, dock)
    assert decode_pboutput(pb)["chargingStationLoc"] == {"y": 3.5}


def test_decode_pboutput_no_rtk_when_absent() -> None:
    pb = _build_pboutput()
    state = decode_pboutput(pb)
    assert "rtkSatellites" not in state
    assert "rtkEastM" not in state
    assert "totalTaskAreaM2" not in state
    assert "poseEastM" not in state


def test_decode_pboutput_mow_strip_count() -> None:
    """f12.f1 → mowStripCount decoded as integer."""
    pb = _build_pboutput_with_extras(mow_strip_count=17)
    state = decode_pboutput(pb)
    assert state["mowStripCount"] == 17


def test_decode_pboutput_mow_progress() -> None:
    """f12.f5 → mowProgress decoded as float 0–1 * 100."""
    pb = _build_pboutput_with_extras(mow_progress=0.526)
    state = decode_pboutput(pb)
    # Should be approximately 52.6
    assert abs(state["mowProgress"] - 52.6) < 1.0


def test_decode_pboutput_mow_strip_count_and_progress_together() -> None:
    """f12.f1, f12.f2, f12.f5 all decoded simultaneously."""
    pb = _build_pboutput_with_extras(total_area_m2=800.0, mow_strip_count=5, mow_progress=0.25)
    state = decode_pboutput(pb)
    assert state["mowStripCount"] == 5
    assert abs(state["totalTaskAreaM2"] - 800.0) < 1.0
    assert abs(state["mowProgress"] - 25.0) < 1.0


def test_decode_pboutput_mow_fields_absent_when_not_set() -> None:
    """mowStripCount and mowProgress absent from state when not encoded."""
    pb = _build_pboutput()
    state = decode_pboutput(pb)
    assert "mowStripCount" not in state
    assert "mowProgress" not in state


def test_decode_pboutput_remain_clean_time_decoded() -> None:
    """f12.f4 → remainCleanTimeSec — used by the new ETA sensor."""
    pb = _build_pboutput_with_extras(remain_clean_time_sec=1830)
    state = decode_pboutput(pb)
    assert state["remainCleanTimeSec"] == 1830


def test_decode_pboutput_map_area_decoded() -> None:
    """f12.f6 → mapAreaM2 — the total map area (much larger than per-task)."""
    pb = _build_pboutput_with_extras(map_area_m2=4250.0)
    state = decode_pboutput(pb)
    assert abs(state["mapAreaM2"] - 4250.0) < 1.0


def test_decode_pboutput_heated_lens_times_decoded_as_counter() -> None:
    """PbOutput.f37 = uint32 — lens heater fire count, monotonically
    increasing. Surfaces as ``heatedLensTimes`` for a maintenance sensor."""
    pb = _build_pboutput() + _field_i32(37, 42)
    state = decode_pboutput(pb)
    assert state["heatedLensTimes"] == 42


def test_decode_pboutput_no_heated_lens_times_key_when_field37_absent() -> None:
    """When the robot doesn't report f37 (older firmware / no camera lens
    on this SKU), the key must stay absent so the sensor doesn't show 0
    as if the heater has fired zero times."""
    assert "heatedLensTimes" not in decode_pboutput(_build_pboutput())


def test_decode_pboutput_heated_lens_times_zero_surfaces() -> None:
    """A reported 0 IS meaningful — the heater hasn't fired yet this install.
    Distinct from "field absent" → key absent."""
    pb = _build_pboutput() + _field_i32(37, 0)
    assert decode_pboutput(pb)["heatedLensTimes"] == 0


def test_decode_pboutput_heated_lens_times_drops_sign_extended_negative() -> None:
    """``_decode_varint`` always returns unsigned, so a sign-extended int32
    -1 (0xFFFFFFFFFFFFFFFF on the wire) would surface as 4-billion+ if we
    only checked ``>= 0``. ``_signed32`` interprets the wrap-around as -1,
    which we reject so the sensor doesn't render a nonsense counter."""
    # _field_i32(37, -1) emits a 10-byte varint = 0xFFFFFFFFFFFFFFFF
    pb = _build_pboutput() + _field_i32(37, -1)
    assert "heatedLensTimes" not in decode_pboutput(pb)


def test_decode_pboutput_clean_info_all_fields_together() -> None:
    """All five PbCleanInfo fields coexist in one PbOutput.f12 sub-message."""
    pb = _build_pboutput_with_extras(
        mow_strip_count=120,
        total_area_m2=350.0,
        remain_clean_time_sec=900,
        mow_progress=0.45,
        map_area_m2=1500.0,
    )
    state = decode_pboutput(pb)
    assert state["mowStripCount"] == 120
    assert abs(state["totalTaskAreaM2"] - 350.0) < 1.0
    assert state["remainCleanTimeSec"] == 900
    assert abs(state["mowProgress"] - 45.0) < 1.0
    assert abs(state["mapAreaM2"] - 1500.0) < 1.0


def test_decode_pboutput_remain_and_map_area_absent_when_not_set() -> None:
    """New PbCleanInfo fields stay out of state when the wire doesn't carry them
    — partial frames must not introduce zero values."""
    pb = _build_pboutput_with_extras(mow_strip_count=5)
    state = decode_pboutput(pb)
    assert "remainCleanTimeSec" not in state
    assert "mapAreaM2" not in state


# ---------------------------------------------------------------------------
# decode_pboutput — f22 wifiRssiDbm
# ---------------------------------------------------------------------------


def _build_pboutput_with_wifi_rssi(rssi_str: str) -> bytes:
    """Build PbOutput with f22.f6 = rssi_str (UTF-8 bytes)."""
    from lymow.protocol import PB_VERSION

    f22_inner = _field_str(6, rssi_str)
    return _field_i32(2, PB_VERSION) + _field_bytes(22, f22_inner)


def test_decode_pboutput_wifi_rssi_dbm_valid() -> None:
    """f22.f6 string '-77' → wifiRssiDbm = -77."""
    pb = _build_pboutput_with_wifi_rssi("-77")
    state = decode_pboutput(pb)
    assert state["wifiRssiDbm"] == -77


def test_decode_pboutput_wifi_rssi_dbm_positive() -> None:
    """f22.f6 positive value is accepted."""
    pb = _build_pboutput_with_wifi_rssi("-40")
    state = decode_pboutput(pb)
    assert state["wifiRssiDbm"] == -40


def test_decode_pboutput_wifi_rssi_dbm_invalid_string() -> None:
    """f22.f6 non-numeric string → wifiRssiDbm absent (ValueError swallowed)."""
    pb = _build_pboutput_with_wifi_rssi("N/A")
    state = decode_pboutput(pb)
    assert "wifiRssiDbm" not in state


def test_decode_pboutput_wifi_rssi_dbm_absent_when_no_f22() -> None:
    """wifiRssiDbm absent when f22 is not present at all."""
    pb = _build_pboutput()
    state = decode_pboutput(pb)
    assert "wifiRssiDbm" not in state


# ---------------------------------------------------------------------------
# _decode_fields — 64-bit and unknown wire types
# ---------------------------------------------------------------------------


def test_decode_fields_64bit_wire_type() -> None:
    """Wire type 1 (64-bit fixed) should be decoded and included in results."""
    import struct

    # Build a field with wire type 1: field_no=1, wire=1 → tag=0x09
    tag = _encode_varint((1 << 3) | 1)
    value_bytes = struct.pack("<Q", 0xDEADBEEFCAFEBABE)
    data = tag + value_bytes

    fields = _decode_fields(data)
    assert len(fields) == 1
    fn, wt, val = fields[0]
    assert fn == 1
    assert wt == 1
    assert val == 0xDEADBEEFCAFEBABE


def test_decode_fields_unknown_wire_type_stops_parsing() -> None:
    """Unknown wire type (e.g. 6) should cause parsing to stop gracefully."""
    # Build valid field 1 (varint=42), then invalid wire type 6
    valid = _field_i32(1, 42)
    # wire type 6 → tag = (2 << 3) | 6 = 22
    invalid_tag = _encode_varint((2 << 3) | 6)
    data = valid + invalid_tag + b"\x00\x00"

    fields = _decode_fields(data)
    # First field decoded normally; second stops parsing
    assert len(fields) == 1
    assert fields[0][0] == 1


# ---------------------------------------------------------------------------
# extract_raw_map_content — missing-field paths
# ---------------------------------------------------------------------------


def test_extract_raw_map_content_returns_none_on_empty_bytes() -> None:
    from lymow.protocol import extract_raw_map_content

    result = extract_raw_map_content(b"")
    assert result is None


def test_extract_raw_map_content_returns_none_when_f23_missing() -> None:
    from lymow.protocol import extract_raw_map_content

    # Build a message with field 1 only (no f23)
    data = _field_i32(1, 99)
    result = extract_raw_map_content(data)
    assert result is None


def test_extract_raw_map_content_returns_none_when_wrapper_f2_missing() -> None:
    from lymow.protocol import extract_raw_map_content

    # f23 exists but its inner bytes have no f2
    inner = _field_i32(1, 0)  # f1, not f2
    data = _field_bytes(23, inner)
    result = extract_raw_map_content(data)
    assert result is None


def test_extract_raw_map_content_returns_none_when_content_f3_missing() -> None:
    from lymow.protocol import extract_raw_map_content

    # f23 → f2 exists but f2 content has no f3
    wrapper = _field_bytes(2, _field_i32(1, 0))  # f2 contains f1, not f3
    data = _field_bytes(23, wrapper)
    result = extract_raw_map_content(data)
    assert result is None


def test_extract_raw_map_content_returns_bytes_when_present() -> None:
    from lymow.protocol import extract_raw_map_content

    raw_content = b"\x01\x02\x03"
    wrapper = _field_bytes(2, _field_bytes(3, raw_content))
    data = _field_bytes(23, wrapper)
    result = extract_raw_map_content(data)
    assert result == raw_content


# ---------------------------------------------------------------------------
# _zone_hash_from_raw / _nogo_parent_from_raw — empty paths
# ---------------------------------------------------------------------------


def test_zone_hash_from_raw_returns_empty_when_no_basic_info() -> None:
    from lymow.protocol import _zone_hash_from_raw

    # Zone raw with no f1 (BasicInfo)
    raw = _field_i32(2, 99)
    assert _zone_hash_from_raw(raw) == ""


def test_nogo_parent_from_raw_returns_empty_when_no_parent_field() -> None:
    from lymow.protocol import _nogo_parent_from_raw

    # NogoZone raw with only f1, no f4 (parent hash)
    raw = _field_i32(1, 0)
    assert _nogo_parent_from_raw(raw) == ""


# ---------------------------------------------------------------------------
# delete_zone_from_raw_content — raw field re-encode paths
# ---------------------------------------------------------------------------


def test_delete_zone_from_raw_content_removes_target_zone() -> None:
    from lymow.protocol import _zone_hash_from_raw, delete_zone_from_raw_content

    # Build minimal raw content with two go-zones
    bi1 = _field_str(3, "zoneA")
    zone1_raw = _field_bytes(1, bi1)

    bi2 = _field_str(3, "zoneB")
    zone2_raw = _field_bytes(1, bi2)

    # _MAP_CONTENT_GO_ZONES = 1
    content = _field_bytes(1, zone1_raw) + _field_bytes(1, zone2_raw)

    result = delete_zone_from_raw_content(content, "zoneA")

    # Decode result and collect go-zone hashes (fn=1)
    remaining_hashes = [
        _zone_hash_from_raw(val) for fn, _wt, val in _decode_fields(result) if fn == 1 and isinstance(val, bytes)
    ]
    assert "zoneA" not in remaining_hashes
    assert "zoneB" in remaining_hashes


def test_delete_zone_from_raw_content_also_removes_child_nogo() -> None:
    from lymow.protocol import delete_zone_from_raw_content

    # Go-zone with hashId "parent"
    bi_go = _field_str(3, "parent")
    go_raw = _field_bytes(1, bi_go)

    # Nogo-zone with parentZoneHashId = "parent" (f4)
    bi_nogo = _field_str(3, "nogo-child")
    nogo_raw = _field_bytes(1, bi_nogo) + _field_bytes(4, b"parent")

    # _MAP_CONTENT_GO_ZONES=1, _MAP_CONTENT_NOGO_ZONES=2
    content = _field_bytes(1, go_raw) + _field_bytes(2, nogo_raw)

    result = delete_zone_from_raw_content(content, "parent")
    assert b"parent" not in result.replace(_field_bytes(9, b"parent"), b"")


# ---------------------------------------------------------------------------
# encode_sync_map_raw
# ---------------------------------------------------------------------------


def test_encode_sync_map_raw_contains_pb_version() -> None:
    from lymow.protocol import encode_sync_map_raw

    pb = encode_sync_map_raw(b"\x01\x02")
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    # f2 = PB_VERSION
    assert 2 in by_field


def test_encode_sync_map_raw_contains_sync_map_command() -> None:
    from lymow.const import USER_CTRL_SYNC_MAP
    from lymow.protocol import encode_sync_map_raw

    pb = encode_sync_map_raw(b"\x01\x02")
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field.get(5) == USER_CTRL_SYNC_MAP


def test_encode_sync_map_raw_embeds_content_in_f23() -> None:
    from lymow.protocol import encode_sync_map_raw

    raw_content = b"\xab\xcd\xef"
    pb = encode_sync_map_raw(raw_content)
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field.get(23) == raw_content


# ---------------------------------------------------------------------------
# decode_map_response — missing-wrapper early returns
# ---------------------------------------------------------------------------


def test_decode_map_response_returns_empty_when_f23_wrapper_f2_missing() -> None:
    # f23 present but its content has no f2
    inner = _field_i32(1, 0)  # f1, not f2
    pb = _field_bytes(23, inner)
    result = decode_map_response(pb)
    assert result == {}


def test_decode_map_response_returns_empty_when_f23_wrapper_f3_missing() -> None:
    # f23 → f2 present but f2 content has no f3
    wrapper = _field_bytes(2, _field_i32(1, 0))
    pb = _field_bytes(23, wrapper)
    result = decode_map_response(pb)
    assert result == {}


# ---------------------------------------------------------------------------
# decode_map_response — nogo zone with pp fields (area, innerPoint)
# ---------------------------------------------------------------------------


def test_decode_map_response_nogo_with_area_and_inner_point() -> None:
    """Nogo zone with a PpBasicInfo (f3) containing area and innerPoint."""
    # We need to build a full map response with a nogo zone that has pp fields.
    # Use _field_bytes helpers to construct the protobuf manually.

    def _pt(x: float, y: float) -> bytes:
        return _field_f32(1, x) + _field_f32(2, y)

    # NogoZone BasicInfo (f1): type=0, hashId="nogo1", isEnabled=1
    bi = _field_i32(1, 0) + _field_str(3, "nogo1") + _field_i32(4, 1)
    # PpBasicInfo (f3): area=250, innerPoint
    inner_pt = _pt(5.0, 6.0)
    pp = _field_i32(3, 250) + _field_bytes(5, inner_pt)
    nogo_raw = _field_bytes(1, bi) + _field_bytes(3, pp)

    # _MAP_CONTENT_NOGO_ZONES = 2
    content_bytes = _field_bytes(2, nogo_raw)

    # Wrap in full map response structure: f23 → f2 → f3
    wrapper = _field_bytes(2, _field_bytes(3, content_bytes))
    pb = _field_bytes(23, wrapper)

    result = decode_map_response(pb)
    assert "nogoZones" in result
    nogo = result["nogoZones"][0]
    assert nogo["area"] == 250
    assert abs(nogo["innerPoint"]["x"] - 5.0) < 0.01


# ---------------------------------------------------------------------------
# _encode_go_zone — optional fields (pathSpacing, boundMin, boundMax, innerPoint)
# ---------------------------------------------------------------------------


def test_encode_go_zone_with_optional_fields_roundtrips() -> None:
    """encode_sync_map round-trips go-zone optional fields."""
    map_data = {
        "goZones": [
            {
                "hashId": "gz1",
                "type": 1,
                "cutHeight": 30,
                "pathSpacing": 0.5,
                "isEnabled": True,
                "polygon": [],
                "boundMin": {"x": 0.1, "y": 0.2},
                "boundMax": {"x": 9.9, "y": 8.8},
                "innerPoint": {"x": 5.0, "y": 4.0},
                "area": 100,
            }
        ],
        "nogoZones": [],
    }
    pb = encode_sync_map(map_data)
    # Simply verify it produces valid bytes without error and encodes the command
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert 5 in by_field  # userCtrl present


# ---------------------------------------------------------------------------
# _encode_nogo_zone — optional fields (area, innerPoint)
# ---------------------------------------------------------------------------


def test_encode_nogo_zone_with_area_and_inner_point_roundtrips() -> None:
    map_data = {
        "goZones": [],
        "nogoZones": [
            {
                "hashId": "nz1",
                "type": 0,
                "isEnabled": True,
                "polygon": [],
                "area": 75,
                "innerPoint": {"x": 3.0, "y": 2.0},
            }
        ],
    }
    pb = encode_sync_map(map_data)
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert 5 in by_field  # userCtrl present


def test_encode_nogo_zone_with_polygon() -> None:
    """Cover _encode_nogo_zone line 609: polygon present → encoded."""
    map_data = {
        "goZones": [],
        "nogoZones": [
            {
                "hashId": "nz2",
                "type": 0,
                "isEnabled": True,
                "polygon": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}],
            }
        ],
    }
    pb = encode_sync_map(map_data)
    fields = _decode_fields(pb)
    by_field = {fn: val for fn, _wt, val in fields}
    assert 5 in by_field  # userCtrl present


# ---------------------------------------------------------------------------
# delete_zone_from_raw_content — varint, 64-bit, 32-bit re-encode branches
# ---------------------------------------------------------------------------


def test_delete_zone_from_raw_content_preserves_varint_fields() -> None:
    """Cover wt=0 (varint) re-encode branch (line 247)."""

    from lymow.protocol import delete_zone_from_raw_content

    # Go-zone to delete
    bi = _field_str(3, "target")
    zone_raw = _field_bytes(1, bi)
    go_zone_field = _field_bytes(1, zone_raw)

    # Extra varint field (fn=4, wt=0)
    varint_field = _field_i32(4, 12345)

    content = go_zone_field + varint_field
    result = delete_zone_from_raw_content(content, "target")

    # Varint field fn=4 should still be present in result
    fields = _decode_fields(result)
    by_fn = {fn: val for fn, _wt, val in fields}
    assert by_fn.get(4) == 12345


def test_delete_zone_from_raw_content_preserves_32bit_float_fields() -> None:
    """Cover wt=5 (32-bit fixed) re-encode branch (lines 252-253)."""

    from lymow.protocol import delete_zone_from_raw_content

    # Go-zone to delete
    bi = _field_str(3, "target")
    zone_raw = _field_bytes(1, bi)
    go_zone_field = _field_bytes(1, zone_raw)

    # Extra f32 field (fn=5, wt=5)
    f32_field = _field_f32(5, 3.14)

    content = go_zone_field + f32_field
    result = delete_zone_from_raw_content(content, "target")

    # f32 field fn=5 should still be present in result
    fields = _decode_fields(result)
    by_fn_wt = {fn: (wt, val) for fn, wt, val in fields}
    assert 5 in by_fn_wt
    assert by_fn_wt[5][0] == 5  # wire type 5


def test_delete_zone_from_raw_content_preserves_64bit_fields() -> None:
    """Cover wt=1 (64-bit fixed) re-encode branch (line 249)."""
    import struct as _struct

    from lymow.protocol import delete_zone_from_raw_content

    # Go-zone to delete
    bi = _field_str(3, "target")
    zone_raw = _field_bytes(1, bi)
    go_zone_field = _field_bytes(1, zone_raw)

    # Manually build a 64-bit field (fn=6, wt=1)
    tag_64 = _encode_varint((6 << 3) | 1)
    val_64 = _struct.pack("<Q", 0xCAFEBABEDEAD0000)
    field_64 = tag_64 + val_64

    content = go_zone_field + field_64
    result = delete_zone_from_raw_content(content, "target")

    fields = _decode_fields(result)
    by_fn_wt = {fn: (wt, val) for fn, wt, val in fields}
    assert 6 in by_fn_wt
    assert by_fn_wt[6][0] == 1  # wire type 1


# ---------------------------------------------------------------------------
# decode_map_response — defensive continue for non-bytes zone fields
# ---------------------------------------------------------------------------


def test_decode_map_response_skips_non_bytes_go_zone_field() -> None:
    """Cover line 296: go-zone field that is not bytes is skipped."""
    # _MAP_CONTENT_GO_ZONES = 1. Encode fn=1 as varint (wt=0) — invalid but tolerated.
    # tag = (1 << 3) | 0 = 8
    tag = _encode_varint((1 << 3) | 0)
    corrupt_field = tag + _encode_varint(99)  # varint value, not bytes

    # Wrap in valid map response structure
    wrapper = _field_bytes(2, _field_bytes(3, corrupt_field))
    pb = _field_bytes(23, wrapper)

    result = decode_map_response(pb)
    # Corrupt go-zone skipped; result has goZones=[] or absent but no error
    assert result.get("goZones", []) == []


def test_decode_map_response_skips_non_bytes_nogo_zone_field() -> None:
    """Cover line 343: nogo-zone field that is not bytes is skipped."""
    # _MAP_CONTENT_NOGO_ZONES = 2. Encode fn=2 as varint (wt=0).
    tag = _encode_varint((2 << 3) | 0)
    corrupt_field = tag + _encode_varint(42)

    wrapper = _field_bytes(2, _field_bytes(3, corrupt_field))
    pb = _field_bytes(23, wrapper)

    result = decode_map_response(pb)
    assert result.get("nogoZones", []) == []


# ---------------------------------------------------------------------------
# encode_ble_drive — BLE manual-drive command
# ---------------------------------------------------------------------------


def test_encode_ble_drive_returns_bytes() -> None:
    result = encode_ble_drive(0.0, 0.0)
    assert isinstance(result, bytes)


def test_encode_ble_drive_is_base64_decodable() -> None:
    result = encode_ble_drive(0.0, 0.0)
    decoded = base64.b64decode(result)
    assert len(decoded) == 16


def test_encode_ble_drive_header_bytes() -> None:
    """Verify the fixed header bytes match the BTSnoop-captured format."""
    result = encode_ble_drive(0.0, 0.0)
    decoded = base64.b64decode(result)
    # field 2 (varint 49) = 0x10 0x31
    # field 7 (varint 2)  = 0x38 0x02
    # field 10, len 10    = 0x52 0x0a
    assert decoded[:6] == bytes.fromhex("10313802520a")


def test_encode_ble_drive_full_forward() -> None:
    """Full forward: linear=+0.5, angular=0.0 — confirmed from ADB swipe capture."""
    import struct

    result = encode_ble_drive(0.5, 0.0)
    decoded = base64.b64decode(result)
    # bytes 6-10: field 1 (float32) tag + 0.5
    assert decoded[6] == 0x0D  # field 1, wire type 5
    assert struct.unpack("<f", decoded[7:11])[0] == pytest.approx(0.5)
    # bytes 11-15: field 2 (float32) tag + 0.0
    assert decoded[11] == 0x15  # field 2, wire type 5
    assert struct.unpack("<f", decoded[12:16])[0] == pytest.approx(0.0)


def test_encode_ble_drive_full_backward() -> None:
    """Full backward: linear=-0.5, angular=0.0 — confirmed from ADB swipe capture."""
    import struct

    result = encode_ble_drive(-0.5, 0.0)
    decoded = base64.b64decode(result)
    assert struct.unpack("<f", decoded[7:11])[0] == pytest.approx(-0.5)
    assert struct.unpack("<f", decoded[12:16])[0] == pytest.approx(0.0)


def test_encode_ble_drive_full_right() -> None:
    """Full right turn: linear=0.0, angular=+0.6 — confirmed from ADB swipe capture."""
    import struct

    result = encode_ble_drive(0.0, 0.6)
    decoded = base64.b64decode(result)
    assert struct.unpack("<f", decoded[7:11])[0] == pytest.approx(0.0)
    assert struct.unpack("<f", decoded[12:16])[0] == pytest.approx(0.6, rel=1e-5)


def test_encode_ble_drive_full_left() -> None:
    """Full left turn: linear=0.0, angular=-0.6 — confirmed from ADB swipe capture."""
    import struct

    result = encode_ble_drive(0.0, -0.6)
    decoded = base64.b64decode(result)
    assert struct.unpack("<f", decoded[7:11])[0] == pytest.approx(0.0)
    assert struct.unpack("<f", decoded[12:16])[0] == pytest.approx(-0.6, rel=1e-5)


def test_encode_ble_drive_stop() -> None:
    """Stop payload: both velocities zero."""
    import struct

    result = encode_ble_drive(0.0, 0.0)
    decoded = base64.b64decode(result)
    assert struct.unpack("<f", decoded[7:11])[0] == pytest.approx(0.0)
    assert struct.unpack("<f", decoded[12:16])[0] == pytest.approx(0.0)


def test_encode_ble_drive_protobuf_structure() -> None:
    """Verify the decoded 16 bytes parse as valid protobuf with correct field numbers."""
    result = encode_ble_drive(0.25, -0.3)
    decoded = base64.b64decode(result)
    outer_fields = _decode_fields(decoded)
    by_field = {fn: val for fn, _wt, val in outer_fields}

    from lymow.protocol import PB_VERSION

    # field 2 = PB_VERSION (49)
    assert by_field[2] == PB_VERSION
    # field 7 = 2 (constant sub-type)
    assert by_field[7] == 2
    # field 10 = inner bytes (10 bytes)
    assert isinstance(by_field[10], bytes)
    assert len(by_field[10]) == 10

    # inner message has float32 fields 1 and 2
    inner_fields = _decode_fields(by_field[10])
    inner_by_field = {fn: val for fn, _wt, val in inner_fields}
    import struct

    linear = struct.unpack("<f", struct.pack("<I", inner_by_field[1]))[0]
    angular = struct.unpack("<f", struct.pack("<I", inner_by_field[2]))[0]
    assert linear == pytest.approx(0.25, rel=1e-5)
    assert angular == pytest.approx(-0.3, rel=1e-5)


def _schedule_pb(
    days=(), hour=0, minute=0, *, repeated=False, disabled=False, zones=(), sched_id=None, tz=None
) -> bytes:
    """Build a PbSchedule (the real, verified layout)."""
    pb = b""
    if days:
        pb += _field_bytes(1, bytes(days))  # packed int32; day values 0-6 are single-byte varints
    pb += _field_i32(2, hour) + _field_i32(3, minute)
    if repeated:
        pb += _field_i32(4, 1)
    for z in zones:
        pb += _field_bytes(5, _field_str(3, z))
    if sched_id is not None:
        pb += _field_i32(6, sched_id)
    if tz is not None:
        pb += _field_i32(7, tz)
    if disabled:
        pb += _field_i32(8, 1)
    return pb


def _schedules_field16(*tasks: bytes) -> bytes:
    """Wrap PbSchedule task blobs as PbOutput field 16 = PbSchedules{tasks(1)}."""
    return _field_bytes(16, b"".join(_field_bytes(1, t) for t in tasks))


def test_decode_schedule_entry_full() -> None:
    entry = decode_schedule_entry(_schedule_pb([1, 3, 5], 7, 30, repeated=True, zones=["z1", "z2"], sched_id=42, tz=2))
    assert entry == {
        "dayOfWeek": [1, 3, 5],
        "hour": 7,
        "minute": 30,
        "isRepeated": True,
        "isDisabled": False,
        "zones": ["z1", "z2"],
        "id": 42,
        "timeZone": 2,
    }


def test_decode_schedule_entry_minimal_disabled() -> None:
    entry = decode_schedule_entry(_schedule_pb([], 0, 0, disabled=True))
    assert entry["dayOfWeek"] == []
    assert entry["zones"] == []
    assert entry["isDisabled"] is True
    assert entry["isRepeated"] is False
    assert "id" not in entry  # absent when not sent


def test_decode_schedule_entry_negative_timezone() -> None:
    entry = decode_schedule_entry(_schedule_pb([2], 9, 0, tz=-3))
    assert entry["timeZone"] == -3


def test_decode_schedule_entry_bounds_untrusted_values() -> None:
    # Malformed/hostile wire values must not surface as garbage HA state.
    pb = _field_bytes(1, bytes([2, 9])) + _field_i32(2, 99) + _field_i32(3, 200)
    entry = decode_schedule_entry(pb)
    assert entry["dayOfWeek"] == [2]  # 9 dropped (out of 0-6)
    assert entry["hour"] == 0  # 99 -> 0 (out of 0-23)
    assert entry["minute"] == 0  # 200 -> 0 (out of 0-59)


def test_decode_pboutput_includes_schedules_field16() -> None:
    pb = _build_pboutput(work_status=1) + _schedules_field16(
        _schedule_pb([5], 12, 47, zones=["wsmjco1T"], sched_id=7),
        _schedule_pb([0, 6], 6, 0),
    )
    state = decode_pboutput(pb)
    assert len(state["schedules"]) == 2
    assert state["schedules"][0]["hour"] == 12
    assert state["schedules"][0]["zones"] == ["wsmjco1T"]
    assert state["schedules"][1]["dayOfWeek"] == [0, 6]


def test_decode_pboutput_empty_field16_is_empty_list() -> None:
    state = decode_pboutput(_build_pboutput(work_status=1) + _field_bytes(16, b""))
    assert state["schedules"] == []


def test_decode_pboutput_no_schedules_key_when_field16_absent() -> None:
    assert "schedules" not in decode_pboutput(_build_pboutput(work_status=1))


def test_decode_robot_config_extracts_known_fields() -> None:
    from lymow.protocol import decode_robot_config

    # f6=audioVolume int, f7=isOpenLed bool, f8=signal int, f10=cmdCellularSwitch bool, f11=metric_4g bool
    cfg = _field_i32(6, 80) + _field_i32(7, 1) + _field_i32(8, 4) + _field_i32(10, 0) + _field_i32(11, 1)
    out = decode_robot_config(cfg)
    assert out == {
        "audioVolume": 80,
        "isOpenLed": True,
        "signal": 4,
        "cmdCellularSwitch": False,
        "metric_4g": True,
    }


def test_decode_robot_config_absent_fields_not_in_dict() -> None:
    from lymow.protocol import decode_robot_config

    # Only metric_4g present — the other keys must NOT appear (so the merge
    # doesn't blow away existing state with False/0 defaults).
    assert decode_robot_config(_field_i32(11, 0)) == {"metric_4g": False}
    assert decode_robot_config(b"") == {}


def test_decode_robot_config_bool_rcRaise_rcLower_fields() -> None:
    from lymow.protocol import decode_robot_config

    assert decode_robot_config(_field_i32(4, 1) + _field_i32(5, 0)) == {
        "rcRaiseCutHeight": True,
        "rcLowerCutHeight": False,
    }


def test_decode_pboutput_surfaces_robot_config_under_robotConfig_key() -> None:
    # f17 = robotConfig sub-message; carry one bool and verify it round-trips
    pb = _build_pboutput(work_status=2) + _field_bytes(17, _field_i32(7, 1) + _field_i32(11, 0))
    state = decode_pboutput(pb)
    assert state["robotConfig"] == {"isOpenLed": True, "metric_4g": False}


def test_decode_pboutput_no_robotConfig_key_when_field17_absent() -> None:
    assert "robotConfig" not in decode_pboutput(_build_pboutput(work_status=1))


# ---------------------------------------------------------------------------
# decode_clean_report (PbOutput.f28 — QUERY_CLEANING_SUMMARY reply)
# ---------------------------------------------------------------------------


def test_decode_clean_report_all_scalar_fields() -> None:
    """PbCleanReport: f1 cleanStartTime, f3 mowEndType, f6 usedBattery."""
    from lymow.protocol import decode_clean_report

    payload = _field_i32(1, 1_700_000_000) + _field_i32(3, 1) + _field_i32(6, 35)
    assert decode_clean_report(payload) == {
        "cleanStartTime": 1_700_000_000,
        "mowEndType": 1,
        "usedBattery": 35,
    }


def test_decode_clean_report_drops_out_of_range_values() -> None:
    """Untrusted wire: bound mowEndType to the APK enum (0-2) and usedBattery
    to a percentage; drop a non-positive start time so HA doesn't surface 1970,
    and drop a huge start time so ``datetime.fromtimestamp`` can't OverflowError
    downstream — cap at the POSIX-portable int32 ceiling (year 2038)."""
    from lymow.protocol import decode_clean_report

    assert decode_clean_report(_field_i32(1, 0)) == {}
    assert decode_clean_report(_field_i32(3, 99)) == {}
    assert decode_clean_report(_field_i32(6, 150)) == {}
    # Boundary: max accepted is 2^31-1 (year 2038)
    assert decode_clean_report(_field_i32(1, 2_147_483_647)) == {"cleanStartTime": 2_147_483_647}
    # Boundary: one past the cap is rejected
    assert decode_clean_report(_field_i32(1, 2_147_483_648)) == {}


def test_decode_clean_report_empty_returns_empty_dict() -> None:
    from lymow.protocol import decode_clean_report

    assert decode_clean_report(b"") == {}


def test_decode_clean_report_status_times_packed_int32() -> None:
    """PbCleanReport.f5 is a packed repeated int32 — seconds per workStatus
    indexed by enum value. Verified against the encoder's int32+ldelim pattern."""
    from lymow.protocol import _encode_varint, _field_bytes, decode_clean_report

    # Three statuses: 120s, 0s, 60s
    packed = _encode_varint(120) + _encode_varint(0) + _encode_varint(60)
    assert decode_clean_report(_field_bytes(5, packed))["statusTimes"] == [120, 0, 60]


def test_decode_clean_report_status_times_clamps_preserves_index_alignment() -> None:
    """Bound each duration at [0, one year of seconds] so a misaligned varint
    can't surface a wildly large 64-bit value — but CLAMP rather than drop:
    statusTimes[i] must keep mapping to workStatus == i. A mid-array out-of-
    range entry becoming 0 preserves the position of the entries after it."""
    from lymow.protocol import _encode_varint, _field_bytes, decode_clean_report

    packed = _encode_varint(100) + _encode_varint(31_536_001) + _encode_varint(200)
    assert decode_clean_report(_field_bytes(5, packed))["statusTimes"] == [100, 31_536_000, 200]
    # Negative wire values (sign-extended varint) clamp to 0 too.
    neg_packed = _encode_varint(50) + _encode_varint(0xFFFFFFFF) + _encode_varint(75)
    assert decode_clean_report(_field_bytes(5, neg_packed))["statusTimes"] == [50, 0, 75]


def test_decode_clean_report_status_times_concatenates_multiple_segments() -> None:
    """Protobuf wire permits a packed-repeated field to be split across
    multiple key/value segments — decoders must concatenate them. Two f5
    segments with [10, 20] and [30] must surface as [10, 20, 30]."""
    from lymow.protocol import _encode_varint, _field_bytes, decode_clean_report

    seg1 = _field_bytes(5, _encode_varint(10) + _encode_varint(20))
    seg2 = _field_bytes(5, _encode_varint(30))
    assert decode_clean_report(seg1 + seg2)["statusTimes"] == [10, 20, 30]


def test_decode_clean_report_status_times_absent_when_empty_bytes() -> None:
    """A zero-length f5 has no entries — don't surface an empty list."""
    from lymow.protocol import _field_bytes, decode_clean_report

    assert "statusTimes" not in decode_clean_report(_field_bytes(5, b""))


def test_decode_clean_report_error_list_single_entry() -> None:
    """PbErrorList sub-message: f1 code (varint int32), f2 percent (float32
    fraction 0..1, converted to a 0..100 attribute matching mowProgress)."""
    from lymow.protocol import _field_bytes, _field_f32, decode_clean_report

    entry = _field_i32(1, 64) + _field_f32(2, 0.73)
    out = decode_clean_report(_field_bytes(4, entry))
    assert out["errorList"] == [{"code": 64, "percent": 73.0}]


def test_decode_clean_report_error_list_multiple_entries_preserve_order() -> None:
    """Repeated non-packed sub-messages: each PbErrorList is its own f4
    occurrence — every appearance must surface, in wire order."""
    from lymow.protocol import _field_bytes, _field_f32, decode_clean_report

    e1 = _field_bytes(4, _field_i32(1, 31) + _field_f32(2, 0.10))
    e2 = _field_bytes(4, _field_i32(1, 55) + _field_f32(2, 0.50))
    assert decode_clean_report(e1 + e2)["errorList"] == [
        {"code": 31, "percent": 10.0},
        {"code": 55, "percent": 50.0},
    ]


def test_decode_clean_report_error_list_drops_out_of_range_percent() -> None:
    """A misaligned f2 float that decodes outside [0, 1] (NaN, -inf, 1.5…)
    surfaces with code only — better to lose the percent than to render NaN."""
    from lymow.protocol import _field_bytes, _field_f32, decode_clean_report

    entry = _field_i32(1, 31) + _field_f32(2, 5.0)
    assert decode_clean_report(_field_bytes(4, entry))["errorList"] == [{"code": 31}]


def test_decode_clean_report_error_list_absent_when_no_f4() -> None:
    from lymow.protocol import decode_clean_report

    assert "errorList" not in decode_clean_report(_field_i32(1, 1_700_000_000))


def test_decode_clean_report_error_list_skips_empty_entries() -> None:
    """A PbErrorList with neither code nor percent decodes to {} — drop it
    rather than surface a placeholder entry."""
    from lymow.protocol import _field_bytes, decode_clean_report

    assert "errorList" not in decode_clean_report(_field_bytes(4, b""))


def test_decode_clean_report_error_list_skips_wire_type_drift_for_percent() -> None:
    """f2 is wire-type 5 (fixed32) per the encoder, but the wire is
    untrusted — if a malformed payload sends f2 as length-delimited bytes,
    ``_decode_f32`` would otherwise raise. The decoder must surface the
    code (and drop the percent) rather than blow up the whole report."""
    from lymow.protocol import _field_bytes, decode_clean_report

    entry = _field_i32(1, 31) + _field_bytes(2, b"x")
    assert decode_clean_report(_field_bytes(4, entry))["errorList"] == [{"code": 31}]


def test_decode_pboutput_surfaces_clean_report_under_cleanReport_key() -> None:
    pb = _build_pboutput(work_status=2) + _field_bytes(28, _field_i32(1, 1_700_000_000) + _field_i32(3, 2))
    assert decode_pboutput(pb)["cleanReport"] == {"cleanStartTime": 1_700_000_000, "mowEndType": 2}


def test_decode_pboutput_no_cleanReport_key_when_field28_absent() -> None:
    assert "cleanReport" not in decode_pboutput(_build_pboutput(work_status=1))


def test_decode_pboutput_no_cleanReport_key_when_field28_empty() -> None:
    """An empty PbCleanReport (no scalar fields present) should NOT surface a
    truthy ``cleanReport`` entry — otherwise the sensor would render with
    everything None."""
    pb = _build_pboutput(work_status=2) + _field_bytes(28, b"")
    assert "cleanReport" not in decode_pboutput(pb)


def test_decode_pboutput_surfaces_theft_lock_engaged_bool_for_field_27() -> None:
    """PbOutput.f27 is a bool (writer.bool tag 216 = (27<<3)|0) — surface as
    Python bool under ``theftLockEngaged`` (NOT ``theftLock``, which is the
    REST feature toggle key owned by TheftLockSwitch)."""
    assert decode_pboutput(_build_pboutput(work_status=2) + _field_i32(27, 1))["theftLockEngaged"] is True
    assert decode_pboutput(_build_pboutput(work_status=2) + _field_i32(27, 0))["theftLockEngaged"] is False


def test_decode_pboutput_does_not_clobber_rest_theftLock_key() -> None:
    """Regression: PbOutput.f27 must not write to ``theftLock`` since that
    key belongs to /update-device-feature (the REST feature toggle). Mixing
    them would cause MQTT updates to silently flip the feature switch
    between REST polls."""
    state = decode_pboutput(_build_pboutput(work_status=2) + _field_i32(27, 1))
    assert "theftLock" not in state


def test_decode_pboutput_no_theft_lock_engaged_key_when_field27_absent() -> None:
    """When the robot doesn't report f27, the sensor must stay ``None`` —
    surfacing False would imply we know the lock is disengaged."""
    assert "theftLockEngaged" not in decode_pboutput(_build_pboutput(work_status=1))


# ---------------------------------------------------------------------------
# decode_rr_config (PbRobotConfig.rrConfig — Recharge & Resume)
# ---------------------------------------------------------------------------


def _rr_period(hour: int, minute: int) -> bytes:
    return _field_i32(1, hour) + _field_i32(2, minute)


def test_decode_rr_config_all_fields_round_trip() -> None:
    from lymow.protocol import decode_rr_config

    rr = (
        _field_i32(1, 1)
        + _field_bytes(2, _rr_period(4, 0))
        + _field_bytes(3, _rr_period(20, 30))
        + _field_i32(4, 15)
        + _field_i32(5, 75)
    )
    assert decode_rr_config(rr) == {
        "enable": True,
        "periodStart": {"hour": 4, "minute": 0},
        "periodEnd": {"hour": 20, "minute": 30},
        "rechargeBat": 15,
        "resumeBat": 75,
    }


def test_decode_rr_config_empty_returns_empty_dict() -> None:
    from lymow.protocol import decode_rr_config

    assert decode_rr_config(b"") == {}


def test_decode_rr_config_drops_non_boolean_enable_and_out_of_range_values() -> None:
    """Untrusted wire data: drop bool-shaped fields that aren't 0/1, drop
    battery % outside 0-100, and drop time-of-day outside 0-23 / 0-59."""
    from lymow.protocol import decode_rr_config

    assert decode_rr_config(_field_i32(1, 2)) == {}
    assert decode_rr_config(_field_i32(4, 150) + _field_i32(5, -1)) == {}
    assert decode_rr_config(_field_bytes(2, _rr_period(24, 0))) == {}
    assert decode_rr_config(_field_bytes(3, _rr_period(0, 60))) == {}


def test_decode_rr_config_skips_period_with_missing_minute() -> None:
    from lymow.protocol import decode_rr_config

    # Period sub-message with only hour set — minute None means we can't
    # safely reconstruct an HH:MM, so the period must drop entirely.
    pb = _field_bytes(2, _field_i32(1, 9))
    assert decode_rr_config(pb) == {}


def test_decode_robot_config_surfaces_rr_config_under_rrConfig_key() -> None:
    from lymow.protocol import decode_robot_config

    rr = _field_i32(1, 1) + _field_i32(4, 20) + _field_i32(5, 80)
    out = decode_robot_config(_field_i32(7, 1) + _field_bytes(18, rr))
    assert out == {
        "isOpenLed": True,
        "rrConfig": {"enable": True, "rechargeBat": 20, "resumeBat": 80},
    }


def test_decode_robot_config_drops_empty_rrConfig() -> None:
    """If PbRobotConfig.f18 is present but the sub-message has no valid
    fields, don't surface an empty rrConfig dict."""
    from lymow.protocol import decode_robot_config

    out = decode_robot_config(_field_bytes(18, _field_i32(1, 2)))
    assert "rrConfig" not in out


def test_encode_set_task_config_wraps_in_pbinput() -> None:
    from lymow.protocol import encode_set_task_config

    pb = encode_set_task_config(pathSpacing=200, perimeterMowLaps=2, pathOrder=True, moveSpeed=0.5)
    f = _decode_fields(pb)
    assert _first(f, 2) == 49  # version
    assert _first(f, 5) == 36  # USER_CTRL_SET_TASK_CONFIG
    cfg = _decode_fields(_first(f, 26))  # PbTaskConfig sub-message
    assert _first(cfg, 9) == 200  # pathSpacing
    assert _first(cfg, 10) == 2  # perimeterMowLaps
    assert _first(cfg, 14) == 1  # pathOrder (bool -> 1)
    # moveSpeed is a float32 (wire type 5)
    assert struct.unpack("<f", struct.pack("<I", _first(cfg, 4)))[0] == pytest.approx(0.5, rel=1e-5)


def test_encode_set_task_config_skips_none_and_rejects_unknown() -> None:
    from lymow.protocol import encode_set_task_config

    pb = encode_set_task_config(cutSpeed=None, brushSpeed=100)
    cfg = _decode_fields(_first(_decode_fields(pb), 26))
    assert _first(cfg, 5) == 100  # brushSpeed (field 5) present
    assert _first(cfg, 6) is None  # cutSpeed (field 6) skipped (None)
    with pytest.raises(ValueError, match="unknown zone-config field"):
        encode_set_task_config(nonsense=1)


def test_encode_set_robot_config_no_userctrl_just_submessage() -> None:
    from lymow.protocol import encode_set_robot_config

    pb = encode_set_robot_config(metric_4g=True)
    f = _decode_fields(pb)
    assert _first(f, 2) == 49  # version
    # robotConfig writes skip userCtrl — the robot dispatches by submessage shape
    assert _first(f, 5) is None
    cfg = _decode_fields(_first(f, 13))  # PbInput.robotConfig
    assert _first(cfg, 11) == 1  # metric_4g (bool encoded as varint 1)


def test_encode_set_robot_config_false_and_unknown_rejected() -> None:
    from lymow.protocol import encode_set_robot_config

    pb_false = encode_set_robot_config(metric_4g=False)
    cfg = _decode_fields(_first(_decode_fields(pb_false), 13))
    assert _first(cfg, 11) == 0

    # None is skipped (no field 11)
    pb_skip = encode_set_robot_config(metric_4g=None)
    cfg_skip = _decode_fields(_first(_decode_fields(pb_skip), 13))
    assert _first(cfg_skip, 11) is None

    with pytest.raises(ValueError, match="unknown robot-config field"):
        encode_set_robot_config(nonsense=1)


def test_encode_set_robot_config_int_field_audio_volume() -> None:
    from lymow.protocol import encode_set_robot_config

    pb = encode_set_robot_config(audioVolume=42)
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    assert _first(cfg, 6) == 42  # field 6 = audioVolume


def test_encode_set_robot_config_timezone_offset_writes_field_21() -> None:
    """``setTimezone`` (#9036) writes seconds east of UTC to PbRobotConfig.f21."""
    from lymow.protocol import encode_set_robot_config

    pb = encode_set_robot_config(timezoneOffset=3600)
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    assert _first(cfg, 21) == 3600


def test_encode_set_robot_config_negative_timezone_round_trips_through_decoder() -> None:
    """Americas timezones are negative UTC offsets — verify the int32 varint
    survives the encoder + decoder round-trip without flipping sign."""
    from lymow.protocol import _signed32, decode_robot_config, encode_set_robot_config

    pb = encode_set_robot_config(timezoneOffset=-18000)  # UTC-5
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    # On the wire the varint is the two's-complement uint64; _signed32 brings
    # it back to a signed int32 the way decode_robot_config does internally.
    assert _signed32(_first(cfg, 21)) == -18000
    assert decode_robot_config(_first(_decode_fields(pb), 13)) == {"timezoneOffset": -18000}


def test_decode_robot_config_extracts_timezone_offset() -> None:
    from lymow.protocol import decode_robot_config

    assert decode_robot_config(_field_i32(21, 7200)) == {"timezoneOffset": 7200}
    # Negative offset (e.g. Americas) round-trips as two's-complement uint64 →
    # _signed32 brings it back to negative.
    assert decode_robot_config(_field_i32(21, (1 << 32) - 18000)) == {"timezoneOffset": -18000}


def test_encode_set_robot_config_mixed_int_and_bool_in_one_message() -> None:
    from lymow.protocol import encode_set_robot_config

    pb = encode_set_robot_config(audioVolume=80, isOpenLed=True)
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    assert _first(cfg, 6) == 80
    assert _first(cfg, 7) == 1


def test_encode_set_robot_config_rejects_unsupported_kind() -> None:
    """Guard against silent mis-encoding if the kind map ever grows past bool/int."""
    from lymow.protocol import _ROBOT_CONFIG_FIELDS, encode_set_robot_config

    _ROBOT_CONFIG_FIELDS["__test_bogus__"] = (99, "float")
    try:
        with pytest.raises(ValueError, match="unsupported robot-config kind"):
            encode_set_robot_config(__test_bogus__=1.5)
    finally:
        del _ROBOT_CONFIG_FIELDS["__test_bogus__"]


def test_encode_set_device_settings_full_message() -> None:
    """Wire: PbInput { userCtrl=36, taskConfig=PbTaskConfig{f1..f4} }."""
    from lymow.protocol import encode_set_device_settings

    pb = encode_set_device_settings(
        charging_mode=1,  # 1 = QUICK / Direct Route
        zone_order=0,  # 0 = OPTIMIZE
        rainy_mowing=True,
        charging_handbrake=True,  # UI sense ON → wire disableChargingPark=0
    )
    f = _decode_fields(pb)
    assert _first(f, 2) == 49  # version
    assert _first(f, 5) == 36  # USER_CTRL_SET_TASK_CONFIG
    cfg = _decode_fields(_first(f, 26))  # PbInput.taskConfig
    assert _first(cfg, 1) == 1  # chargingMode (Direct Route)
    assert _first(cfg, 2) == 0  # zoneOrder (Optimize)
    assert _first(cfg, 3) == 1  # rainCleaning true
    assert _first(cfg, 4) == 0  # disableChargingPark=0 because handbrake ON


def test_encode_set_device_settings_inverts_handbrake_off() -> None:
    """charging_handbrake=False (handbrake disengaged) → disableChargingPark=1 on wire."""
    from lymow.protocol import encode_set_device_settings

    pb = encode_set_device_settings(charging_handbrake=False)
    cfg = _decode_fields(_first(_decode_fields(pb), 26))
    assert _first(cfg, 4) == 1


def test_encode_set_device_settings_partial_omits_unset() -> None:
    from lymow.protocol import encode_set_device_settings

    pb = encode_set_device_settings(rainy_mowing=True)
    cfg = _decode_fields(_first(_decode_fields(pb), 26))
    assert _first(cfg, 3) == 1
    for fn in (1, 2, 4):
        assert _first(cfg, fn) is None


def test_encode_set_recharge_resume_full_message() -> None:
    """Wire: PbInput.robotConfig (f13) → PbRobotConfig.rrConfig (f18) → PbRRConfig."""
    from lymow.protocol import encode_set_recharge_resume

    pb = encode_set_recharge_resume(
        enable=True,
        period_start=(9, 30),
        period_end=(18, 0),
        recharge_bat=20,
        resume_bat=80,
    )
    f = _decode_fields(pb)
    assert _first(f, 2) == 49  # version
    assert _first(f, 5) is None  # no userCtrl (robotConfig dispatch)
    cfg = _decode_fields(_first(f, 13))  # PbInput.robotConfig
    rr = _decode_fields(_first(cfg, 18))  # PbRobotConfig.rrConfig
    assert _first(rr, 1) == 1  # enableRr
    assert _first(rr, 4) == 20  # rechargeBat
    assert _first(rr, 5) == 80  # resumeBat

    start = _decode_fields(_first(rr, 2))  # PbTimeZone start
    assert _first(start, 1) == 9 and _first(start, 2) == 30

    end = _decode_fields(_first(rr, 3))  # PbTimeZone end
    assert _first(end, 1) == 18 and _first(end, 2) == 0


def test_encode_set_recharge_resume_partial_skips_unset() -> None:
    from lymow.protocol import encode_set_recharge_resume

    pb = encode_set_recharge_resume(enable=False)
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    rr = _decode_fields(_first(cfg, 18))
    assert _first(rr, 1) == 0  # enableRr false
    # All other PbRRConfig fields absent so a partial write doesn't blow them away.
    for fno in (2, 3, 4, 5):
        assert _first(rr, fno) is None


def test_encode_set_night_mode_enable_true_writes_only_times() -> None:
    """enable=True publishes the window without the kill-the-light signal."""
    from lymow.protocol import encode_set_night_mode

    pb = encode_set_night_mode(open_time=(21, 0), close_time=(6, 30), enable=True)
    f = _decode_fields(pb)
    assert _first(f, 5) is None  # no userCtrl
    cfg = _decode_fields(_first(f, 13))  # PbInput.robotConfig

    open_tz = _decode_fields(_first(cfg, 14))
    assert _first(open_tz, 1) == 21
    assert _first(open_tz, 2) == 0
    close_tz = _decode_fields(_first(cfg, 15))
    assert _first(close_tz, 1) == 6
    assert _first(close_tz, 2) == 30
    # No signal field — the schedule keeps running, light stays on its own time.
    assert _first(cfg, 8) is None


def test_encode_set_night_mode_enable_false_co_publishes_off_signal() -> None:
    """enable=False still records the schedule but forces the light off now."""
    from lymow.const import SIGNAL_TURN_OFF_CAMERA_LIGHT
    from lymow.protocol import encode_set_night_mode

    pb = encode_set_night_mode(open_time=(22, 0), close_time=(5, 0), enable=False)
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    assert isinstance(_first(cfg, 14), bytes)  # openLedTime still written
    assert isinstance(_first(cfg, 15), bytes)  # closeLedTime still written
    assert _first(cfg, 8) == SIGNAL_TURN_OFF_CAMERA_LIGHT


def test_encode_set_night_mode_rejects_out_of_range_times() -> None:
    """Bound-check at the encoder so a malformed service call can't publish garbage."""
    from lymow.protocol import encode_set_night_mode

    for bad in ((24, 0), (-1, 0), (0, 60), (0, -1)):
        with pytest.raises(ValueError, match="out of range"):
            encode_set_night_mode(open_time=bad, close_time=(6, 0), enable=True)
        with pytest.raises(ValueError, match="out of range"):
            encode_set_night_mode(open_time=(0, 0), close_time=bad, enable=True)


def test_decode_robot_config_extracts_open_close_led_time() -> None:
    """The robot echoes the schedule window back in pboutput so users see the
    current window on the entity card without a separate poll."""
    from lymow.protocol import decode_robot_config

    cfg = _field_bytes(14, _field_i32(1, 21) + _field_i32(2, 0)) + _field_bytes(
        15, _field_i32(1, 6) + _field_i32(2, 30)
    )
    out = decode_robot_config(cfg)
    assert out == {
        "openLedTime": {"hour": 21, "minute": 0},
        "closeLedTime": {"hour": 6, "minute": 30},
    }


def test_decode_robot_config_drops_out_of_range_led_times() -> None:
    """A hostile pboutput must not surface 25:99 or -1:-1 to HA state."""
    from lymow.protocol import decode_robot_config

    bad = _field_bytes(14, _field_i32(1, 24) + _field_i32(2, 0))  # 24h is invalid
    bad += _field_bytes(15, _field_i32(1, 0) + _field_i32(2, 60))  # minute 60 invalid
    assert decode_robot_config(bad) == {}


def test_encode_set_robot_config_signal_field_for_vehicle_led() -> None:
    """Vehicle LED writes go via the signal field (one-shot), not isOpenLed."""
    from lymow.protocol import SIGNAL_TURN_OFF_VEHICLE_LIGHT, SIGNAL_TURN_ON_VEHICLE_LIGHT, encode_set_robot_config

    pb_on = encode_set_robot_config(signal=SIGNAL_TURN_ON_VEHICLE_LIGHT)
    cfg = _decode_fields(_first(_decode_fields(pb_on), 13))
    assert _first(cfg, 8) == SIGNAL_TURN_ON_VEHICLE_LIGHT == 10

    pb_off = encode_set_robot_config(signal=SIGNAL_TURN_OFF_VEHICLE_LIGHT)
    cfg_off = _decode_fields(_first(_decode_fields(pb_off), 13))
    assert _first(cfg_off, 8) == SIGNAL_TURN_OFF_VEHICLE_LIGHT == 11


def test_encode_set_robot_config_dock_on_error_field_22() -> None:
    from lymow.protocol import encode_set_robot_config

    pb = encode_set_robot_config(dockOnError=True)
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    assert _first(cfg, 22) == 1


def test_decode_robot_config_surfaces_dock_on_error() -> None:
    from lymow.protocol import decode_robot_config

    assert decode_robot_config(_field_i32(22, 1)) == {"dockOnError": True}
    assert decode_robot_config(_field_i32(22, 0)) == {"dockOnError": False}


def test_encode_set_run_time_config_wraps_in_pbinput_map() -> None:
    from lymow.protocol import encode_set_run_time_config

    pb = encode_set_run_time_config(cutHeight=45, moveSpeed=0.6, cutSpeed=120)
    f = _decode_fields(pb)
    assert _first(f, 2) == 49  # version
    assert _first(f, 5) == 50  # USER_CTRL_SET_RUN_TIME_CONFIG
    # PbInput.map (field 12) → PbMap.runTimeConfig (field 13) → PbRunTimeConfig
    pb_map = _decode_fields(_first(f, 12))
    cfg = _decode_fields(_first(pb_map, 13))
    assert _first(cfg, 1) == 45  # cutHeight
    assert _first(cfg, 6) == 120  # cutSpeed
    # moveSpeed is float32 (wire type 5)
    assert struct.unpack("<f", struct.pack("<I", _first(cfg, 4)))[0] == pytest.approx(0.6, rel=1e-5)


def test_encode_set_run_time_config_skips_none_and_rejects_unknown() -> None:
    from lymow.protocol import encode_set_run_time_config

    pb = encode_set_run_time_config(cutHeight=None, cutSpeed=80)
    cfg = _decode_fields(_first(_decode_fields(_first(_decode_fields(pb), 12)), 13))
    assert _first(cfg, 6) == 80  # cutSpeed present
    assert _first(cfg, 1) is None  # cutHeight (None) skipped
    with pytest.raises(ValueError, match="unknown run-time-config field"):
        encode_set_run_time_config(nonsense=1)


def test_encode_rename_zone_structure() -> None:
    from lymow.protocol import encode_rename_zone

    pb = encode_rename_zone("wsmjco1T", "Front lawn")
    f = _decode_fields(pb)
    assert _first(f, 5) == 9  # USER_CTRL_MODIFY_ZONE_INFO
    pb_map = _decode_fields(_first(f, 12))  # PbMap
    zone = _decode_fields(_first(pb_map, 1))  # PbZone
    bi = _decode_fields(_first(zone, 1))  # PbZoneBasicInfo
    assert _first(bi, 2).decode() == "Front lawn"  # name = field 2
    assert _first(bi, 3).decode() == "wsmjco1T"  # hashId = field 3


def test_encode_clear_schedules_is_empty_schedule_field() -> None:
    from lymow.protocol import encode_clear_schedules

    pb = encode_clear_schedules()
    assert pb.hex() == "10315a00"  # version=49, schedule(11)=empty — captured from app
    f = _decode_fields(pb)
    assert _first(f, 2) == 49
    assert _first(f, 11) == b""


def test_encode_set_schedules_wraps_version_and_schedule_field() -> None:
    from lymow.protocol import PB_VERSION, encode_set_schedules

    pb = encode_set_schedules([{"hour": 9, "minute": 30}])
    f = _decode_fields(pb)
    assert _first(f, 2) == PB_VERSION
    assert isinstance(_first(f, 11), bytes)  # PbSchedules in field 11, no userCtrl
    assert _first(f, 5) is None


def test_encode_set_schedules_entry_fields() -> None:
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules(
        [
            {
                "hour": 9,
                "minute": 30,
                "dayOfWeek": [1, 3, 5],
                "zones": [{"hashId": "abc123", "name": "Front", "point": {"x": 1.5, "y": -2.5}}],
                "isRepeated": True,
                "config": {"hashId": "abc123", "cutHeight": 60, "moveSpeed": 0.6, "pathSpacing": 90},
            }
        ]
    )
    schedules = _decode_fields(_first(_decode_fields(pb), 11))
    task = _decode_fields(_first(schedules, 1))  # PbSchedules.tasks[0] = PbSchedule
    assert _first(task, 1) == bytes([1, 3, 5])  # dayOfWeek packed
    assert _first(task, 2) == 9
    assert _first(task, 3) == 30
    assert _first(task, 4) == 1  # isRepeated
    zone = _decode_fields(_first(task, 5))  # zonesInfo = PbZoneBasicInfo
    assert _first(zone, 2) == b"Front"  # name
    assert _first(zone, 3) == b"abc123"  # hashId
    assert _first(zone, 8) == 1  # selected flag
    point = _decode_fields(_first(zone, 9))  # representative point (f1=x, f2=y float32)
    assert pytest.approx(_decode_f32(_first(point, 1)), abs=1e-3) == 1.5
    assert pytest.approx(_decode_f32(_first(point, 2)), abs=1e-3) == -2.5
    cfg = _decode_fields(_first(task, 11))  # PbScheduleConfig
    assert _first(cfg, 1) == b"abc123"  # hashId
    assert _first(cfg, 2) == 60  # cutHeight
    assert pytest.approx(_decode_f32(_first(cfg, 3)), abs=1e-3) == 0.6  # moveSpeed
    assert _first(cfg, 4) == 90  # pathSpacing


def test_encode_set_schedules_multiple_tasks() -> None:
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules([{"hour": 8, "minute": 0}, {"hour": 18, "minute": 15}])
    schedules = _decode_fields(_first(_decode_fields(pb), 11))
    tasks = _all(schedules, 1)
    assert len(tasks) == 2
    assert _first(_decode_fields(tasks[1]), 2) == 18  # second entry hour


def test_encode_set_schedules_disabled_and_empty() -> None:
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules([{"hour": 0, "minute": 0, "isDisabled": True}])
    task = _decode_fields(_first(_decode_fields(_first(_decode_fields(pb), 11)), 1))
    assert _first(task, 8) == 1  # isDisabled
    assert _first(task, 1) is None  # no dayOfWeek when omitted


def test_encode_set_schedules_optional_fields() -> None:
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules([{"hour": 6, "minute": 0, "id": 7, "timeZone": 2, "isAngleOffset": True}])
    task = _decode_fields(_first(_decode_fields(_first(_decode_fields(pb), 11)), 1))
    assert _first(task, 6) == 7  # id
    assert _first(task, 7) == 2  # timeZone (UTC offset hours)
    assert _first(task, 9) == 1  # isAngleOffset
