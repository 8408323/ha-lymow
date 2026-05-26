"""Malformed-payload regression tests for protocol.py decoders/encoders."""

from __future__ import annotations

import struct

from lymow.protocol import (
    _decode_fields,
    _field_bytes,
    _field_f32,
    _field_i32,
    _field_str,
    _first,
    decode_map_response,
    decode_pboutput,
    decode_schedule_entry,
    delete_zone_from_raw_content,
    encode_set_recharge_resume,
)

# ---------------------------------------------------------------------------
# Helpers — minimal payload builders for malformed-shape tests
# ---------------------------------------------------------------------------


def _map_resp(content: bytes) -> bytes:
    """Wrap raw PbMap content in the f23 → f2 → f3 PbOutput envelope."""
    wrapper = _field_i32(1, 1) + _field_bytes(3, content)
    return _field_bytes(23, _field_bytes(2, wrapper))


def _go_zone(bi: bytes, pp: bytes | None = None, cfg: bytes | None = None) -> bytes:
    """Build a goZone (PbMap f1) submessage from its three sub-parts."""
    zone = _field_bytes(1, bi)
    if pp is not None:
        zone += _field_bytes(3, pp)
    if cfg is not None:
        zone += _field_bytes(2, cfg)
    return _field_bytes(1, zone)


def _nogo_zone(bi: bytes, pp: bytes | None = None, parent: bytes | None = None) -> bytes:
    """Build a nogoZone (PbMap f2) submessage."""
    nogo = _field_bytes(1, bi)
    if pp is not None:
        nogo += _field_bytes(3, pp)
    if parent is not None:
        nogo += _field_bytes(4, parent)
    return _field_bytes(2, nogo)


# ---------------------------------------------------------------------------
# decode_map_response — defensive guards on go-zone / nogo-zone fields
# ---------------------------------------------------------------------------


def test_go_zone_basic_info_with_wrong_wire_type_is_skipped() -> None:
    """Wrong-typed goZone.f1 (BasicInfo) skips BI branch — zone emitted without attrs."""
    zone_pb = _field_i32(1, 99) + _field_bytes(3, _field_i32(3, 0))
    content = _field_bytes(1, zone_pb)
    out = decode_map_response(_map_resp(content))
    assert len(out["goZones"]) == 1
    z = out["goZones"][0]
    assert "hashId" not in z and "polygon" not in z and "isEnabled" not in z


def test_go_zone_polygonProps_with_all_optional_fields_absent() -> None:
    """When polygonProps (f3) is present but empty, area / cut / inner stay absent."""
    bi = _field_str(3, "AAAAAAAA")
    out = decode_map_response(_map_resp(_go_zone(bi, pp=b"")))
    z = out["goZones"][0]
    assert "area" not in z
    assert "boundMin" not in z and "boundMax" not in z and "innerPoint" not in z


def test_go_zone_config_with_all_optional_fields_absent() -> None:
    """A zone config submessage with no cutHeight or pathSpacing must not set them."""
    bi = _field_str(3, "AAAAAAAA")
    out = decode_map_response(_map_resp(_go_zone(bi, cfg=_field_i32(99, 7))))
    z = out["goZones"][0]
    assert "cutHeight" not in z and "pathSpacing" not in z


def test_nogo_zone_basic_info_with_wrong_wire_type_is_skipped() -> None:
    """Mirror of the goZone guard — nogoZone with non-bytes BasicInfo is skipped."""
    nogo_pb = _field_i32(1, 99) + _field_bytes(3, _field_i32(3, 0))
    content = _field_bytes(2, nogo_pb)
    out = decode_map_response(_map_resp(content))
    assert len(out["nogoZones"]) == 1
    n = out["nogoZones"][0]
    assert "hashId" not in n


def test_nogo_zone_polygonProps_without_area_or_inner() -> None:
    """nogo with present-but-empty polygonProps: area and innerPoint absent."""
    bi = _field_str(3, "BBBBBBBB")
    out = decode_map_response(_map_resp(_nogo_zone(bi, pp=b"")))
    n = out["nogoZones"][0]
    assert "area" not in n and "innerPoint" not in n


def test_channel_with_wrong_wire_type_is_skipped() -> None:
    """A channel field (PbMap f3) that arrives as a varint must not crash."""
    content = _field_i32(3, 99)  # channel slot, but wire type 0
    out = decode_map_response(_map_resp(content))
    assert out["channels"] == []


