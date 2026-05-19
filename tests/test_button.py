"""Tests for button.py — userCtrl command buttons."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lymow.button import (
    ChargingStationResetButton,
    ForceReinitButton,
    LockRobotButton,
    SelfCheckButton,
    async_setup_entry,
)
from lymow.const import (
    DOMAIN,
    USER_CTRL_CHARGING_STATION_RESET,
    USER_CTRL_FORCE_REINIT,
    USER_CTRL_LOCK,
    USER_CTRL_SELF_CHECKING,
)

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord() -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.async_send_user_ctrl = AsyncMock()
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


async def test_async_setup_entry_creates_four_buttons_per_device() -> None:
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
    }


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
