"""Wire-format snapshot tests pinning encoder byte output to frozen literals."""

from __future__ import annotations

import pytest
from lymow.protocol import (
    _decode_fields,
    _first,
    decode_channel,
    decode_clean_report,
    decode_map_response,
    decode_pboutput,
    decode_robot_config,
    decode_rr_config,
    decode_schedule_entry,
    decode_task_config,
    encode_ble_drive,
    encode_clear_schedules,
    encode_delete_channel,
    encode_delete_nogo_zone,
    encode_delete_zone,
    encode_query_map,
    encode_query_schedules,
    encode_rename_zone,
    encode_set_device_settings,
    encode_set_recharge_resume,
    encode_set_robot_config,
    encode_set_run_time_config,
    encode_set_schedules,
    encode_set_task_config,
    encode_start_zones,
    encode_userctrl,
)

# ---------------------------------------------------------------------------
# encode_userctrl — the simplest possible PbInput (version + userCtrl)
# ---------------------------------------------------------------------------
#
# Wire structure: PbInput { version(2)=49, userCtrl(5)=code }
#   tag 0x10 = (field 2 << 3) | wire 0 (varint)
#   0x31     = 49 (PB_VERSION)
#   tag 0x28 = (field 5 << 3) | wire 0
#   0xNN     = code


def test_userctrl_clean_is_byte_stable() -> None:
    """USER_CTRL_CLEAN=1 (start mowing)."""
    assert encode_userctrl(1).hex() == "10312801"


def test_userctrl_recharge_dock_is_byte_stable() -> None:
    """USER_CTRL_RECHARGE_DOCK=33 — sent by the Dock button."""
    assert encode_userctrl(33).hex() == "10312821"


def test_userctrl_pause_is_byte_stable() -> None:
    """USER_CTRL_PAUSE=3."""
    assert encode_userctrl(3).hex() == "10312803"


# ---------------------------------------------------------------------------
# encode_start_zones — PbInput { ..., map(12) = PbMap { goZones(1) = [...] } }
# Drift target: PbMap is at PbInput field 12. Each goZone has a PbZoneBasicInfo
# at field 1, with hashId at field 3 (length-delim) and index at field 8.
# ---------------------------------------------------------------------------


def test_start_zones_single_zone_is_byte_stable() -> None:
    expected = "1031280162100a0e0a0c1a0841424344303030314001"
    assert encode_start_zones(["ABCD0001"]).hex() == expected
    # Also assert it parses back the way we expect — guards against an encoder
    # bug that produces bytes that aren't valid protobuf.
    fields = _decode_fields(encode_start_zones(["ABCD0001"]))
    assert _first(fields, 5) == 1  # USER_CTRL_CLEAN
    pb_map = _first(fields, 12)
    assert isinstance(pb_map, bytes)
    go_zone = _first(_decode_fields(pb_map), 1)
    bi = _first(_decode_fields(go_zone), 1)
    assert _first(_decode_fields(bi), 3) == b"ABCD0001"


# ---------------------------------------------------------------------------
# encode_delete_zone — USER_CTRL_CLEAR_ZONE=8 + PbMap.goZones[].basicInfo.hashId
# ---------------------------------------------------------------------------


def test_delete_zone_is_byte_stable() -> None:
    expected = "10312808620e0a0c0a0a1a084142434430303031"
    assert encode_delete_zone("ABCD0001").hex() == expected


# ---------------------------------------------------------------------------
# encode_delete_channel — USER_CTRL_DELETE_CHANNEL + PbMap.channels[].hashId
# ---------------------------------------------------------------------------


def test_delete_channel_is_byte_stable() -> None:
    expected = "1031280e620c1a0a0a084348303030303031"
    assert encode_delete_channel("CH000001").hex() == expected


# ---------------------------------------------------------------------------
# encode_ble_drive — base64-ASCII-wrapped protobuf written to the BLE GATT
# drive characteristic. Both the inner wire format AND the base64 wrap are
# locked in (the robot's BLE firmware accepts only the wrapped form).
# ---------------------------------------------------------------------------


