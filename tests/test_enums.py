"""Tests for enums.py — value/label mappings extracted from the APK."""

from __future__ import annotations

from lymow.enums import (
    CHARGING_MODE_LABELS,
    CHARGING_MODE_NORMAL,
    CHARGING_MODE_QUICK,
    CLEAN_MODE_ADAPTIVE_ZIGZAG,
    CLEAN_MODE_CHESS_BOARD,
    CLEAN_MODE_LABELS,
    CLEAN_MODE_NONE,
    CLEAN_MODE_PERIMETER_LAPS_ONLY,
    CLEAN_MODE_ZIGZAG,
    MOW_DIR_CLOCKWISE,
    MOW_DIR_COUNTERCLOCKWISE,
    MOW_DIR_LABELS,
    MOW_DIR_SHUFFLE,
    OBS_DEC_MODE_LABELS,
    OBS_DEC_MODE_NONE,
    OBS_DEC_MODE_SMART_HIGH,
    OBS_DEC_MODE_SMART_LOW,
    OBS_DEC_MODE_SMART_MEDIUM,
    OBS_DEC_MODE_TOUCH_ONLY,
    ROBOT_STATUS_LABELS,
    ZONE_ORDER_CUSTOM,
    ZONE_ORDER_LABELS,
    ZONE_ORDER_OPTIMIZE,
)


def test_charging_mode_values_match_apk_pbtaskconfig_fromObject() -> None:
    """NORMAL=0 / QUICK=1 (from PbTaskConfig.fromObject fn #9595)."""
    assert CHARGING_MODE_NORMAL == 0
    assert CHARGING_MODE_QUICK == 1
    assert set(CHARGING_MODE_LABELS) == {0, 1}


def test_zone_order_values_match_apk() -> None:
    assert ZONE_ORDER_OPTIMIZE == 0
    assert ZONE_ORDER_CUSTOM == 1
    assert set(ZONE_ORDER_LABELS) == {0, 1}


def test_clean_mode_has_all_five_known_values() -> None:
    """CLEAN_MODE_NONE=0 / ZIGZAG=1 / ADAPTIVE_ZIGZAG=2 / CHESS_BOARD=3 /
    PERIMETER_LAPS_ONLY=4 — five-value enum, not arbitrary int."""
    assert CLEAN_MODE_NONE == 0
    assert CLEAN_MODE_ZIGZAG == 1
    assert CLEAN_MODE_ADAPTIVE_ZIGZAG == 2
    assert CLEAN_MODE_CHESS_BOARD == 3
    assert CLEAN_MODE_PERIMETER_LAPS_ONLY == 4
    assert set(CLEAN_MODE_LABELS) == {0, 1, 2, 3, 4}


def test_mow_dir_includes_shuffle() -> None:
    """The MOW_DIR enum has three values (CW=0, CCW=1, Shuffle=2) — the
    previous services.yaml selector that allowed 0..3 was too permissive."""
    assert MOW_DIR_CLOCKWISE == 0
    assert MOW_DIR_COUNTERCLOCKWISE == 1
    assert MOW_DIR_SHUFFLE == 2
    assert set(MOW_DIR_LABELS) == {0, 1, 2}


def test_obs_dec_mode_has_five_levels() -> None:
    """OBS_DEC_MODE is off + touch-only + three smart-sensitivity levels —
    the prior 0..2 selector was missing the medium/low sensitivity values."""
    assert OBS_DEC_MODE_NONE == 0
    assert OBS_DEC_MODE_TOUCH_ONLY == 1
    assert OBS_DEC_MODE_SMART_HIGH == 2
    assert OBS_DEC_MODE_SMART_MEDIUM == 3
    assert OBS_DEC_MODE_SMART_LOW == 4
    assert set(OBS_DEC_MODE_LABELS) == {0, 1, 2, 3, 4}


def test_robot_status_labels_cover_known_values() -> None:
    """Each ROBOT_STATUS_* value the existing WORK_STATUS_* constants in
    const.py use must have a human label."""
    from lymow.const import (
        WORK_STATUS_CHARGING,
        WORK_STATUS_CHARGING_FULL,
        WORK_STATUS_DOCKING,
        WORK_STATUS_EMERGENCY_STOP,
        WORK_STATUS_ERROR,
        WORK_STATUS_ESCAPING,
        WORK_STATUS_MOWING,
        WORK_STATUS_NONE,
        WORK_STATUS_PAUSE,
        WORK_STATUS_PAUSE_DOCKING,
        WORK_STATUS_REMOTE_CONTROL,
        WORK_STATUS_RESUME,
        WORK_STATUS_RTT,
        WORK_STATUS_UPDATING,
        WORK_STATUS_WAITING,
        WORK_STATUS_ZONE_PARTITION,
    )

    known = {
        WORK_STATUS_NONE,
        WORK_STATUS_WAITING,
        WORK_STATUS_MOWING,
        WORK_STATUS_PAUSE,
        WORK_STATUS_DOCKING,
        WORK_STATUS_CHARGING,
        WORK_STATUS_REMOTE_CONTROL,
        WORK_STATUS_ERROR,
        WORK_STATUS_RESUME,
        WORK_STATUS_ZONE_PARTITION,
        WORK_STATUS_PAUSE_DOCKING,
        WORK_STATUS_UPDATING,
        WORK_STATUS_CHARGING_FULL,
        WORK_STATUS_EMERGENCY_STOP,
        WORK_STATUS_ESCAPING,
        WORK_STATUS_RTT,
    }
    assert known.issubset(ROBOT_STATUS_LABELS)


def test_all_label_dicts_have_matching_int_constants() -> None:
    """Every value in a *_LABELS dict must have a matching int constant in
    the module — catches a label being added without bumping the constant set."""
    import sys

    _enums = sys.modules["lymow.enums"]
    for name, value in vars(_enums).items():
        if not name.endswith("_LABELS") or not isinstance(value, dict):
            continue
        prefix = name.removesuffix("_LABELS") + "_"
        constants = {v for k, v in vars(_enums).items() if k.startswith(prefix) and isinstance(v, int)}
        if not constants:
            continue  # ROBOT_STATUS_LABELS has no per-value constants
        missing = constants - set(value)
        assert not missing, f"{name} missing labels for {sorted(missing)}"
