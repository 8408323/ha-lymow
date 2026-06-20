"""Snapshot tests for USER_CTRL_* command codes and button command mapping."""

from __future__ import annotations

import sys

import pytest
from lymow.const import (
    USER_CTRL_ABORT_OTA,
    USER_CTRL_CHARGING_STATION_RESET,
    USER_CTRL_CLEAR_ALL_ZONES_CHANNELS,
    USER_CTRL_CLEAR_ZONE,
    USER_CTRL_COMPLETE_ZONE_PARTITION,
    USER_CTRL_DOCK,
    USER_CTRL_EXIT_REMOTE,
    USER_CTRL_FLOOR_BACKUP,
    USER_CTRL_FORCE_REINIT,
    USER_CTRL_LOCK,
    USER_CTRL_MODIFY_STATION,
    USER_CTRL_RESTORE_FACTORY,
    USER_CTRL_SELF_CHECKING,
    USER_CTRL_SWITCH_LTE_AIRPLANE,
)

# grab the actual loaded module so we can introspect for every USER_CTRL_*
const = sys.modules["lymow.const"]


# ---------------------------------------------------------------------------
# USER_CTRL_* value snapshot — pin every documented command code
# ---------------------------------------------------------------------------

_EXPECTED_USER_CTRL: dict[str, int] = {
    "USER_CTRL_CLEAN": 1,
    "USER_CTRL_DOCK": 2,
    "USER_CTRL_PAUSE": 3,
    "USER_CTRL_RESUME": 4,
    "USER_CTRL_GO_ZONE_PARTITION": 5,
    "USER_CTRL_NO_GO_ZONE_PARTITION": 6,
    "USER_CTRL_EXIT_ZONE_PARTITION": 7,
    "USER_CTRL_CLEAR_ZONE": 8,
    "USER_CTRL_MODIFY_ZONE_INFO": 9,
    "USER_CTRL_MODIFY_ZONE_EDGE_START": 10,
    "USER_CTRL_MODIFY_ZONE_EDGE_STOP": 11,
    "USER_CTRL_CHANNEL_START": 12,
    "USER_CTRL_CHANNEL_FINISH": 13,
    "USER_CTRL_DELETE_CHANNEL": 14,
    "USER_CTRL_CLEAR_ALL_ZONES_CHANNELS": 15,
    "USER_CTRL_SELF_CHECKING": 16,
    "USER_CTRL_CHARGING_STATION_RESET": 17,
    "USER_CTRL_LOCK": 18,
    "USER_CTRL_QUERY_MAP": 19,
    "USER_CTRL_QUERY_SCHEDULES": 20,
    "USER_CTRL_PAUSE_DOCK": 21,
    "USER_CTRL_RESUME_DOCK": 22,
    "USER_CTRL_QUERY_PATH": 23,
    "USER_CTRL_QUERY_CLEANING_INFO": 24,
    "USER_CTRL_SYNC_MAP": 25,
    "USER_CTRL_OTA": 26,
    "USER_CTRL_ABORT_OTA": 27,
    "USER_CTRL_FORCE_REINIT": 28,
    "USER_CTRL_COMPLETE_ZONE_PARTITION": 29,
    "USER_CTRL_START_RECORDING": 30,
    "USER_CTRL_STOP_RECORDING": 31,
    "USER_CTRL_EXIT_REMOTE": 32,
    "USER_CTRL_RECHARGE_DOCK": 33,
    "USER_CTRL_QUERY_CLEANING_SUMMARY": 34,
    "USER_CTRL_QUERY_ROBOT_CONFIG": 35,
    "USER_CTRL_SET_TASK_CONFIG": 36,
    "USER_CTRL_RESTORE_FACTORY": 37,
    "USER_CTRL_MODIFY_STATION": 38,
    "USER_CTRL_QUERY_CHANNELS": 39,
    "USER_CTRL_FLOOR_SWITCH": 40,
    "USER_CTRL_FLOOR_ADD": 41,
    "USER_CTRL_FLOOR_DELETE": 42,
    "USER_CTRL_FLOOR_MODIFY": 43,
    "USER_CTRL_FLOOR_BACKUP": 44,
    "USER_CTRL_FLOOR_RESTORE": 45,
    "USER_CTRL_START_MOW_SCHEDULE": 46,
    "USER_CTRL_RESET_INIT": 47,
    "USER_CTRL_GLOBAL_SETTING_Y": 48,
    "USER_CTRL_GLOBAL_SETTING_N": 49,
    "USER_CTRL_SET_RUN_TIME_CONFIG": 50,
    "USER_CTRL_QUERY_RUN_TIME_CONFIG": 51,
    "USER_CTRL_QUERY_WIFI_4G": 52,
    "USER_CTRL_QUERY_NET_DETAIL": 53,
    "USER_CTRL_SWITCH_LTE_AIRPLANE": 54,
    "USER_CTRL_MERGE_ZONE": 55,
    "USER_CTRL_CUT_ZONE": 56,
    "USER_CTRL_QUERY_RTK_DIAGNOSTIC_L1": 57,
    "USER_CTRL_QUERY_RTK_DIAGNOSTIC_L2": 58,
    "USER_CTRL_MAX": 59,
}