def test_ble_drive_zero_velocity_is_byte_stable() -> None:
    """Stop command — both velocities exactly 0.0."""
    # ASCII / base64. Decoding it must yield a PbInput with version=49,
    # sub-type=2 at f7, inner=(0.0, 0.0) at f10.
    assert encode_ble_drive(0.0, 0.0) == b"EDE4AlIKDQAAAAAVAAAAAA=="


def test_ble_drive_forward_03_no_rotation_is_byte_stable() -> None:
    # The inner protobuf carries 0.3 as IEEE 754 little-endian (\x9a\x99\x99\x3e).
    assert encode_ble_drive(0.3, 0.0) == b"EDE4AlIKDZqZmT4VAAAAAA=="


def test_ble_drive_backward_left_turn_is_byte_stable() -> None:
    assert encode_ble_drive(-0.2, 0.4) == b"EDE4AlIKDc3MTL4VzczMPg=="


# ---------------------------------------------------------------------------
# encode_set_task_config — PbInput { ..., taskConfig(26) = PbZoneConfig { ... } }
# Drift target: each named option must map to its documented PbZoneConfig
# field number (raiseCutHeight=2, moveSpeed=4, cutSpeed=6, perimeterMowLaps=10).
# ---------------------------------------------------------------------------


def test_set_task_config_subset_is_byte_stable() -> None:
    # Envelope: PbInput{f2:49, f5:49(GLOBAL_SETTING_N), f12 PbMap{f11 globalZoneConfig}}
    # — the live-confirmed mowing-settings path (cutSpeed f6, brushSpeed f5, perimeterMowLaps f10).
    expected = "1031283162085a06306428785002"
    out = encode_set_task_config(cutSpeed=100, brushSpeed=120, perimeterMowLaps=2)
    assert out.hex() == expected


# ---------------------------------------------------------------------------
# encode_set_recharge_resume — three levels deep, the wire format most likely
# to drift in a refactor.
#
# Path: PbInput.robotConfig(13) → PbRobotConfig.rrConfig(18) → PbRRConfig
#   PbRRConfig: enableRr(1, bool), resumePeriodStart(2, PbTimeZone),
#               resumePeriodEnd(3, PbTimeZone), rechargeBat(4, int),
#               resumeBat(5, int)
#   PbTimeZone: hour(1), minute(2)
# Also: no userCtrl is set (PbRobotConfig dispatches by shape).
# ---------------------------------------------------------------------------


def test_set_recharge_resume_full_message_is_byte_stable() -> None:
    expected = "10316a1592011208011204080910001a040812100020142850"
    out = encode_set_recharge_resume(
        enable=True,
        period_start=(9, 0),
        period_end=(18, 0),
        recharge_bat=20,
        resume_bat=80,
    )
    assert out.hex() == expected

    # Sanity-decode: verify the bytes really are at PbInput.robotConfig (f13),
    # PbRobotConfig.rrConfig (f18), and PbRRConfig.enableRr (f1).
    fields = _decode_fields(out)
    assert _first(fields, 5) is None  # NO userCtrl — robotConfig dispatch
    rc = _decode_fields(_first(fields, 13))
    rr = _decode_fields(_first(rc, 18))
    assert _first(rr, 1) == 1  # enableRr True
    assert _first(rr, 4) == 20  # rechargeBat
    assert _first(rr, 5) == 80  # resumeBat


# ---------------------------------------------------------------------------
# encode_set_robot_config — PbInput.robotConfig(13) = PbRobotConfig
# Drift targets: audioVolume=6 (int), metric_4g=11 (bool), dockOnError=22 (bool).
# ---------------------------------------------------------------------------


def test_set_robot_config_subset_is_byte_stable() -> None:
    expected = "10316a07303c5801b00100"
    out = encode_set_robot_config(audioVolume=60, metric_4g=True, dockOnError=False)
    assert out.hex() == expected


# ---------------------------------------------------------------------------
# encode_set_schedules — full PbSchedules.tasks at PbInput field 11.
# Each PbSchedule carries dayOfWeek (packed varint at f1), hour (f2), minute
# (f3), isRepeated (f4), zonesInfo (f5, PbZoneBasicInfo).
# ---------------------------------------------------------------------------


