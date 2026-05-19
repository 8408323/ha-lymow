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

    # 5 originals + 10 query services (#39) + 2 zone-edit primitives (#38).
    assert hass.services.async_register.call_count == 17


# ---------------------------------------------------------------------------
# Service handlers (tested via async_setup_entry captured handlers)
# ---------------------------------------------------------------------------


async def _setup_and_get_handlers(hass: MagicMock, entry: MagicMock, coord: MagicMock) -> dict:
    """Call async_setup_entry and capture registered service handlers."""
    from lymow.const import DOMAIN

    handlers: dict = {}

    def _register(domain, service, handler, schema=None, supports_response=False):
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

    def _register2(domain, service, handler, schema=None, supports_response=False):
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

    def _register2(domain, service, handler, schema=None, supports_response=False):
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

    def _register2(domain, service, handler, schema=None, supports_response=False):
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

    def _register2(domain, service, handler, schema=None, supports_response=False):
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

    def _register2(domain, service, handler, schema=None, supports_response=False):
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

    def _register2(domain, service, handler, schema=None, supports_response=False):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"])
    await handlers2["query_schedules"](call)
    coord.async_query_schedules.assert_called_once_with(THING)


async def test_handle_start_video_session_returns_session_data() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_start_video_session = AsyncMock(return_value={"channelARN": "arn:test", "region": "eu-west-1"})
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2: dict = {}

    def _register2(domain, service, handler, schema=None, supports_response=False):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"])
    result = await handlers2["start_video_session"](call)
    assert result == {"channelARN": "arn:test", "region": "eu-west-1"}
    coord.async_start_video_session.assert_awaited_once_with(THING)


async def test_handle_start_video_session_raises_when_no_match() -> None:
    from homeassistant.exceptions import ServiceValidationError

    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_start_video_session = AsyncMock(return_value={})
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2: dict = {}

    def _register2(domain, service, handler, schema=None, supports_response=False):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.unknown"])
    with pytest.raises(ServiceValidationError):
        await handlers2["start_video_session"](call)
    coord.async_start_video_session.assert_not_awaited()


# query_* services (#39) — handler routing
# ---------------------------------------------------------------------------


_QUERY_SERVICE_METHODS = [
    ("query_cleaning_info", "async_query_cleaning_info"),
    ("query_cleaning_summary", "async_query_cleaning_summary"),
    ("query_robot_config", "async_query_robot_config"),
    ("query_path", "async_query_path"),
    ("query_channels", "async_query_channels"),
    ("query_run_time_config", "async_query_run_time_config"),
    ("query_wifi_4g", "async_query_wifi_4g"),
    ("query_net_detail", "async_query_net_detail"),
    ("query_rtk_diagnostic_l1", "async_query_rtk_diagnostic_l1"),
    ("query_rtk_diagnostic_l2", "async_query_rtk_diagnostic_l2"),
]


@pytest.mark.parametrize(("service_name", "method_name"), _QUERY_SERVICE_METHODS)
async def test_query_service_calls_matching_coordinator_method(service_name: str, method_name: str) -> None:
    from lymow.const import DOMAIN

    coord = _make_coord()
    coord.devices = [DEVICE]
    setattr(coord, method_name, AsyncMock())

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers: dict = {}

    def _register(domain, service, handler, schema=None, supports_response=False):
        handlers[service] = handler

    hass.services.async_register.side_effect = _register

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    await handlers[service_name](_make_call(["lawn_mower.mower_1"]))
    getattr(coord, method_name).assert_awaited_once_with(THING)


async def test_query_service_unknown_entity_skips() -> None:
    """Calling a query service with an unmatched entity_id is a silent no-op."""
    from lymow.const import DOMAIN

    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_query_robot_config = AsyncMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers: dict = {}

    def _register(domain, service, handler, schema=None, supports_response=False):
        handlers[service] = handler

    hass.services.async_register.side_effect = _register
    await async_setup_entry(hass, entry, lambda entities: None)
    await handlers["query_robot_config"](_make_call(["lawn_mower.unknown"]))
    coord.async_query_robot_config.assert_not_awaited()


# ---------------------------------------------------------------------------
# update_zone_polygon / add_zone services (#38)
# ---------------------------------------------------------------------------


_TRIANGLE = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.5, "y": 1.0}]


async def test_handle_update_zone_polygon_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_zone_polygon = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers: dict = {}

    def _register(domain, service, handler, schema=None, supports_response=False):
        handlers[service] = handler

    hass.services.async_register.side_effect = _register

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z1", "polygon": _TRIANGLE})
    await handlers["update_zone_polygon"](call)
    coord.async_update_zone_polygon.assert_awaited_once_with(THING, "z1", _TRIANGLE)


async def test_handle_update_zone_polygon_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_zone_polygon = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_id": "z1", "polygon": _TRIANGLE})
    await handlers["update_zone_polygon"](call)
    coord.async_update_zone_polygon.assert_not_awaited()


async def test_handle_add_zone_returns_new_hash_ids() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_add_zone = AsyncMock(return_value="newhash01")
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers: dict = {}

    def _register(domain, service, handler, schema=None, supports_response=False):
        handlers[service] = handler

    hass.services.async_register.side_effect = _register

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    call = _make_call(
        ["lawn_mower.mower_1"],
        {"polygon": _TRIANGLE, "name": "Patio", "cut_height_mm": 30},
    )
    result = await handlers["add_zone"](call)
    assert result == {"hash_ids": {"lawn_mower.mower_1": "newhash01"}}
    coord.async_add_zone.assert_awaited_once_with(THING, _TRIANGLE, name="Patio", cut_height_mm=30)


async def test_handle_add_zone_unknown_entity_returns_empty_mapping() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_add_zone = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"polygon": _TRIANGLE, "name": "", "cut_height_mm": 40})
    result = await handlers["add_zone"](call)
    assert result == {"hash_ids": {}}
    coord.async_add_zone.assert_not_awaited()