@pytest.mark.parametrize("name,expected", sorted(_EXPECTED_USER_CTRL.items()))
def test_user_ctrl_value_pinned(name: str, expected: int) -> None:
    """Each documented USER_CTRL_* must match the APK-captured value."""
    actual = getattr(const, name)
    assert actual == expected, f"{name} = {actual}, expected {expected} (APK-pinned)"


def test_user_ctrl_snapshot_covers_every_constant_in_const_py() -> None:
    """A new USER_CTRL_* added without a pin here is a silent drift gap."""
    declared = {name for name in dir(const) if name.startswith("USER_CTRL_") and isinstance(getattr(const, name), int)}
    missing = declared - set(_EXPECTED_USER_CTRL)
    extra = set(_EXPECTED_USER_CTRL) - declared
    assert not missing, f"USER_CTRL_* declared in const.py but not pinned here: {sorted(missing)}"
    assert not extra, f"_EXPECTED_USER_CTRL references unknown USER_CTRL_*: {sorted(extra)}"


def test_user_ctrl_values_are_unique() -> None:
    """Duplicate int values would silently collapse distinct operations to one wire code."""
    by_value: dict[int, list[str]] = {}
    for name, val in _EXPECTED_USER_CTRL.items():
        by_value.setdefault(val, []).append(name)
    dupes = {v: names for v, names in by_value.items() if len(names) > 1}
    assert not dupes, f"USER_CTRL_* ints are not unique: {dupes}"


def test_user_ctrl_values_form_a_dense_range() -> None:
    """Constants must be contiguous 1..MAX with no gaps (matches APK enum table)."""
    values = sorted(_EXPECTED_USER_CTRL.values())
    assert values == list(range(1, _EXPECTED_USER_CTRL["USER_CTRL_MAX"] + 1)), (
        f"USER_CTRL_* values have gaps: missing {set(range(1, max(values) + 1)) - set(values)}"
    )


# ---------------------------------------------------------------------------
# Button → USER_CTRL mapping snapshot
# ---------------------------------------------------------------------------
#
# Each button class binds to a specific USER_CTRL_* via its _user_ctrl class
# attribute. Pinning this mapping catches both:
#   - A constant renamed in const.py while button.py is updated to a different
#     constant (a real subtle bug after merge conflicts).
#   - A button's _user_ctrl reassignment in a refactor.