def test_set_schedules_single_entry_is_byte_stable() -> None:
    expected = "10315a1d0a1b0a030103051009181e20012a0e12001a0841424344303030314001"
    out = encode_set_schedules(
        [
            {
                "hour": 9,
                "minute": 30,
                "dayOfWeek": [1, 3, 5],
                "isRepeated": True,
                "zones": [{"hashId": "ABCD0001"}],
            }
        ]
    )
    assert out.hex() == expected


def test_decode_pboutput_synthetic_golden() -> None:
    """A frozen PbOutput payload must decode to the same flat dict each time."""
    # Re-build the golden bytes with the documented field layout so the test
    # documents itself rather than depending on a magic hex string in the file.
    from lymow.protocol import _encode_varint, _field_bytes, _field_f32, _field_i32, _field_str

    ri = (
        _field_i32(1, 2)
        + _field_i32(2, 87)
        + _field_i32(3, 3)
        + _field_i32(4, 2)
        + _field_i32(6, 5)
        + _field_i32(7, 0)
        + _field_i32(8, 1)
    )
    # Compose in field-number order: errs(3), ri(5), rtk(6), dp(10), pose(14), wifi(22).
    pb_sorted = (
        _field_bytes(3, _encode_varint(42) + _encode_varint(99))
        + _field_bytes(5, ri)
        + _field_bytes(6, _field_i32(1, 12) + _field_f32(2, 1.5) + _field_f32(3, 2.5) + _field_i32(4, 4))
        + _field_bytes(
            10,
            _field_str(1, "1.2.3")
            + _field_str(2, "4.5")
            + _field_str(5, "10.0.0.1")
            + _field_str(6, "aa:bb:cc:dd:ee:ff")
            + _field_str(7, "SN-DEMO"),
        )
        + _field_bytes(14, _field_f32(1, 10.0) + _field_f32(2, 20.0) + _field_f32(3, 1.57))
        + _field_bytes(22, _field_str(6, "-77"))
    )
    # Lock the resulting wire bytes as a golden snapshot — any helper tweak
    # that changes tag emission, length-prefix encoding, or float byte order
    # surfaces as a hex diff here before the decode assertions even run.
    expected_pb_hex = (
        "1a022a63"  # f3 errorCodes packed [42, 99]
        "2a0e0802105718032002300538004001"  # f5 PbRobotInfo (length 14)
        "320e080c150000c03f1d00002040200"
        "4"  # f6 RTK { sats=12, east=1.5, north=2.5, status=4 }
        "52320a05312e322e331203342e352a08"
        "31302e302e302e31321161613a62623a"
        "63633a64643a65653a66663a07534e2d"
        "44454d4f"  # f10 PbDeviceProfile (length 50)
        "720f0d00002041150000a0411dc3f5c83f"  # f14 pose
        "b2010532032d3737"  # f22 wifi rssi
    )
    assert pb_sorted.hex() == expected_pb_hex

    state = decode_pboutput(pb_sorted)
    assert state["errorCodes"] == [42, 99]
    assert state["workStatus"] == 5
    assert state["robotState"] == 2
    assert state["battery"] == 87
    assert state["isCharging"] is True
    assert state["isRecharging"] is False
    assert state["wifiSignalQuality"] == 3
    assert state["lteSignalQuality"] == 2
    assert state["fwVersion"] == "1.2.3"
    assert state["mcuVersion"] == "4.5"
    assert state["ipAddress"] == "10.0.0.1"
    assert state["macAddress"] == "aa:bb:cc:dd:ee:ff"
    assert state["sn"] == "SN-DEMO"
    assert state["rtkSatellites"] == 12
    assert state["rtkEastM"] == pytest.approx(1.5)
    assert state["rtkNorthM"] == pytest.approx(2.5)
    assert state["rtkStatus"] == 4
    assert state["poseEastM"] == pytest.approx(10.0)
    assert state["poseNorthM"] == pytest.approx(20.0)
    assert state["poseThetaRad"] == pytest.approx(1.57, abs=1e-6)
    assert state["wifiRssiDbm"] == -77


# ---------------------------------------------------------------------------
# decode_map_response — full PbOutput.f23 → f2 → f3 path with a go-zone,
# nogo-zone, charging-station and GPS origin.
# ---------------------------------------------------------------------------