# ---------------------------------------------------------------------------
# decode_pboutput — defensive guards on PbRobotInfo + pose + wifi
# ---------------------------------------------------------------------------


def _pboutput(*field_blobs: bytes) -> bytes:
    return b"".join(field_blobs)


def test_pboutput_robot_info_without_battery_does_not_set_battery() -> None:
    """PbRobotInfo (f5) with workStatus only → battery key is absent on state."""
    ri = _field_i32(6, 1)  # workStatus only
    state = decode_pboutput(_pboutput(_field_bytes(5, ri)))
    assert "workStatus" in state
    assert "battery" not in state


def test_pboutput_wifi_rssi_with_wrong_wire_type_is_skipped() -> None:
    """wifi.f6 (rssi) arriving as a varint instead of bytes must not surface a value."""
    wifi = _field_i32(6, -77)  # wrong wire type (should be string bytes)
    state = decode_pboutput(_pboutput(_field_bytes(22, wifi)))
    assert "wifiRssiDbm" not in state


def test_pboutput_pose_with_only_east_present() -> None:
    """A partial pose submessage sets only the present coordinates."""
    pose = _field_f32(1, 1.5)  # eastM only
    state = decode_pboutput(_pboutput(_field_bytes(14, pose)))
    assert state["poseEastM"] == 1.5
    assert "poseNorthM" not in state and "poseThetaRad" not in state


def test_pboutput_pose_with_north_no_east() -> None:
    pose = _field_f32(2, 2.5)  # northM only
    state = decode_pboutput(_pboutput(_field_bytes(14, pose)))
    assert state["poseNorthM"] == 2.5
    assert "poseEastM" not in state and "poseThetaRad" not in state


def test_pboutput_pose_with_theta_only() -> None:
    pose = _field_f32(3, 0.5)
    state = decode_pboutput(_pboutput(_field_bytes(14, pose)))
    assert state["poseThetaRad"] == 0.5
    assert "poseEastM" not in state and "poseNorthM" not in state


# ---------------------------------------------------------------------------
# decode_schedule_entry — zones-list defensive guards
# ---------------------------------------------------------------------------


def test_schedule_zone_with_wrong_wire_type_is_skipped() -> None:
    """A PbSchedule.zones (f5) entry arriving as a varint must not produce a hash."""
    entry_pb = _field_i32(5, 99)  # zone slot, but varint instead of submessage
    out = decode_schedule_entry(entry_pb)
    assert out["zones"] == []


def test_schedule_zone_basic_info_without_hash_is_dropped() -> None:
    """PbZoneBasicInfo without f3 (hashId) — zone is silently skipped, not crashed."""
    bi_no_hash = _field_str(2, "name only")  # f2 name, no f3 hashId
    entry_pb = _field_bytes(5, bi_no_hash)
    out = decode_schedule_entry(entry_pb)
    assert out["zones"] == []


# ---------------------------------------------------------------------------
# delete_zone_from_raw_content — wire-type fall-throughs and parent mismatch
# ---------------------------------------------------------------------------


def test_delete_zone_keeps_unrelated_nogo_zone() -> None:
    """A nogoZone with a different parent must survive deletion of the go-zone."""
    keep_bi = _field_str(3, "NOGO1234")
    keep_parent = b"OTHERGZ_"  # 8 chars, NOT the hashId we delete
    keep_nogo = _field_bytes(2, _field_bytes(1, keep_bi) + _field_bytes(4, keep_parent))

    delete_bi = _field_str(3, "GOZONEXX")
    delete_go = _field_bytes(1, _field_bytes(1, delete_bi))

    content = delete_go + keep_nogo
    out = delete_zone_from_raw_content(content, "GOZONEXX")
    fields = list(_decode_fields(out))
    # The unrelated nogoZone (f2) must still be present.
    assert any(fn == 2 and isinstance(val, bytes) for fn, _, val in fields)
    # The deleted goZone (f1) must NOT be present.
    assert not any(fn == 1 and isinstance(val, bytes) for fn, _, val in fields)


def test_delete_zone_preserves_fixed32_wire_field() -> None:
    """Unknown 32-bit-fixed (wire type 5) fields must be re-encoded byte-for-byte."""
    # Use field number 5 so the tag fits in a single byte: (5 << 3) | 5 = 45.
    tag = bytes([(5 << 3) | 5])
    fixed = struct.pack("<I", 0x01020304)
    content = tag + fixed
    out = delete_zone_from_raw_content(content, "NOMATCH_")
    # The fixed field round-trips verbatim, plus the appended f9 modifyHashs tag.
    assert out.startswith(tag + fixed)


