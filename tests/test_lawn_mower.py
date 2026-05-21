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
    coord.async_ble_drive = AsyncMock()
    coord.async_restore_backup_map = AsyncMock()
    coord.async_delete_backup_map = AsyncMock()
    coord.async_rename_backup_map = AsyncMock()
    coord.async_rename_device = AsyncMock()
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

    # 5 originals + 10 query + 2 zone-edit + 1 merge + 1 pin-and-go + 1 split
    # + 1 set-device-name + 3 backup-map + 1 ble_drive.
    assert hass.services.async_register.call_count == 25


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


# ---------------------------------------------------------------------------
# merge_zones service (#41)
# ---------------------------------------------------------------------------


async def test_handle_merge_zones_returns_new_hash_ids() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_merge_zones = AsyncMock(return_value="merged01")
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
        {"zone_hash_ids": ["alpha", "beta"], "name": "Lawn", "cut_height_mm": 30},
    )
    result = await handlers["merge_zones"](call)
    assert result == {"hash_ids": {"lawn_mower.mower_1": "merged01"}}
    coord.async_merge_zones.assert_awaited_once_with(THING, ["alpha", "beta"], name="Lawn", cut_height_mm=30)


async def test_handle_merge_zones_uses_default_cut_height_when_unset() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_merge_zones = AsyncMock(return_value="merged02")
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
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_ids": ["alpha", "beta"], "name": ""})
    await handlers["merge_zones"](call)
    coord.async_merge_zones.assert_awaited_once_with(THING, ["alpha", "beta"], name="", cut_height_mm=None)


async def test_handle_merge_zones_unknown_entity_returns_empty_mapping() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_merge_zones = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_ids": ["a", "b"], "name": ""})
    result = await handlers["merge_zones"](call)
    assert result == {"hash_ids": {}}
    coord.async_merge_zones.assert_not_awaited()


# ---------------------------------------------------------------------------
# pin_and_go service (#43)
# ---------------------------------------------------------------------------


async def test_handle_pin_and_go_returns_new_hash_id() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_pin_and_go = AsyncMock(return_value="pinhash01")
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
        {"x": 5.0, "y": 3.0, "radius_m": 2.0, "cut_height_mm": 35, "name": "pin"},
    )
    result = await handlers["pin_and_go"](call)
    assert result == {"hash_ids": {"lawn_mower.mower_1": "pinhash01"}}
    coord.async_pin_and_go.assert_awaited_once_with(THING, 5.0, 3.0, radius_m=2.0, cut_height_mm=35, name="pin")


async def test_handle_pin_and_go_unknown_entity_returns_empty_mapping() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_pin_and_go = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(
        ["lawn_mower.unknown"],
        {"x": 0.0, "y": 0.0, "radius_m": 1.0, "cut_height_mm": 40, "name": ""},
    )
    result = await handlers["pin_and_go"](call)
    assert result == {"hash_ids": {}}
    coord.async_pin_and_go.assert_not_awaited()


# ---------------------------------------------------------------------------
# split_zone service (#42)
# ---------------------------------------------------------------------------