def test_decode_map_response_synthetic_golden() -> None:
    """Pin nav path PbOutput→outer(23)→wrapper(2)→content(3) + child field numbers."""
    from lymow.protocol import _field_bytes, _field_f32, _field_i32, _field_str

    # Build content the same way decode_map_response expects.
    go_bi = (
        _field_i32(1, 1)
        + _field_str(3, "GOZONE01")
        + _field_i32(4, 1)
        + _field_bytes(5, _field_bytes(1, _field_f32(1, 0.0) + _field_f32(2, 0.0)))
    )
    go_pp = (
        _field_bytes(1, _field_f32(1, 0.0) + _field_f32(2, 0.0))
        + _field_bytes(2, _field_f32(1, 10.0) + _field_f32(2, 10.0))
        + _field_i32(3, 100)
        + _field_bytes(5, _field_f32(1, 5.0) + _field_f32(2, 5.0))
    )
    # PbZoneConfig: cutHeight f1 (int), pathSpacing f9 (int, cm).
    go_cfg = _field_i32(1, 40) + _field_i32(9, 20)
    go = _field_bytes(1, go_bi) + _field_bytes(3, go_pp) + _field_bytes(2, go_cfg)

    nogo_bi = _field_i32(1, 2) + _field_str(3, "NOGO0001") + _field_i32(4, 1)
    nogo_pp = _field_i32(3, 25) + _field_bytes(5, _field_f32(1, 2.0) + _field_f32(2, 2.0))
    nogo = _field_bytes(1, nogo_bi) + _field_bytes(3, nogo_pp) + _field_bytes(4, b"GOZONE01")

    cs = _field_f32(1, 1.0) + _field_f32(2, 1.0) + _field_f32(3, 0.0)
    gps = _field_f32(1, 59.0) + _field_f32(2, 18.0)

    content = _field_bytes(1, go) + _field_bytes(2, nogo) + _field_bytes(4, cs) + _field_bytes(7, gps)
    wrapper = _field_i32(1, 1) + _field_bytes(3, content)
    mr = _field_bytes(23, _field_bytes(2, wrapper))

    out = decode_map_response(mr)
    # Top-level keys
    assert set(out) >= {"goZones", "nogoZones", "channels", "chargingStation"}

    # Go zone
    z = out["goZones"][0]
    assert z["hashId"] == "GOZONE01"
    assert z["isEnabled"] is True
    assert z["type"] == 1
    assert z["area"] == 100
    assert z["boundMin"] == {"x": pytest.approx(0.0), "y": pytest.approx(0.0)}
    assert z["boundMax"] == {"x": pytest.approx(10.0), "y": pytest.approx(10.0)}
    assert z["innerPoint"] == {"x": pytest.approx(5.0), "y": pytest.approx(5.0)}
    assert z["cutHeight"] == 40
    assert z["pathSpacing"] == 20

    # Nogo zone — note: ``parentZoneHashId`` is the field that links it to its parent go-zone.
    n = out["nogoZones"][0]
    assert n["hashId"] == "NOGO0001"
    assert n["parentZoneHashId"] == "GOZONE01"
    assert n["area"] == 25
    assert n["innerPoint"]["x"] == pytest.approx(2.0)

    # Charging station + GPS origin
    assert out["chargingStation"] == {
        "x": pytest.approx(1.0),
        "y": pytest.approx(1.0),
        "theta": pytest.approx(0.0),
    }
    assert out["gpsOrigin"] == {"lat": pytest.approx(59.0), "lon": pytest.approx(18.0)}


# ---------------------------------------------------------------------------
# Parameterless commands — drift target: USER_CTRL code + envelope shape.
# (query_* are reads, clear_schedules is a mutating "delete all" command;
# grouped together because they share the no-payload envelope shape.)
# ---------------------------------------------------------------------------


def test_query_schedules_is_byte_stable() -> None:
    """USER_CTRL_QUERY_SCHEDULES=20, no payload."""
    assert encode_query_schedules().hex() == "10312814"


def test_clear_schedules_is_byte_stable() -> None:
    """No userCtrl is set — the robot routes on the PbInput.schedule sub-message
    (f11) being present-and-empty. Pinning so a refactor can't silently start
    setting userCtrl=USER_CTRL_SET_SCHEDULES=11 (the "set N entries" path)
    when the contract is "delete everything"."""
    assert encode_clear_schedules().hex() == "10315a00"