def test_delete_zone_preserves_fixed64_wire_field() -> None:
    """Unknown 64-bit-fixed (wire type 1) fields must also round-trip."""
    tag = bytes([(6 << 3) | 1])
    fixed = struct.pack("<Q", 0x0102030405060708)
    content = tag + fixed
    out = delete_zone_from_raw_content(content, "NOMATCH_")
    assert out.startswith(tag + fixed)


def test_delete_zone_loop_continues_past_repeated_fixed_fields() -> None:
    """Multiple wire-5 fields force loop back-edge coverage (protocol.py 254→239)."""
    tag5 = bytes([(5 << 3) | 5])
    f5 = tag5 + struct.pack("<I", 0x11111111)
    # A varint field 7 (wire type 0), value 42 → tag 0x38, then 0x2a.
    f_varint = bytes([(7 << 3) | 0, 42])
    out = delete_zone_from_raw_content(f5 + f_varint, "NOMATCH_")
    # Both fields preserved in original order, in front of the appended modifyHashs.
    assert out.startswith(f5 + f_varint)


# ---------------------------------------------------------------------------
# encode_set_recharge_resume — every optional kwarg has its own skip path
# ---------------------------------------------------------------------------


def _rr_fields(pb: bytes) -> dict[int, object]:
    """Helper: dig into PbInput.robotConfig.rrConfig and return its sub-fields by number."""
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    rr = _decode_fields(_first(cfg, 18))
    return {fn: val for fn, _, val in rr}


def test_recharge_resume_enable_omitted_writes_only_other_fields() -> None:
    """enable=None must skip f1 entirely (covers the `if enable is not None:` False branch)."""
    pb = encode_set_recharge_resume(recharge_bat=42)
    rr = _rr_fields(pb)
    assert 1 not in rr  # enableRr absent
    assert rr[4] == 42  # rechargeBat present


def test_recharge_resume_period_start_omitted_skips_field2() -> None:
    pb = encode_set_recharge_resume(period_end=(18, 0))
    rr = _rr_fields(pb)
    assert 2 not in rr
    assert 3 in rr  # period_end present


def test_recharge_resume_period_end_omitted_skips_field3() -> None:
    pb = encode_set_recharge_resume(period_start=(9, 0))
    rr = _rr_fields(pb)
    assert 2 in rr
    assert 3 not in rr


def test_recharge_resume_recharge_bat_omitted_skips_field4() -> None:
    pb = encode_set_recharge_resume(resume_bat=80)
    rr = _rr_fields(pb)
    assert 4 not in rr
    assert rr[5] == 80


def test_recharge_resume_resume_bat_omitted_skips_field5() -> None:
    pb = encode_set_recharge_resume(recharge_bat=20)
    rr = _rr_fields(pb)
    assert 5 not in rr
    assert rr[4] == 20


# ---------------------------------------------------------------------------
# Schedule encoder branches — _encode_zone_basic_info / _encode_schedule_config
# These are exercised indirectly through encode_set_schedules, but the optional
# branches (point absent, each PbScheduleConfig key absent) aren't directly hit.
# ---------------------------------------------------------------------------


def test_encode_schedules_omits_point_when_zone_has_no_point() -> None:
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules([{"hour": 9, "minute": 0, "zones": [{"hashId": "ZZZZZZZZ"}]}])
    # Drill: PbInput.f25 (schedules) → PbSchedules.tasks (f1) → PbSchedule.zonesInfo (f5)
    schedules_blob = _first(_decode_fields(pb), 11)
    task_blob = _first(_decode_fields(schedules_blob), 1)
    zone_info_blob = _first(_decode_fields(task_blob), 5)
    zi = _decode_fields(zone_info_blob)
    # f9 (representative point) must be absent when caller didn't supply it.
    assert _first(zi, 9) is None
    assert _first(zi, 3) == b"ZZZZZZZZ"  # hashId preserved


def test_encode_schedules_config_omits_each_unset_field() -> None:
    from lymow.protocol import encode_set_schedules

    # Only cutHeight set in config — pathSpacing, moveSpeed, hashId must be absent.
    pb = encode_set_schedules(
        [
            {
                "hour": 9,
                "minute": 0,
                "zones": [{"hashId": "ZZZZZZZZ"}],
                "config": {"cutHeight": 40},
            }
        ]
    )
    schedules_blob = _first(_decode_fields(pb), 11)
    task_blob = _first(_decode_fields(schedules_blob), 1)
    cfg_blob = _first(_decode_fields(task_blob), 11)  # PbSchedule.config (f11)
    cfg = _decode_fields(cfg_blob)
    assert _first(cfg, 2) == 40  # cutHeight present
    assert _first(cfg, 1) is None  # hashId absent
    assert _first(cfg, 3) is None  # moveSpeed absent
    assert _first(cfg, 4) is None  # pathSpacing absent


