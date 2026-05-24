"""Tests for binary_sensor.py — charging / recharging / stolen."""

from __future__ import annotations

from unittest.mock import MagicMock

from lymow.binary_sensor import (
    ChargingBinarySensor,
    DeviceLockedBinarySensor,
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


def test_device_name_fallback_to_sn() -> None:
    coord = _make_coord({"isCharging": False})
    e = ChargingBinarySensor(coord, {"deviceThingName": THING, "sn": "SN9"})
    assert e._attr_has_entity_name is True
    assert e._attr_name == "Charging"
    assert e._attr_device_info["name"] == "SN9"


async def test_device_locked_metadata_disabled_by_default() -> None:
    coord = _make_coord({})
    e = DeviceLockedBinarySensor(coord, DEVICE)
    assert e._attr_entity_registry_enabled_default is False
    assert e._attr_unique_id == f"{THING}_device_locked"
    assert "Device locked" in e._attr_name


def test_device_locked_inverts_for_lock_device_class() -> None:
    """LOCK device class: is_on=True means *unlocked* (HA convention)."""
    coord = _make_coord({"deviceLocked": True})
    e = DeviceLockedBinarySensor(coord, DEVICE)
    assert e.is_on is False  # locked → reported as off

    coord = _make_coord({"deviceLocked": False})
    e = DeviceLockedBinarySensor(coord, DEVICE)
    assert e.is_on is True  # unlocked → reported as on


def test_device_locked_none_when_missing() -> None:
    coord = _make_coord({})
    e = DeviceLockedBinarySensor(coord, DEVICE)
    assert e.is_on is None


async def test_async_setup_entry_creates_seven_per_device() -> None:
    coord = _make_coord({"isCharging": True, "isRecharging": False, "stolenStatus": False, "deviceLocked": False})

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    # Exact count, not just type set — catches accidental duplicates.
    assert len(added) == 7
    types = [type(e).__name__ for e in added]
    assert sorted(types) == [
        "ChargingBinarySensor",
        "DeviceLockedBinarySensor",
        "LteWorkingBinarySensor",
        "RechargingBinarySensor",
        "StolenBinarySensor",
        "TheftLockBinarySensor",
        "WifiWorkingBinarySensor",
    ]


async def test_async_setup_entry_seven_per_device_with_two_devices() -> None:
    """Two devices → exactly fourteen entities (no duplicates, no skips)."""
    coord = _make_coord({"isCharging": True})
    coord.devices = [DEVICE, {"deviceThingName": "mower-002", "deviceName": "Mower 2"}]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 14
    thing_names = sorted({e._thing_name for e in added})
    assert thing_names == ["mower-001", "mower-002"]


def test_theft_lock_inverts_for_lock_device_class() -> None:
    """LOCK device class: is_on=True means *unlocked* — wire True (lock
    engaged) renders as 'off' in the UI (the lock is engaged → not unlocked).
    Reads the PbOutput-derived ``theftLockEngaged`` key, NOT the REST
    ``theftLock`` (which is the feature toggle owned by TheftLockSwitch)."""
    from lymow.binary_sensor import TheftLockBinarySensor

    assert TheftLockBinarySensor(_make_coord({"theftLockEngaged": True}), DEVICE).is_on is False
    assert TheftLockBinarySensor(_make_coord({"theftLockEngaged": False}), DEVICE).is_on is True


def test_theft_lock_ignores_rest_feature_flag() -> None:
    """The REST ``theftLock`` key (TheftLockSwitch territory) must NOT leak
    into this sensor's reading — they're different concepts (engaged vs
    feature-enabled). Without ``theftLockEngaged``, the sensor reports None
    even if the REST feature flag is present."""
    from lymow.binary_sensor import TheftLockBinarySensor

    assert TheftLockBinarySensor(_make_coord({"theftLock": True}), DEVICE).is_on is None


def test_theft_lock_none_when_missing() -> None:
    from lymow.binary_sensor import TheftLockBinarySensor

    assert TheftLockBinarySensor(_make_coord({}), DEVICE).is_on is None


def test_theft_lock_metadata() -> None:
    from lymow.binary_sensor import TheftLockBinarySensor

    e = TheftLockBinarySensor(_make_coord({}), DEVICE)
    assert e._attr_entity_registry_enabled_default is False
    assert e._attr_unique_id == f"{THING}_theft_lock"
    assert "Anti-theft" in e._attr_name


def test_wifi_working_reflects_pbrobotinfo_field() -> None:
    from lymow.binary_sensor import WifiWorkingBinarySensor

    assert WifiWorkingBinarySensor(_make_coord({"wifiWorking": True}), DEVICE).is_on is True
    assert WifiWorkingBinarySensor(_make_coord({"wifiWorking": False}), DEVICE).is_on is False
    assert WifiWorkingBinarySensor(_make_coord({}), DEVICE).is_on is None


def test_lte_working_reflects_pbrobotinfo_field() -> None:
    from lymow.binary_sensor import LteWorkingBinarySensor

    assert LteWorkingBinarySensor(_make_coord({"lteWorking": True}), DEVICE).is_on is True
    assert LteWorkingBinarySensor(_make_coord({"lteWorking": False}), DEVICE).is_on is False
    assert LteWorkingBinarySensor(_make_coord({}), DEVICE).is_on is None


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