def test_query_map_full_is_byte_stable() -> None:
    """USER_CTRL_QUERY_MAP=19 with a queryIndex=0 PbBtMap sub-message — the
    default "give me the whole map" form."""
    assert encode_query_map(0).hex() == "10312813ba010408002001"


def test_query_map_diff_is_byte_stable() -> None:
    """queryIndex=1 is the smaller "incremental" form. Pin the int so a future
    encoder change can't silently flip the index direction."""
    assert encode_query_map(1).hex() == "10312813ba010408012001"


# ---------------------------------------------------------------------------
# Zone edits — single-target mutators
# ---------------------------------------------------------------------------


def test_delete_nogo_zone_is_byte_stable() -> None:
    """USER_CTRL_CLEAR_ZONE=8 + PbMap.nogoZones[].basicInfo.hashId — the
    no-go variant of encode_delete_zone."""
    assert encode_delete_nogo_zone("NOGO0001").hex() == "10312808620e120c0a0a1a084e4f474f30303031"


def test_rename_zone_is_byte_stable() -> None:
    """USER_CTRL_MODIFY_ZONE_INFO=9 + PbMap.goZones[] with basicInfo.hashId
    and a PbZoneBasicInfo name string (field 2). Smoke-tests the
    name-then-hashId field-order convention captured from customizeConfig."""
    assert encode_rename_zone("ZONE0001", "Lawn").hex() == "1031280962140a120a1012044c61776e1a085a4f4e4530303031"


# ---------------------------------------------------------------------------
# PbRobotConfig writes — robotConfig sub-message (PbInput.f13), no userCtrl
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PbTaskConfig writes — USER_CTRL_SET_TASK_CONFIG=36 + PbInput.taskConfig (f26)
# ---------------------------------------------------------------------------


def test_set_device_settings_full_is_byte_stable() -> None:
    """All four PbTaskConfig fields set: chargingMode (f1 int), zoneOrder
    (f2 int), rainCleaning (f3 bool), disableChargingPark (f4 bool — note
    the encoder's UI→wire inversion: charging_handbrake=True → wire 0)."""
    assert (
        encode_set_device_settings(charging_mode=1, zone_order=0, rainy_mowing=True, charging_handbrake=True).hex()
        == "10312824d201080801100018012000"
    )


# ---------------------------------------------------------------------------
# Runtime config writes — USER_CTRL_SET_RUN_TIME_CONFIG=50 + PbInput.map (f12)
# wrapping a PbMap.runTimeConfig (f13) with the PbRunTimeConfig sub-message.
# ---------------------------------------------------------------------------


def test_set_run_time_config_full_is_byte_stable() -> None:
    """cutHeight (f1, int) + moveSpeed (f2, float32) + cutSpeed (f3, int) —
    PbRunTimeConfig field map pinned to Hermes class #9456. Pins both the
    field-number map and the float32-vs-int wire types.
    NOTE(2026-06-19): a prior RE pass read moveSpeed/cutSpeed as f4/f6 (PbZoneConfig
    numbering); the deployed encoder uses f2/f3 — re-verify against the APK on the
    next capture pass (tracked for the 2026-07-01 cleanup)."""
    assert (
        encode_set_run_time_config(cutHeight=30, moveSpeed=0.5, cutSpeed=200).hex()
        == "10312832620c6a0a081e150000003f18c801"
    )


# ---------------------------------------------------------------------------
# Decoder snapshots — lock the wire-side field-number maps that pboutput and
# map_response *navigate to* but don't decode themselves. Drift target: a
# Hermes-RE'd field number changing under a rename, or a wire-type flip
# (varint vs. fixed32) silently widening a value through ``_first``.
# ---------------------------------------------------------------------------


def test_decode_task_config_all_fields_synthetic_golden() -> None:
    """PbTaskConfig — the four-field "Device Settings" sub-message:
    f1 chargingMode (int), f2 zoneOrder (int), f3 rainCleaning (bool),
    f4 disableChargingPark (bool). The two bool fields are *dropped* (key
    omitted) if the wire value isn't 0 or 1, not coerced — pinning the
    layout so a refactor can't silently start surfacing 2+ as True."""
    from lymow.protocol import _field_i32

    pb = _field_i32(1, 1) + _field_i32(2, 0) + _field_i32(3, 1) + _field_i32(4, 0)
    assert pb.hex() == "0801100018012000"
    assert decode_task_config(pb) == {
        "chargingMode": 1,
        "zoneOrder": 0,
        "rainCleaning": True,
        "disableChargingPark": False,
    }