async def test_handle_split_zone_returns_two_new_hash_ids() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_split_zone = AsyncMock(return_value=("leftId01", "rightId01"))
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
        {
            "zone_hash_id": "alpha",
            "cut_p1": {"x": 0.0, "y": 0.0},
            "cut_p2": {"x": 1.0, "y": 1.0},
            "names": ["west", "east"],
        },
    )
    result = await handlers["split_zone"](call)
    assert result == {"hash_ids": {"lawn_mower.mower_1": ("leftId01", "rightId01")}}
    coord.async_split_zone.assert_awaited_once_with(
        THING, "alpha", {"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, names=("west", "east")
    )


async def test_handle_split_zone_unknown_entity_returns_empty_mapping() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_split_zone = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(
        ["lawn_mower.unknown"],
        {
            "zone_hash_id": "alpha",
            "cut_p1": {"x": 0.0, "y": 0.0},
            "cut_p2": {"x": 1.0, "y": 1.0},
            "names": ["", ""],
        },
    )
    result = await handlers["split_zone"](call)
    assert result == {"hash_ids": {}}
    coord.async_split_zone.assert_not_awaited()


# ---------------------------------------------------------------------------
# ble_drive service
# ---------------------------------------------------------------------------


async def _setup_with_entity(coord: MagicMock, entry: MagicMock) -> dict:
    """async_setup_entry, assign the mower a known entity_id, capture handlers."""
    from lymow.const import DOMAIN

    hass = MagicMock()
    hass.data = {DOMAIN: {entry.entry_id: coord}}
    handlers: dict = {}

    def _register(domain, service, handler, schema=None, supports_response=False):
        handlers[service] = handler

    hass.services.async_register.side_effect = _register

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    return handlers


async def test_handle_ble_drive_calls_coordinator() -> None:
    from lymow.const import CONF_BLE_ADDRESS

    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"linear": 0.3, "angular": -0.2, "duration": 1.5})
    await handlers["ble_drive"](call)
    coord.async_ble_drive.assert_awaited_once_with("AA:BB:CC:DD:EE:FF", 0.3, -0.2, 1.5)


async def test_handle_ble_drive_without_address_raises() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"linear": 0.3, "angular": 0.0, "duration": 1.0})
    with pytest.raises(ServiceValidationError):
        await handlers["ble_drive"](call)
    coord.async_ble_drive.assert_not_called()


async def test_handle_ble_drive_unknown_entity_skips() -> None:
    from lymow.const import CONF_BLE_ADDRESS

    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.unknown"], {"linear": 0.1, "angular": 0.0, "duration": 1.0})
    await handlers["ble_drive"](call)
    coord.async_ble_drive.assert_not_called()


async def test_handle_ble_drive_drives_once_for_duplicate_entities() -> None:
    from lymow.const import CONF_BLE_ADDRESS

    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {CONF_BLE_ADDRESS: "AA:BB"}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1", "lawn_mower.mower_1"],
        {"linear": 0.2, "angular": 0.0, "duration": 1.0},
    )
    await handlers["ble_drive"](call)
    coord.async_ble_drive.assert_awaited_once()


# ---------------------------------------------------------------------------
# Backup-map management services
# ---------------------------------------------------------------------------


async def test_handle_restore_backup_map_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"object_key": "dev/map/m1.pb"})
    await handlers["restore_backup_map"](call)
    coord.async_restore_backup_map.assert_awaited_once_with(THING, "dev/map/m1.pb")


async def test_handle_delete_backup_map_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"object_key": "dev/map/m1.pb"})
    await handlers["delete_backup_map"](call)
    coord.async_delete_backup_map.assert_awaited_once_with(THING, "dev/map/m1.pb")


async def test_handle_rename_backup_map_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"object_key": "dev/map/m1.pb", "name": "Spring"})
    await handlers["rename_backup_map"](call)
    coord.async_rename_backup_map.assert_awaited_once_with(THING, "dev/map/m1.pb", "Spring")


async def test_handle_backup_map_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.nope"], {"object_key": "k"})
    await handlers["delete_backup_map"](call)
    coord.async_delete_backup_map.assert_not_called()


async def test_handle_restore_backup_map_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["restore_backup_map"](_make_call(["lawn_mower.nope"], {"object_key": "k"}))
    coord.async_restore_backup_map.assert_not_called()


async def test_handle_rename_backup_map_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["rename_backup_map"](_make_call(["lawn_mower.nope"], {"object_key": "k", "name": "x"}))
    coord.async_rename_backup_map.assert_not_called()


async def test_handle_set_device_name_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["set_device_name"](_make_call(["lawn_mower.mower_1"], {"name": "Garden Bot"}))
    coord.async_rename_device.assert_awaited_once_with(THING, "Garden Bot")


async def test_handle_set_device_name_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["set_device_name"](_make_call(["lawn_mower.nope"], {"name": "x"}))
    coord.async_rename_device.assert_not_called()
