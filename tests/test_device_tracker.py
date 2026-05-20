"""Tests for device_tracker.py — LymowDeviceTracker entity."""

from __future__ import annotations

from unittest.mock import MagicMock

from lymow.const import DOMAIN
from lymow.device_tracker import LymowDeviceTracker, async_setup_entry

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: state or {}}
    return coord


def test_metadata() -> None:
    coord = _make_coord({})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_location"
    assert "Location" in e._attr_name
    assert "Mower 1" in e._attr_name


def test_coords_returns_lat_lon_from_robot_location() -> None:
    coord = _make_coord({"robotLocation": [12.3456, 65.4321]})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.latitude == 12.3456
    assert e.longitude == 65.4321


def test_coords_none_when_location_missing() -> None:
    coord = _make_coord({})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.latitude is None
    assert e.longitude is None


def test_coords_none_when_location_is_zero_zero() -> None:
    """A robot that hasn't surveyed yet reports [0.0, 0.0]; don't pin the map to the Atlantic."""
    coord = _make_coord({"robotLocation": [0.0, 0.0]})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.latitude is None
    assert e.longitude is None


def test_coords_none_when_not_a_list() -> None:
    coord = _make_coord({"robotLocation": "not-a-list"})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.latitude is None


def test_coords_none_when_list_too_short() -> None:
    coord = _make_coord({"robotLocation": [12.3]})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.latitude is None


def test_coords_none_when_values_not_numeric() -> None:
    coord = _make_coord({"robotLocation": ["lat", "lon"]})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.latitude is None


def test_extra_state_attributes_collects_metadata() -> None:
    coord = _make_coord(
        {
            "robotLocation": [12.3, 65.0],
            "sn": "SN42",
            "deviceState": "online",
            "stolenStatus": False,
        }
    )
    e = LymowDeviceTracker(coord, DEVICE)
    attrs = e.extra_state_attributes
    assert attrs["serial"] == "SN42"
    assert attrs["device_state"] == "online"
    assert attrs["stolen"] is False


def test_extra_state_attributes_empty_when_no_data() -> None:
    coord = _make_coord({})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.extra_state_attributes == {}


def test_extra_state_attributes_marks_stolen() -> None:
    coord = _make_coord({"stolenStatus": True})
    e = LymowDeviceTracker(coord, DEVICE)
    assert e.extra_state_attributes["stolen"] is True


def test_device_data_empty_when_coordinator_data_none() -> None:
    coord = _make_coord()
    coord.data = None
    e = LymowDeviceTracker(coord, DEVICE)
    assert e._device_data == {}
    assert e.latitude is None


async def test_async_setup_entry_creates_one_per_device() -> None:
    coord = _make_coord({"robotLocation": [12.3, 65.0]})

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], LymowDeviceTracker)


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