def test_encode_schedules_config_with_only_movespeed_skips_cutheight_path() -> None:
    """Schedule config with moveSpeed but no cutHeight must skip the cutHeight branch."""
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules(
        [
            {
                "hour": 9,
                "minute": 0,
                "zones": [{"hashId": "ZZZZZZZZ"}],
                "config": {"moveSpeed": 0.5},
            }
        ]
    )
    schedules_blob = _first(_decode_fields(pb), 11)
    task_blob = _first(_decode_fields(schedules_blob), 1)
    cfg_blob = _first(_decode_fields(task_blob), 11)
    cfg = _decode_fields(cfg_blob)
    assert _first(cfg, 2) is None  # cutHeight absent
    assert _first(cfg, 3) is not None  # moveSpeed present


def test_encode_schedules_entry_omits_hour_when_absent() -> None:
    """A schedule entry without hour/minute keys must still encode cleanly."""
    from lymow.protocol import encode_set_schedules

    pb = encode_set_schedules([{"zones": [{"hashId": "ZZZZZZZZ"}]}])
    schedules_blob = _first(_decode_fields(pb), 11)
    task_blob = _first(_decode_fields(schedules_blob), 1)
    t = _decode_fields(task_blob)
    assert _first(t, 2) is None  # hour absent
    assert _first(t, 3) is None  # minute absent


# ---------------------------------------------------------------------------
# PbCleanInfo.cleanPercent (f5) — out-of-range bound check
# ---------------------------------------------------------------------------


def test_pboutput_mowprogress_above_one_is_dropped() -> None:
    """A misaligned decode could surface a float >> 1.0 here. Without the
    bound, the sensor would render '12300%' or similar garbage."""
    import struct

    # f5 = cleanPercent as float32 with raw bits forming 123.0 (clearly invalid).
    bad_pct_bytes = struct.pack("<f", 123.0)
    bad_pct_raw = int.from_bytes(bad_pct_bytes, "little")
    area_pb = bytes([(5 << 3) | 5]) + bad_pct_bytes  # tag = field 5, wire 5
    pb = bytes([(12 << 3) | 2, len(area_pb)]) + area_pb  # outer field 12
    state = decode_pboutput(pb)
    assert "mowProgress" not in state, (
        f"out-of-range cleanPercent ({bad_pct_raw=}) should be dropped, got {state.get('mowProgress')}"
    )


def test_pboutput_mowprogress_nan_is_dropped() -> None:
    """A NaN float (e.g. from a misaligned 4-byte boundary) must not surface
    as a NaN sensor state — comparisons with NaN are False so the
    `0.0 <= pct <= 1.0` guard cleanly rejects it."""
    import struct

    nan_bytes = struct.pack("<f", float("nan"))
    area_pb = bytes([(5 << 3) | 5]) + nan_bytes
    pb = bytes([(12 << 3) | 2, len(area_pb)]) + area_pb
    state = decode_pboutput(pb)
    assert "mowProgress" not in state


def test_pboutput_mowprogress_in_range_is_surfaced() -> None:
    """Sanity: the bound check doesn't reject the documented 0..1 range."""
    import struct

    half = struct.pack("<f", 0.5)
    area_pb = bytes([(5 << 3) | 5]) + half
    pb = bytes([(12 << 3) | 2, len(area_pb)]) + area_pb
    state = decode_pboutput(pb)
    assert state["mowProgress"] == 50.0


def test_pboutput_mowprogress_with_wrong_wire_type_is_skipped() -> None:
    """f5 arriving as length-delimited bytes (wire type 2 instead of fixed32 wire 5)
    must not crash _decode_f32 at struct.pack — Copilot review #196 flagged this."""
    # f5 wire-type 2, length 4, payload doesn't matter.
    area_pb = bytes([(5 << 3) | 2, 4]) + b"\x00\x00\x00\x00"
    pb = bytes([(12 << 3) | 2, len(area_pb)]) + area_pb
    state = decode_pboutput(pb)  # must not raise
    assert "mowProgress" not in state
