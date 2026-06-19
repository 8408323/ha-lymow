"""Tests for select.py — Device Settings dropdowns."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import _load_lymow_module

_load_lymow_module("select")

from lymow.const import CHARGING_MODES, DOMAIN, ZONE_ORDERS  # noqa: E402
from lymow.select import (  # noqa: E402
    _CHARGING_MODE_OPTIONS,
    _ZONE_ORDER_OPTIONS,
    ChargingModeSelect,
    ZoneOrderSelect,
    async_setup_entry,
)

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Test Mower"}


def _make_coord(task_config: dict | None = None) -> MagicMock:
    coord = MagicMock()
    state: dict = {"mapData": {}}
    if task_config is not None:
        state["mapData"]["taskConfig"] = task_config
    coord.data = {THING: state}
    coord.devices = [DEVICE]
    coord.async_set_device_settings = AsyncMock()
    return coord


# ---------------------------------------------------------------------------
# ChargingModeSelect
# ---------------------------------------------------------------------------


def test_charging_mode_unique_id_and_options() -> None:
    e = ChargingModeSelect(_make_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_charging_mode"
    assert e._attr_name == "Return-to-dock route"
    assert e._attr_options == ["Follow perimeter", "Direct route"]


def test_charging_mode_reads_current_value() -> None:
    e = ChargingModeSelect(_make_coord({"chargingMode": 1}), DEVICE)
    assert e.current_option == "Direct route"
    e2 = ChargingModeSelect(_make_coord({"chargingMode": 0}), DEVICE)
    assert e2.current_option == "Follow perimeter"


def test_charging_mode_defaults_to_option0_when_missing_unknown_when_invalid() -> None:
    # Proto3: absent field == enum default 0 → option 0, not unknown.
    assert ChargingModeSelect(_make_coord(), DEVICE).current_option == "Follow perimeter"
    assert ChargingModeSelect(_make_coord({}), DEVICE).current_option == "Follow perimeter"
    # Unknown future enum value (present but unmapped) → unknown.
    assert ChargingModeSelect(_make_coord({"chargingMode": 99}), DEVICE).current_option is None
    # Non-int (hostile decode) → unknown.
    assert ChargingModeSelect(_make_coord({"chargingMode": "1"}), DEVICE).current_option is None


def test_charging_mode_defaults_to_option0_when_coordinator_data_none() -> None:
    coord = _make_coord({"chargingMode": 1})
    coord.data = None
    assert ChargingModeSelect(coord, DEVICE).current_option == "Follow perimeter"


async def test_charging_mode_select_option_calls_coordinator() -> None:
    coord = _make_coord({"chargingMode": 0})
    e = ChargingModeSelect(coord, DEVICE)
    await e.async_select_option("Direct route")
    coord.async_set_device_settings.assert_awaited_once_with(THING, charging_mode=1)


# ---------------------------------------------------------------------------
# ZoneOrderSelect
# ---------------------------------------------------------------------------


def test_zone_order_unique_id_and_options() -> None:
    e = ZoneOrderSelect(_make_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_zone_order"
    assert e._attr_name == "Zone order"
    assert e._attr_options == ["Optimize", "Custom"]


def test_zone_order_reads_current_value() -> None:
    assert ZoneOrderSelect(_make_coord({"zoneOrder": 0}), DEVICE).current_option == "Optimize"
    assert ZoneOrderSelect(_make_coord({"zoneOrder": 1}), DEVICE).current_option == "Custom"


async def test_zone_order_select_option_calls_coordinator() -> None:
    coord = _make_coord({"zoneOrder": 0})
    await ZoneOrderSelect(coord, DEVICE).async_select_option("Custom")
    coord.async_set_device_settings.assert_awaited_once_with(THING, zone_order=1)


# ---------------------------------------------------------------------------
# Label/wire-enum drift guards — labels must map 1:1 to the const tables that
# const.py + the protocol encoder use.
# ---------------------------------------------------------------------------


def test_charging_mode_option_values_match_const_table() -> None:
    assert set(_CHARGING_MODE_OPTIONS.values()) == set(CHARGING_MODES)


def test_zone_order_option_values_match_const_table() -> None:
    assert set(_ZONE_ORDER_OPTIONS.values()) == set(ZONE_ORDERS)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_adds_all_selects_per_device() -> None:

    coord = _make_coord({"chargingMode": 0, "zoneOrder": 0})
    coord.async_set_robot_config = AsyncMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    types = {type(e).__name__ for e in added}
    assert types == {"ChargingModeSelect", "ZoneOrderSelect", "CameraLightSelect"}


async def test_async_setup_entry_skips_when_no_devices() -> None:
    coord = _make_coord()
    coord.devices = []
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    called = False

    def _add(entities):
        nonlocal called
        called = True

    await async_setup_entry(hass, entry, _add)
    assert called is False


# ---------------------------------------------------------------------------
# CameraLightSelect — write-optimistic, no decoded read-back available
# ---------------------------------------------------------------------------


def _make_camera_coord() -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: {}}
    coord.devices = [DEVICE]
    coord.async_set_robot_config = AsyncMock()
    return coord


def test_camera_light_select_metadata_and_unknown_initially() -> None:
    from lymow.select import CameraLightSelect

    e = CameraLightSelect(_make_camera_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_camera_light"
    assert e._attr_name == "Camera light"
    assert e._attr_options == ["Off", "Low", "Medium", "High"]
    assert e._attr_entity_registry_enabled_default is False
    # Write-optimistic: no decoded brightness in pboutput → unknown until first press.
    assert e.current_option is None


async def test_camera_light_select_each_option_publishes_matching_signal() -> None:
    from lymow.const import (
        SIGNAL_TURN_OFF_CAMERA_LIGHT,
        SIGNAL_TURN_ON_CAMERA_LIGHT,
        SIGNAL_TURN_ON_CAMERA_LIGHT_LOW,
        SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE,
    )
    from lymow.select import CameraLightSelect

    cases = [
        ("Off", SIGNAL_TURN_OFF_CAMERA_LIGHT),
        ("Low", SIGNAL_TURN_ON_CAMERA_LIGHT_LOW),
        ("Medium", SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE),
        ("High", SIGNAL_TURN_ON_CAMERA_LIGHT),
    ]
    for label, signal in cases:
        coord = _make_camera_coord()
        e = CameraLightSelect(coord, DEVICE)
        e.async_write_ha_state = MagicMock()
        await e.async_select_option(label)
        coord.async_set_robot_config.assert_awaited_once_with(THING, signal=signal)
        assert e.current_option == label  # last choice sticks