def test_decode_channel_full_synthetic_golden() -> None:
    """PbChannel (path connector between two zones): f1 hashId, f2 zone1,
    f3 zone2, f4 isValid, f5 polygon (repeated points), f6 isDockingChannel,
    f9 cutHeight, f10 channelLift. Polygon at f5 is the same point-sub-message
    shape used elsewhere on the wire (PbMapPoint{f1=x, f2=y, both float32})."""
    from lymow.protocol import _field_bytes, _field_f32, _field_i32, _field_str

    poly = _field_bytes(1, _field_f32(1, 0.0) + _field_f32(2, 0.0)) + _field_bytes(
        1, _field_f32(1, 1.0) + _field_f32(2, 1.0)
    )
    pb = (
        _field_str(1, "CH000001")
        + _field_str(2, "ZONE0001")
        + _field_str(3, "ZONE0002")
        + _field_i32(4, 1)
        + _field_bytes(5, poly)
        + _field_i32(6, 1)
        + _field_i32(9, 40)
        + _field_i32(10, 1)
    )
    assert pb.hex() == (
        "0a08434830303030303112085a4f4e45303030311a085a4f4e453030303220012a180a0a0d000000001500000000"
        "0a0a0d0000803f150000803f300148285001"
    )
    out = decode_channel(pb)
    assert out["hashId"] == "CH000001"
    assert out["zone1"] == "ZONE0001"
    assert out["zone2"] == "ZONE0002"
    assert out["isValid"] is True
    assert out["isDockingChannel"] is True
    assert out["cutHeight"] == 40
    assert out["channelLift"] == 1
    assert out["polygon"] == [
        {"x": pytest.approx(0.0), "y": pytest.approx(0.0)},
        {"x": pytest.approx(1.0), "y": pytest.approx(1.0)},
    ]


def test_decode_rr_config_full_synthetic_golden() -> None:
    """PbRRConfig — mirrors :func:`encode_set_recharge_resume`'s wire output.
    Pins the read-side too so the round-trip (write then decode) can't drift:
    f1 enableRr (bool), f2/f3 period PbTimeZones, f4/f5 rechargeBat/resumeBat
    (int, bounded 0-100)."""
    from lymow.protocol import _field_bytes, _field_i32

    pb = (
        _field_i32(1, 1)
        + _field_bytes(2, _field_i32(1, 9) + _field_i32(2, 0))
        + _field_bytes(3, _field_i32(1, 18) + _field_i32(2, 0))
        + _field_i32(4, 20)
        + _field_i32(5, 80)
    )
    assert pb.hex() == "08011204080910001a040812100020142850"
    assert decode_rr_config(pb) == {
        "enable": True,
        "periodStart": {"hour": 9, "minute": 0},
        "periodEnd": {"hour": 18, "minute": 0},
        "rechargeBat": 20,
        "resumeBat": 80,
    }


