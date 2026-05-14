"""Tests for lawn_mower.py — LymowMower and async_setup_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.lawn_mower import LawnMowerActivity
from homeassistant.exceptions import ServiceValidationError
from lymow.const import (
    WORK_STATUS_CHARGING,
    WORK_STATUS_DOCKING,
    WORK_STATUS_ERROR,
    WORK_STATUS_MOWING,
    WORK_STATUS_OFFLINE,
    WORK_STATUS_PAUSE,
)
from lymow.lawn_mower import LymowMower, async_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: state or {}}
    coord.devices = [DEVICE]
    coord.async_start_mowing = AsyncMock()
    coord.async_pause = AsyncMock()
    coord.async_dock = AsyncMock()
    coord.async_delete_zone = AsyncMock()
    coord.async_start_zones = AsyncMock()
    coord.async_query_map = AsyncMock()
    coord.async_query_schedules = AsyncMock()
    return coord


def _make_mower(state: dict | None = None) -> LymowMower:
    coord = _make_coord(state)
    return LymowMower(coord, DEVICE)


# ---------------------------------------------------------------------------
# LymowMower init
# ---------------------------------------------------------------------------


def test_mower_unique_id() -> None:
    m = _make_mower()
    assert m._attr_unique_id == THING


def test_mower_name() -> None:
    m = _make_mower()
    assert m._attr_name == "Mower 1"


def test_mower_name_fallback_sn() -> None:
    coord = _make_coord()
    m = LymowMower(coord, {"deviceThingName": THING, "sn": "SN001"})
    assert m._attr_name == "SN001"


def test_mower_name_fallback_thing() -> None:
    coord = _make_coord()
    m = LymowMower(coord, {"deviceThingName": THING})
    assert m._attr_name == THING


def test_mower_supported_features() -> None:
    from homeassistant.components.lawn_mower import LawnMowerEntityFeature

    m = _make_mower()
    assert m._attr_supported_features & LawnMowerEntityFeature.START_MOWING
    assert m._attr_supported_features & LawnMowerEntityFeature.PAUSE
    assert m._attr_supported_features & LawnMowerEntityFeature.DOCK


# ---------------------------------------------------------------------------
# activity property
# ---------------------------------------------------------------------------


def test_activity_offline_flag_returns_error() -> None:
    m = _make_mower({"isOnline": False, "workStatus": WORK_STATUS_MOWING})
    assert m.activity == LawnMowerActivity.ERROR


def test_activity_mowing() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_MOWING})
    assert m.activity == LawnMowerActivity.MOWING


def test_activity_returning() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_DOCKING})
    assert m.activity == LawnMowerActivity.RETURNING


def test_activity_docked() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_CHARGING})
    assert m.activity == LawnMowerActivity.DOCKED


def test_activity_paused() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_PAUSE})
    assert m.activity == LawnMowerActivity.PAUSED


def test_activity_error_code() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_ERROR})
    assert m.activity == LawnMowerActivity.ERROR


def test_activity_offline_status_returns_error() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_OFFLINE})
    assert m.activity == LawnMowerActivity.ERROR


def test_activity_no_data_returns_error() -> None:
    coord = MagicMock()
    coord.data = {}
    m = LymowMower(coord, DEVICE)
    assert m.activity == LawnMowerActivity.ERROR


def test_activity_online_true_not_required_when_key_absent() -> None:
    # isOnline key absent — defaults to True; workStatus absent → WORK_STATUS_OFFLINE → ERROR
    m = _make_mower({})
    assert m.activity == LawnMowerActivity.ERROR


# ---------------------------------------------------------------------------
# async_start_mowing, async_pause, async_dock
# ---------------------------------------------------------------------------


async def test_async_start_mowing_calls_coordinator() -> None:
    m = _make_mower()
    await m.async_start_mowing()
    m.coordinator.async_start_mowing.assert_called_once_with(THING)


async def test_async_pause_calls_coordinator() -> None:
    m = _make_mower()
    await m.async_pause()
    m.coordinator.async_pause.assert_called_once_with(THING)


async def test_async_dock_calls_coordinator() -> None:
    m = _make_mower()
    await m.async_dock()
    m.coordinator.async_dock.assert_called_once_with(THING)


# ---------------------------------------------------------------------------
# extra_state_attributes
# ---------------------------------------------------------------------------


def test_extra_attrs_zones_empty_without_map_data() -> None:
    m = _make_mower({})
    assert m.extra_state_attributes["zones"] == []


def test_extra_attrs_zones_with_zones() -> None:
    zones = [{"hashId": "z1", "area": 25.0, "isEnabled": True}]
    m = _make_mower({"mapData": {"goZones": zones}})
    attrs = m.extra_state_attributes
    assert len(attrs["zones"]) == 1
    assert attrs["zones"][0]["hash_id"] == "z1"
    assert attrs["zones"][0]["area_m2"] == 25.0
    assert attrs["zones"][0]["enabled"] is True


def test_extra_attrs_zone_enabled_defaults_true() -> None:
    zones = [{"hashId": "z1", "area": 10.0}]  # no isEnabled
    m = _make_mower({"mapData": {"goZones": zones}})
    assert m.extra_state_attributes["zones"][0]["enabled"] is True


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_mower_entities() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord()
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    hass.services = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], LymowMower)


async def test_async_setup_entry_registers_services() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord()
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    hass.services = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    await async_setup_entry(hass, entry, lambda entities: None)

    assert hass.services.async_register.call_count == 4


# ---------------------------------------------------------------------------
# Service handlers (tested via async_setup_entry captured handlers)
# ---------------------------------------------------------------------------


async def _setup_and_get_handlers(hass: MagicMock, entry: MagicMock, coord: MagicMock) -> dict:
    """Call async_setup_entry and capture registered service handlers."""
    from lymow.const import DOMAIN

    handlers: dict = {}

    def _register(domain, service, handler, schema=None):
        handlers[service] = handler

    hass.services.async_register.side_effect = _register
    hass.data = {DOMAIN: {entry.entry_id: coord}}

    await async_setup_entry(hass, entry, lambda entities: None)
    return handlers


def _make_call(entity_ids: list[str], extra: dict | None = None) -> MagicMock:
    call = MagicMock()
    call.data = {"entity_id": entity_ids, **(extra or {})}
    return call


async def test_handle_delete_zone_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_id": "z1"})
    await handlers["delete_zone"](call)
    coord.async_delete_zone.assert_not_called()


async def test_handle_delete_zone_valid_zone_calls_coordinator() -> None:
    coord = _make_coord({"mapData": {"goZones": [{"hashId": "z1"}]}})
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    await _setup_and_get_handlers(hass, entry, coord)

    # We need an entity with a known entity_id
    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    mower_entity = next(e for e in added if isinstance(e, LymowMower))
    mower_entity.entity_id = "lawn_mower.mower_1"

    # Re-setup so entity_map is populated
    handlers2 = {}

    def _register2(domain, service, handler, schema=None):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z1"})
    await handlers2["delete_zone"](call)
    coord.async_delete_zone.assert_called_once_with(THING, "z1")


async def test_handle_delete_zone_unknown_zone_raises_validation_error() -> None:
    coord = _make_coord({"mapData": {"goZones": [{"hashId": "z1"}]}})
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}

    handlers2 = {}

    def _register2(domain, service, handler, schema=None):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z-unknown"})
    with pytest.raises(ServiceValidationError):
        await handlers2["delete_zone"](call)


async def test_handle_delete_zone_empty_go_ids_skips_validation() -> None:
    """When mapData has no goZones, skip zone validation (map not loaded)."""
    coord = _make_coord({"mapData": {"goZones": []}})
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2 = {}

    def _register2(domain, service, handler, schema=None):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z-any"})
    await handlers2["delete_zone"](call)
    coord.async_delete_zone.assert_called_once_with(THING, "z-any")


async def test_handle_start_zone_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_ids": ["z1"]})
    await handlers["start_zone"](call)
    coord.async_start_zones.assert_not_called()


async def test_handle_query_map_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"])
    await handlers["query_map"](call)
    coord.async_query_map.assert_not_called()


async def test_handle_query_schedules_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"])
    await handlers["query_schedules"](call)
    coord.async_query_schedules.assert_not_called()


async def test_handle_start_zone_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2 = {}

    def _register2(domain, service, handler, schema=None):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_ids": ["z1", "z2"]})
    await handlers2["start_zone"](call)
    coord.async_start_zones.assert_called_once_with(THING, ["z1", "z2"])


async def test_handle_query_map_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2 = {}

    def _register2(domain, service, handler, schema=None):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"])
    await handlers2["query_map"](call)
    coord.async_query_map.assert_called_once_with(THING)


async def test_handle_query_schedules_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2 = {}

    def _register2(domain, service, handler, schema=None):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"])
    await handlers2["query_schedules"](call)
    coord.async_query_schedules.assert_called_once_with(THING)