_EXPECTED_BUTTON_MAPPING: dict[str, int] = {
    "LockRobotButton": USER_CTRL_LOCK,
    "SelfCheckButton": USER_CTRL_SELF_CHECKING,
    "CancelTaskButton": USER_CTRL_FORCE_REINIT,
    "DockAndForgetProgressButton": USER_CTRL_DOCK,
    "ChargingStationResetButton": USER_CTRL_CHARGING_STATION_RESET,
    "SetChargingStationHereButton": USER_CTRL_MODIFY_STATION,
    "AbortOtaButton": USER_CTRL_ABORT_OTA,
    "CompleteZonePartitionButton": USER_CTRL_COMPLETE_ZONE_PARTITION,
    "ExitRemoteControlButton": USER_CTRL_EXIT_REMOTE,
    "RestoreFactoryDefaultsButton": USER_CTRL_RESTORE_FACTORY,
    "ClearAllZonesAndChannelsButton": USER_CTRL_CLEAR_ALL_ZONES_CHANNELS,
    "ToggleLteAirplaneButton": USER_CTRL_SWITCH_LTE_AIRPLANE,
    "BackupMapButton": USER_CTRL_FLOOR_BACKUP,
}


@pytest.mark.parametrize("class_name,expected_ctrl", sorted(_EXPECTED_BUTTON_MAPPING.items()))
def test_button_user_ctrl_mapping(class_name: str, expected_ctrl: int) -> None:
    """Pin each ButtonEntity → USER_CTRL_* binding; changes require a snapshot bump."""
    button_mod = sys.modules.get("lymow.button")
    if button_mod is None:
        import importlib.util
        import os

        path = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow", "button.py")
        spec = importlib.util.spec_from_file_location("lymow.button", path)
        assert spec and spec.loader
        button_mod = importlib.util.module_from_spec(spec)
        sys.modules["lymow.button"] = button_mod
        spec.loader.exec_module(button_mod)
    cls = getattr(button_mod, class_name)
    assert cls._user_ctrl == expected_ctrl, f"{class_name}._user_ctrl = {cls._user_ctrl}, expected {expected_ctrl}"


def test_button_mapping_covers_every_user_ctrl_button() -> None:
    """Every _UserCtrlButton subclass must be pinned to catch later reassignments."""
    import importlib.util
    import inspect
    import os

    button_mod = sys.modules.get("lymow.button")
    if button_mod is None:
        path = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow", "button.py")
        spec = importlib.util.spec_from_file_location("lymow.button", path)
        assert spec and spec.loader
        button_mod = importlib.util.module_from_spec(spec)
        sys.modules["lymow.button"] = button_mod
        spec.loader.exec_module(button_mod)

    base = button_mod._UserCtrlButton
    subclasses = {
        name
        for name, obj in inspect.getmembers(button_mod, inspect.isclass)
        if obj is not base and issubclass(obj, base) and obj.__module__ == button_mod.__name__
    }
    missing = subclasses - set(_EXPECTED_BUTTON_MAPPING)
    extra = set(_EXPECTED_BUTTON_MAPPING) - subclasses
    assert not missing, f"_UserCtrlButton subclasses not pinned: {sorted(missing)}"
    assert not extra, f"_EXPECTED_BUTTON_MAPPING references unknown classes: {sorted(extra)}"


def test_button_classes_use_distinct_user_ctrl_values() -> None:
    """Two buttons sharing one USER_CTRL is almost always a copy-paste bug."""
    by_ctrl: dict[int, list[str]] = {}
    for cls_name, ctrl in _EXPECTED_BUTTON_MAPPING.items():
        by_ctrl.setdefault(ctrl, []).append(cls_name)
    dupes = {ctrl: names for ctrl, names in by_ctrl.items() if len(names) > 1}
    assert not dupes, f"two button classes bound to the same USER_CTRL: {dupes}"


# ---------------------------------------------------------------------------
# encode_userctrl values used inside protocol.py must match the pinned constants
# ---------------------------------------------------------------------------


def test_clear_zone_encoder_uses_pinned_ctrl_value() -> None:
    """delete-zone encoder must send USER_CTRL_CLEAR_ZONE=8 — catches independent drift."""
    from lymow.protocol import _decode_fields, _first, encode_delete_zone

    pb = encode_delete_zone("ABCD0001")
    fields = _decode_fields(pb)
    # PbInput.userCtrl is field 5.
    assert _first(fields, 5) == USER_CTRL_CLEAR_ZONE == 8