def test_decode_robot_config_full_synthetic_golden() -> None:
    """PbRobotConfig — the wide settings sub-message published by the robot.
    Pins the full field map captured from PbRobotConfig.encode (Hermes #9506):
    int/bool varints at f4-f11 + f21/f22, PbTimeZones at f14/f15, and
    PbRRConfig at f18. Drift target: any single field number shifting silently
    breaks an entire settings entity but with no exception — a snapshot makes
    that visible at test time."""
    from lymow.protocol import _field_bytes, _field_i32

    tz_open = _field_i32(1, 21) + _field_i32(2, 0)
    tz_close = _field_i32(1, 6) + _field_i32(2, 30)
    rr = (
        _field_i32(1, 1)
        + _field_bytes(2, _field_i32(1, 9) + _field_i32(2, 0))
        + _field_bytes(3, _field_i32(1, 18) + _field_i32(2, 0))
        + _field_i32(4, 20)
        + _field_i32(5, 80)
    )
    pb = (
        _field_i32(4, 1)
        + _field_i32(5, 0)
        + _field_i32(6, 60)
        + _field_i32(7, 1)
        + _field_i32(8, 0)
        + _field_i32(10, 1)
        + _field_i32(11, 1)
        + _field_bytes(14, tz_open)
        + _field_bytes(15, tz_close)
        + _field_bytes(18, rr)
        + _field_i32(21, 3600)
        + _field_i32(22, 0)
    )
    assert pb.hex() == (
        "20012800303c38014000500158017204081510007a040806101e92011208011204080910001a040812100020142850a801901cb00100"
    )
    out = decode_robot_config(pb)
    assert out["audioVolume"] == 60
    assert out["signal"] == 0
    assert out["timezoneOffset"] == 3600
    assert out["rcRaiseCutHeight"] is True
    assert out["rcLowerCutHeight"] is False
    assert out["isOpenLed"] is True
    assert out["cmdCellularSwitch"] is True
    assert out["metric_4g"] is True
    assert out["dockOnError"] is False
    # f14/f15 decode under the UI-derived names (the deployed headlight schedule).
    assert out["headlightStart"] == {"hour": 21, "minute": 0}
    assert out["headlightEnd"] == {"hour": 6, "minute": 30}
    assert out["rrConfig"]["enable"] is True
    assert out["rrConfig"]["rechargeBat"] == 20


def test_decode_clean_report_full_synthetic_golden() -> None:
    """PbCleanReport — the QUERY_CLEANING_SUMMARY reply. Pins field shapes
    that are easy to drift on a refactor: f4 errorList is *non-packed* repeated
    sub-messages (each entry is its own length-delimited segment with a f1 int
    code and a f2 *float32* percent), and f5 statusTimes is a packed-int32
    array indexed positionally by workStatus."""
    import struct

    from lymow.protocol import _encode_varint, _field_bytes, _field_i32

    def _err(code: int, fraction: float) -> bytes:
        return _field_i32(1, code) + b"\x15" + struct.pack("<f", fraction)

    pb = (
        _field_i32(1, 1700000000)
        + _field_i32(3, 2)
        + _field_bytes(4, _err(42, 0.5))
        + _field_bytes(4, _err(99, 1.0))
        + _field_bytes(
            5,
            _encode_varint(0) + _encode_varint(120) + _encode_varint(60) + _encode_varint(0) + _encode_varint(30),
        )
        + _field_i32(6, 25)
    )
    assert pb.hex() == ("0880e2cfaa0618022207082a150000003f22070863150000803f2a0500783c001e3019")
    out = decode_clean_report(pb)
    assert out == {
        "cleanStartTime": 1700000000,
        "mowEndType": 2,
        "errorList": [{"code": 42, "percent": 50.0}, {"code": 99, "percent": 100.0}],
        "statusTimes": [0, 120, 60, 0, 30],
        "usedBattery": 25,
    }


def test_decode_schedule_entry_full_synthetic_golden() -> None:
    """PbSchedule — full entry layout: f1 dayOfWeek (packed varints), f2 hour,
    f3 minute, f4 isRepeated, f5 zonesInfo (PbZoneBasicInfo with hashId at *f3*,
    not f1), f6 id, f7 timeZone (int hours UTC offset), f8 isDisabled.
    The zonesInfo nesting is the easiest place to drift — pinning the byte
    string for a single ABCD0001 zone."""
    from lymow.protocol import _encode_varint, _field_bytes, _field_i32, _field_str

    pb = (
        _field_bytes(1, _encode_varint(1) + _encode_varint(3) + _encode_varint(5))
        + _field_i32(2, 9)
        + _field_i32(3, 30)
        + _field_i32(4, 1)
        + _field_bytes(5, _field_str(3, "ABCD0001"))
        + _field_i32(6, 7)
        + _field_i32(7, 0)
        + _field_i32(8, 0)
    )
    assert pb.hex() == "0a030103051009181e20012a0a1a084142434430303031300738004000"
    assert decode_schedule_entry(pb) == {
        "dayOfWeek": [1, 3, 5],
        "hour": 9,
        "minute": 30,
        "isRepeated": True,
        "isDisabled": False,
        "zones": ["ABCD0001"],
        "id": 7,
        "timeZone": 0,
    }
