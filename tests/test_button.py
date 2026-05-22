"""Tests for button.py — userCtrl command buttons."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lymow.button import (
    AbortOtaButton,
    BackupMapButton,
    ChargingStationResetButton,
    ClearAllZonesAndChannelsButton,
    CompleteZonePartitionButton,
    ExitRemoteControlButton,
    ForceReinitButton,
    LockRobotButton,
    RestoreFactoryDefaultsButton,
    SelfCheckButton,
    ToggleLteAirplaneButton,
    async_setup_entry,
)
from lymow.const import (
    DOMAIN,
    USER_CTRL_ABORT_OTA,
    USER_CTRL_CHARGING_STATION_RESET,
    USER_CTRL_CLEAR_ALL_ZONES_CHANNELS,
    USER_CTRL_COMPLETE_ZONE_PARTITION,
    USER_CTRL_EXIT_REMOTE,
    USER_CTRL_FORCE_REINIT,
    USER_CTRL_LOCK,
    USER_CTRL_RESTORE_FACTORY,
    USER_CTRL_SELF_CHECKING,
    USER_CTRL_SWITCH_LTE_AIRPLANE,
)

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord() -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.async_send_user_ctrl = AsyncMock()
    coord.async_backup_map = AsyncMock()
    return coord


def test_lock_button_metadata() -> None:
    coord = _make_coord()
    e = LockRobotButton(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_lock_robot"
    assert "Lock" in e._attr_name
    assert "Mower 1" in e._attr_name


def test_self_check_button_metadata() -> None:
    coord = _make_coord()
    e = SelfCheckButton(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_self_check"
    assert "Self-check" in e._attr_name


def test_force_reinit_button_disabled_by_default() -> None:
    coord = _make_coord()
    e = ForceReinitButton(coord, DEVICE)
    assert e._attr_entity_registry_enabled_default is False
    assert "Force stop" in e._attr_name


def test_charging_station_reset_button_disabled_by_default() -> None:
    coord = _make_coord()
    e = ChargingStationResetButton(coord, DEVICE)
    assert e._attr_entity_registry_enabled_default is False
    assert e._attr_unique_id == f"{THING}_charging_station_reset"


async def test_lock_button_press_sends_user_ctrl_lock() -> None:
    coord = _make_coord()
    e = LockRobotButton(coord, DEVICE)
    await e.async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_LOCK)


async def test_self_check_press_sends_user_ctrl_self_checking() -> None:
    coord = _make_coord()
    e = SelfCheckButton(coord, DEVICE)
    await e.async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_SELF_CHECKING)


async def test_force_reinit_press_sends_user_ctrl_force_reinit() -> None:
    coord = _make_coord()
    e = ForceReinitButton(coord, DEVICE)
    await e.async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_FORCE_REINIT)


async def test_charging_station_reset_press_sends_user_ctrl() -> None:
    coord = _make_coord()
    e = ChargingStationResetButton(coord, DEVICE)
    await e.async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_CHARGING_STATION_RESET)


async def test_button_name_fallback_to_sn() -> None:
    coord = _make_coord()
    e = LockRobotButton(coord, {"deviceThingName": THING, "sn": "SN42"})
    assert "SN42" in e._attr_name


async def test_async_setup_entry_creates_all_buttons_per_device() -> None:
    coord = _make_coord()

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    types = {type(e).__name__ for e in added}
    assert types == {
        "LockRobotButton",
        "SelfCheckButton",
        "ForceReinitButton",
        "ChargingStationResetButton",
        "AbortOtaButton",
        "CompleteZonePartitionButton",
        "ExitRemoteControlButton",
        "RestoreFactoryDefaultsButton",
        "ClearAllZonesAndChannelsButton",
        "ToggleLteAirplaneButton",
        "BackupMapButton",
    }


# ---------------------------------------------------------------------------
# Misc destructive / lifecycle buttons (#53)
# ---------------------------------------------------------------------------


def _check_disabled_default(cls) -> None:
    coord = _make_coord()
    e = cls(coord, DEVICE)
    assert e._attr_entity_registry_enabled_default is False


def test_abort_ota_button_disabled_by_default() -> None:
    _check_disabled_default(AbortOtaButton)


def test_complete_zone_partition_button_disabled_by_default() -> None:
    _check_disabled_default(CompleteZonePartitionButton)


def test_exit_remote_button_disabled_by_default() -> None:
    _check_disabled_default(ExitRemoteControlButton)


def test_restore_factory_button_disabled_by_default() -> None:
    _check_disabled_default(RestoreFactoryDefaultsButton)


def test_clear_all_zones_button_disabled_by_default() -> None:
    _check_disabled_default(ClearAllZonesAndChannelsButton)


async def test_abort_ota_press_sends_correct_userctrl() -> None:
    coord = _make_coord()
    await AbortOtaButton(coord, DEVICE).async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_ABORT_OTA)


async def test_complete_zone_partition_press_sends_correct_userctrl() -> None:
    coord = _make_coord()
    await CompleteZonePartitionButton(coord, DEVICE).async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_COMPLETE_ZONE_PARTITION)


async def test_exit_remote_press_sends_correct_userctrl() -> None:
    coord = _make_coord()
    await ExitRemoteControlButton(coord, DEVICE).async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_EXIT_REMOTE)


async def test_restore_factory_press_sends_correct_userctrl() -> None:
    coord = _make_coord()
    await RestoreFactoryDefaultsButton(coord, DEVICE).async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_RESTORE_FACTORY)


async def test_clear_all_zones_press_sends_correct_userctrl() -> None:
    coord = _make_coord()
    await ClearAllZonesAndChannelsButton(coord, DEVICE).async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_CLEAR_ALL_ZONES_CHANNELS)


def test_toggle_lte_airplane_button_disabled_by_default() -> None:
    _check_disabled_default(ToggleLteAirplaneButton)


def test_toggle_lte_airplane_button_metadata() -> None:
    coord = _make_coord()
    e = ToggleLteAirplaneButton(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_toggle_lte_airplane"
    assert "airplane" in e._attr_name.lower()


async def test_toggle_lte_airplane_press_sends_correct_userctrl() -> None:
    coord = _make_coord()
    await ToggleLteAirplaneButton(coord, DEVICE).async_press()
    coord.async_send_user_ctrl.assert_awaited_once_with(THING, USER_CTRL_SWITCH_LTE_AIRPLANE)


def test_backup_map_button_metadata() -> None:
    coord = _make_coord()
    e = BackupMapButton(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_backup_map"
    assert "Back up" in e._attr_name


async def test_backup_map_press_routes_through_coordinator() -> None:
    # Routes via async_backup_map (which invalidates the backup cache), not the
    # generic userCtrl path.
    coord = _make_coord()
    await BackupMapButton(coord, DEVICE).async_press()
    coord.async_backup_map.assert_awaited_once_with(THING)
    coord.async_send_user_ctrl.assert_not_called()


async def test_async_setup_entry_no_devices() -> None:
    coord = _make_coord()
    coord.devices = []

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert added == []
