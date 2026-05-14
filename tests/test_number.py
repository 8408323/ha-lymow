"""Tests for number.py — ZoneCutHeightNumber and async_setup_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("homeassistant.components.number")

from lymow.number import ZoneCutHeightNumber, async_setup_entry  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}
HASH_ID = "aabbccdd"
HASH_ID2 = "11223344"

_ZONE = {"hashId": HASH_ID, "cutHeight": 60, "isEnabled": True, "area": 12.0}
_ZONE2 = {"hashId": HASH_ID2, "cutHeight": 40, "isEnabled": False, "area": 8.0}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: state or {}}
    coord.devices = [DEVICE]
    coord.async_update_zone_cut_height = AsyncMock()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def _make_entity(zone: dict | None = None) -> ZoneCutHeightNumber:
    state = {"mapData": {"goZones": [zone or _ZONE]}}
    coord = _make_coord(state)
    return ZoneCutHeightNumber(coord, DEVICE, HASH_ID)


# ---------------------------------------------------------------------------
# ZoneCutHeightNumber init
# ---------------------------------------------------------------------------


def test_unique_id() -> None:
    e = _make_entity()
    assert e._attr_unique_id == f"{THING}_{HASH_ID}_cut_height"


def test_name() -> None:
    e = _make_entity()
    assert HASH_ID[:4] in e._attr_name
    assert "Cut Height" in e._attr_name


def test_native_constraints() -> None:
    e = _make_entity()
    assert e._attr_native_min_value == 20
    assert e._attr_native_max_value == 100
    assert e._attr_native_step == 1


# ---------------------------------------------------------------------------
# _zone property
# ---------------------------------------------------------------------------


def test_zone_found() -> None:
    e = _make_entity()
    assert e._zone is not None
    assert e._zone["hashId"] == HASH_ID


def test_zone_not_found() -> None:
    coord = _make_coord({})  # no mapData
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)
    assert e._zone is None


def test_zone_wrong_hash() -> None:
    state = {"mapData": {"goZones": [_ZONE2]}}
    coord = _make_coord(state)
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)  # HASH_ID != HASH_ID2
    assert e._zone is None


# ---------------------------------------------------------------------------
# available
# ---------------------------------------------------------------------------


def test_available_when_zone_found() -> None:
    e = _make_entity()
    assert e.available is True


def test_not_available_when_zone_missing() -> None:
    coord = _make_coord({})
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)
    assert e.available is False


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_native_value_returns_cut_height() -> None:
    e = _make_entity({"hashId": HASH_ID, "cutHeight": 55})
    assert e.native_value == 55.0


def test_native_value_none_when_cut_height_absent() -> None:
    e = _make_entity({"hashId": HASH_ID})  # no cutHeight
    assert e.native_value is None


def test_native_value_none_when_no_zone() -> None:
    coord = _make_coord({})
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)
    assert e.native_value is None


# ---------------------------------------------------------------------------
# async_set_native_value
# ---------------------------------------------------------------------------


async def test_set_native_value_calls_coordinator() -> None:
    e = _make_entity()
    await e.async_set_native_value(75.0)
    e.coordinator.async_update_zone_cut_height.assert_called_once_with(THING, HASH_ID, 75)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_no_zones_initially() -> None:
    """With no mapData, no entities added."""
    from lymow.const import DOMAIN

    coord = _make_coord({})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert added == []


async def test_async_setup_entry_creates_entities_for_zones() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord({"mapData": {"goZones": [_ZONE, _ZONE2]}})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 2
    assert all(isinstance(e, ZoneCutHeightNumber) for e in added)


async def test_async_setup_entry_registers_listener() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord({})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    await async_setup_entry(hass, entry, lambda entities: None)
    coord.async_add_listener.assert_called_once()


async def test_async_setup_entry_listener_callback_adds_new_zones() -> None:
    """Listener callback dynamically adds new zone entities when data updates."""
    from lymow.const import DOMAIN

    coord = _make_coord({})  # start with no zones
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    captured_callback = None
    captured_add = None

    def _register_listener(cb):
        nonlocal captured_callback
        captured_callback = cb
        return lambda: None  # unsubscribe no-op

    coord.async_add_listener.side_effect = _register_listener

    added: list = []

    def _add(entities):
        nonlocal captured_add
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)
    assert added == []  # no zones yet

    # Simulate coordinator data update with zones
    coord.data = {THING: {"mapData": {"goZones": [_ZONE]}}}
    captured_callback()

    assert len(added) == 1
    assert isinstance(added[0], ZoneCutHeightNumber)
