"""Tests for binary_sensor.py — charging / recharging / stolen."""

from __future__ import annotations

from unittest.mock import MagicMock

from lymow.binary_sensor import (
    ChargingBinarySensor,
    RechargingBinarySensor,
    StolenBinarySensor,
    async_setup_entry,
)
from lymow.const import DOMAIN

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: state or {}}
    return coord


def test_charging_metadata() -> None:
    coord = _make_coord({})
    e = ChargingBinarySensor(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_is_charging"
    assert "Charging" in e._attr_name


def test_charging_true_when_field_true() -> None:
    coord = _make_coord({"isCharging": True})
    e = ChargingBinarySensor(coord, DEVICE)
    assert e.is_on is True


def test_charging_false_when_field_false() -> None:
    coord = _make_coord({"isCharging": False})
    e = ChargingBinarySensor(coord, DEVICE)
    assert e.is_on is False


def test_charging_none_when_field_missing() -> None:
    coord = _make_coord({})
    e = ChargingBinarySensor(coord, DEVICE)
    assert e.is_on is None


def test_recharging_metadata_disabled_by_default() -> None:
    coord = _make_coord({})
    e = RechargingBinarySensor(coord, DEVICE)
    assert e._attr_entity_registry_enabled_default is False
    assert e._attr_unique_id == f"{THING}_is_recharging"


def test_recharging_reflects_field() -> None:
    coord = _make_coord({"isRecharging": True})
    e = RechargingBinarySensor(coord, DEVICE)
    assert e.is_on is True


def test_stolen_metadata() -> None:
    coord = _make_coord({})
    e = StolenBinarySensor(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_stolen"
    assert "Stolen" in e._attr_name


def test_stolen_true_when_flagged() -> None:
    coord = _make_coord({"stolenStatus": True})
    e = StolenBinarySensor(coord, DEVICE)
    assert e.is_on is True


def test_stolen_false_when_normal() -> None:
    coord = _make_coord({"stolenStatus": False})
    e = StolenBinarySensor(coord, DEVICE)
    assert e.is_on is False


def test_device_data_empty_when_coordinator_data_none() -> None:
    coord = _make_coord()
    coord.data = None
    e = ChargingBinarySensor(coord, DEVICE)
    assert e._device_data == {}
    assert e.is_on is None


def test_name_fallback_to_sn() -> None:
    coord = _make_coord({"isCharging": False})
    e = ChargingBinarySensor(coord, {"deviceThingName": THING, "sn": "SN9"})
    assert "SN9" in e._attr_name


async def test_async_setup_entry_creates_three_per_device() -> None:
    coord = _make_coord({"isCharging": True, "isRecharging": False, "stolenStatus": False})

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    # Exact count, not just type set — catches accidental duplicates.
    assert len(added) == 3
    types = [type(e).__name__ for e in added]
    assert sorted(types) == [
        "ChargingBinarySensor",
        "RechargingBinarySensor",
        "StolenBinarySensor",
    ]


async def test_async_setup_entry_creates_three_per_device_with_two_devices() -> None:
    """Two devices → exactly six entities (no duplicates, no skips)."""
    coord = _make_coord({"isCharging": True})
    coord.devices = [DEVICE, {"deviceThingName": "mower-002", "deviceName": "Mower 2"}]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 6
    thing_names = sorted({e._thing_name for e in added})
    assert thing_names == ["mower-001", "mower-002"]


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
