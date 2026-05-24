"""Tests for select.py — VehicleLedModeSelect / CameraLedModeSelect."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lymow.select import (
    _LED_OPTIONS,
    CameraLedModeSelect,
    VehicleLedModeSelect,
    async_setup_entry,
)

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord(robot_config: dict | None = None) -> MagicMock:
    """The decoder lands robot-config state under data[thing]["robotConfig"]."""
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {"robotConfig": dict(robot_config)} if robot_config is not None else {}}
    coord.async_set_robot_config = AsyncMock()
    return coord


def test_options_are_off_on_auto_in_order() -> None:
    assert _LED_OPTIONS == ["off", "on", "auto"]


def test_vehicle_led_mode_unavailable_until_first_write() -> None:
    e = VehicleLedModeSelect(_make_coord(None), DEVICE)
    assert e.available is False
    assert e.current_option is None
    assert e._attr_unique_id == f"{THING}_vehLedStatus"


@pytest.mark.parametrize("raw,option", [(0, "off"), (1, "on"), (2, "auto")])
def test_vehicle_led_mode_decodes_each_state(raw: int, option: str) -> None:
    e = VehicleLedModeSelect(_make_coord({"vehLedStatus": raw}), DEVICE)
    assert e.available is True
    assert e.current_option == option


def test_vehicle_led_mode_unknown_value_returns_none() -> None:
    """Untrusted wire data: an unexpected int value (e.g. 7) shouldn't crash —
    the entity must report unknown state rather than picking a random option."""
    e = VehicleLedModeSelect(_make_coord({"vehLedStatus": 7}), DEVICE)
    assert e.current_option is None


async def test_vehicle_led_mode_select_writes_int_value() -> None:
    coord = _make_coord(None)
    e = VehicleLedModeSelect(coord, DEVICE)
    await e.async_select_option("auto")
    coord.async_set_robot_config.assert_awaited_once_with(THING, vehLedStatus=2)


async def test_camera_led_mode_select_writes_int_value() -> None:
    coord = _make_coord(None)
    e = CameraLedModeSelect(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_camLedStatus"
    await e.async_select_option("on")
    coord.async_set_robot_config.assert_awaited_once_with(THING, camLedStatus=1)


def test_camera_led_mode_reflects_cached_state() -> None:
    e = CameraLedModeSelect(_make_coord({"camLedStatus": 0}), DEVICE)
    assert e.current_option == "off"


async def test_async_setup_entry_adds_one_select_per_device_per_led() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord(None)
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    types = {type(e).__name__ for e in added}
    assert types == {"VehicleLedModeSelect", "CameraLedModeSelect"}


async def test_async_setup_entry_skips_when_no_devices() -> None:
    from lymow.const import DOMAIN

    coord = MagicMock()
    coord.devices = []
    coord.data = {}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert added == []
