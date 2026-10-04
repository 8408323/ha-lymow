"""Tests for lawn_mower.py — LymowMower and async_setup_entry."""

from __future__ import annotations

import sys
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
    coord.async_delete_channel = AsyncMock()
    coord.async_delete_nogo_zone = AsyncMock()
    coord.async_start_edit_boundary = AsyncMock()
    coord.async_complete_edit_boundary = AsyncMock()
    coord.async_start_zones = AsyncMock()
    coord.async_query_map = AsyncMock()
    coord.async_query_schedules = AsyncMock()
    coord.async_ble_drive = AsyncMock()
    coord.async_set_task_config = AsyncMock()
    coord.async_set_run_time_config = AsyncMock()
    coord.async_set_zone_config = AsyncMock()
    coord.async_set_geofence = AsyncMock()
    coord.async_update_channel_settings = AsyncMock()
    coord.async_get_clean_history = AsyncMock(return_value=[])
    coord.async_set_robot_config = AsyncMock()
    coord.async_set_recharge_resume = AsyncMock()
    coord.async_set_headlight_schedule = AsyncMock()
    coord.async_set_pin = AsyncMock()
    coord.async_set_wifi = AsyncMock()
    coord.async_bind_rtk = AsyncMock()
    coord.async_set_device_settings = AsyncMock()
    coord.async_rename_zone = AsyncMock()
    coord.async_rename_nogo_zone = AsyncMock()
    coord.async_rename_channel = AsyncMock()
    coord.async_clear_schedules = AsyncMock()
    coord.async_set_schedules = AsyncMock()
    coord.async_add_schedule = AsyncMock()
    coord.async_delete_schedule = AsyncMock()
    coord.async_toggle_schedule = AsyncMock()
    coord.async_backup_map = AsyncMock()
    coord.async_restore_backup_map = AsyncMock()
    coord.async_delete_backup_map = AsyncMock()
    coord.async_rename_backup_map = AsyncMock()
    coord.async_rename_device = AsyncMock()
    coord.async_move_charging_station = AsyncMock()
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


def test_mower_is_primary_device_entity() -> None:
    m = _make_mower()
    # Primary entity: has_entity_name + name=None renders as just the device name.
    assert m._attr_has_entity_name is True
    assert m._attr_name is None
    assert m._attr_device_info["name"] == "Mower 1"


def test_mower_device_name_fallback_sn() -> None:
    coord = _make_coord()
    m = LymowMower(coord, {"deviceThingName": THING, "sn": "SN001"})
    assert m._attr_device_info["name"] == "SN001"


def test_mower_device_name_fallback_thing() -> None:
    coord = _make_coord()
    m = LymowMower(coord, {"deviceThingName": THING})
    assert m._attr_device_info["name"] == THING


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


def test_activity_docked_when_charging_despite_docking_status() -> None:
    """The robot keeps workStatus=DOCKING(4) while charging; charging means home → DOCKED (#271)."""
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_DOCKING, "isCharging": True})
    assert m.activity == LawnMowerActivity.DOCKED


def test_activity_error_not_masked_by_charging() -> None:
    """A real workStatus error must still show ERROR even while charging (#271 review)."""
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_ERROR, "isCharging": True})
    assert m.activity == LawnMowerActivity.ERROR


def test_activity_docked() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_CHARGING})
    assert m.activity == LawnMowerActivity.DOCKED


def test_activity_paused() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_PAUSE})
    assert m.activity == LawnMowerActivity.PAUSED


def test_activity_error_code() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_ERROR})
    assert m.activity == LawnMowerActivity.ERROR


def test_activity_robot_state_paused_overrides_work_status_mowing() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_MOWING, "robotState": WORK_STATUS_PAUSE})
    assert m.activity == LawnMowerActivity.PAUSED


def test_activity_robot_state_error_overrides_work_status_mowing() -> None:
    m = _make_mower({"isOnline": True, "workStatus": WORK_STATUS_MOWING, "robotState": WORK_STATUS_ERROR})
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


def test_extra_attrs_headlight_window_enabled_and_formatted() -> None:
    m = _make_mower(
        {"robotConfig": {"headlightStart": {"hour": 21, "minute": 0}, "headlightEnd": {"hour": 6, "minute": 30}}}
    )
    attrs = m.extra_state_attributes
    assert attrs["headlight_enabled"] is True
    assert attrs["headlight_start"] == "21:00"
    assert attrs["headlight_end"] == "06:30"


def test_extra_attrs_headlight_disabled_when_window_all_zero() -> None:
    m = _make_mower(
        {"robotConfig": {"headlightStart": {"hour": 0, "minute": 0}, "headlightEnd": {"hour": 0, "minute": 0}}}
    )
    attrs = m.extra_state_attributes
    assert attrs["headlight_enabled"] is False
    assert "headlight_start" not in attrs


def test_extra_attrs_rr_enabled_from_robot_config() -> None:
    m = _make_mower({"robotConfig": {"rrConfig": {"enable": True}}})
    assert m.extra_state_attributes["rr_enabled"] is True


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
    # + 1 set-device-name + 4 backup-map (create/restore/delete/rename) + 1 ble_drive
    # + 1 set-task-config + 1 rename-zone + 1 rename-nogo-zone + 1 rename-channel
    # + 1 clear-schedules + 1 set-schedules + 1 delete-channel + 1 delete-nogo-zone
    # + 1 update-nogo-polygon + 1 set-zone-enabled + 1 add-nogo-zone + 1 add-channel
    # + 1 move-charging-station + 1 resume + 1 set-run-time-config + 1 set-network-priority
    # + 1 set-recharge-resume + 1 set-device-settings + 1 set-headlight-schedule
    # + 3 granular schedule (add/delete/toggle) + 1 set-pin + 1 set-wifi + 1 bind-rtk
    # + 1 update-zone-cut-height + 1 set-zone-config + 1 set-geofence
    # + 1 update-channel-settings + 1 get-clean-history + main's merged services
    # (pause, query_cleaning_summary, …). Count is asserted against the live total.
    assert hass.services.async_register.call_count == 59


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


async def _edit_boundary_handlers(coord) -> dict:
    """Set up services with a named mower entity; return the handlers dict."""
    from lymow.const import DOMAIN

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers: dict = {}
    hass.services.async_register.side_effect = lambda domain, service, handler, schema=None, supports_response=False: (
        handlers.__setitem__(service, handler)
    )

    def _add(entities):
        for e in entities:
            e.entity_id = "lawn_mower.mower_1"

    await async_setup_entry(hass, entry, _add)
    return handlers


async def test_handle_start_edit_boundary_valid_zone_calls_coordinator() -> None:
    coord = _make_coord({"mapData": {"goZones": [{"hashId": "z1"}]}})
    handlers = await _edit_boundary_handlers(coord)
    await handlers["start_edit_boundary"](_make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z1"}))
    coord.async_start_edit_boundary.assert_awaited_once_with(THING, "z1")


async def test_handle_start_edit_boundary_no_map_skips_validation() -> None:
    coord = _make_coord({})  # no mapData → go_ids empty → no validation
    handlers = await _edit_boundary_handlers(coord)
    await handlers["start_edit_boundary"](_make_call(["lawn_mower.mower_1"], {"zone_hash_id": "zX"}))
    coord.async_start_edit_boundary.assert_awaited_once_with(THING, "zX")


async def test_handle_start_edit_boundary_unknown_zone_raises() -> None:
    coord = _make_coord({"mapData": {"goZones": [{"hashId": "z1"}]}})
    handlers = await _edit_boundary_handlers(coord)
    with pytest.raises(ServiceValidationError):
        await handlers["start_edit_boundary"](_make_call(["lawn_mower.mower_1"], {"zone_hash_id": "nope"}))


async def test_handle_start_edit_boundary_unknown_entity_skips() -> None:
    coord = _make_coord({"mapData": {"goZones": [{"hashId": "z1"}]}})
    handlers = await _edit_boundary_handlers(coord)
    await handlers["start_edit_boundary"](_make_call(["lawn_mower.other"], {"zone_hash_id": "z1"}))
    coord.async_start_edit_boundary.assert_not_called()


async def test_handle_complete_edit_boundary_calls_coordinator() -> None:
    coord = _make_coord({})
    handlers = await _edit_boundary_handlers(coord)
    await handlers["complete_edit_boundary"](_make_call(["lawn_mower.mower_1"]))
    coord.async_complete_edit_boundary.assert_awaited_once_with(THING)


async def test_handle_complete_edit_boundary_unknown_entity_skips() -> None:
    coord = _make_coord({})
    handlers = await _edit_boundary_handlers(coord)
    await handlers["complete_edit_boundary"](_make_call(["lawn_mower.other"]))
    coord.async_complete_edit_boundary.assert_not_called()


async def test_handle_delete_channel_valid_calls_coordinator() -> None:
    coord = _make_coord({"mapData": {"channels": [{"hashId": "ch1"}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"channel_hash_id": "ch1"})
    await handlers["delete_channel"](call)
    coord.async_delete_channel.assert_awaited_once_with(THING, "ch1")


async def test_handle_delete_channel_unknown_raises_validation_error() -> None:
    coord = _make_coord({"mapData": {"channels": [{"hashId": "ch1"}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"channel_hash_id": "nope"})
    with pytest.raises(ServiceValidationError):
        await handlers["delete_channel"](call)
    coord.async_delete_channel.assert_not_called()


async def test_handle_delete_channel_ignores_channels_without_hashid() -> None:
    # A channel dict missing hashId must not poison the validation set (no TypeError on sorted).
    coord = _make_coord({"mapData": {"channels": [{"hashId": "ch1"}, {"isValid": True}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"channel_hash_id": "nope"})
    with pytest.raises(ServiceValidationError):
        await handlers["delete_channel"](call)


async def test_handle_delete_channel_unknown_entity_skips() -> None:
    coord = _make_coord({"mapData": {"channels": [{"hashId": "ch1"}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.other"], {"channel_hash_id": "ch1"})
    await handlers["delete_channel"](call)
    coord.async_delete_channel.assert_not_called()


async def test_handle_delete_channel_empty_channels_skips_validation() -> None:
    coord = _make_coord({"mapData": {"channels": []}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"channel_hash_id": "ch-any"})
    await handlers["delete_channel"](call)
    coord.async_delete_channel.assert_awaited_once_with(THING, "ch-any")


async def test_handle_delete_nogo_zone_valid_calls_coordinator() -> None:
    coord = _make_coord({"mapData": {"nogoZones": [{"hashId": "ng1"}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"nogo_hash_id": "ng1"})
    await handlers["delete_nogo_zone"](call)
    coord.async_delete_nogo_zone.assert_awaited_once_with(THING, "ng1")


async def test_handle_delete_nogo_zone_unknown_raises() -> None:
    coord = _make_coord({"mapData": {"nogoZones": [{"hashId": "ng1"}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"nogo_hash_id": "nope"})
    with pytest.raises(ServiceValidationError):
        await handlers["delete_nogo_zone"](call)
    coord.async_delete_nogo_zone.assert_not_called()


async def test_handle_delete_nogo_zone_unknown_entity_skips() -> None:
    coord = _make_coord({"mapData": {"nogoZones": [{"hashId": "ng1"}]}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.other"], {"nogo_hash_id": "ng1"})
    await handlers["delete_nogo_zone"](call)
    coord.async_delete_nogo_zone.assert_not_called()


async def test_handle_delete_nogo_zone_empty_skips_validation() -> None:
    coord = _make_coord({"mapData": {"nogoZones": []}})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"nogo_hash_id": "ng-any"})
    await handlers["delete_nogo_zone"](call)
    coord.async_delete_nogo_zone.assert_awaited_once_with(THING, "ng-any")


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


async def test_handle_pause_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"])
    await handlers["pause"](call)
    coord.async_pause.assert_not_called()


async def test_handle_pause_calls_coordinator() -> None:
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
    await handlers2["pause"](call)
    coord.async_pause.assert_called_once_with(THING)


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


async def test_handle_resume_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_resume = AsyncMock()
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
    await handlers2["resume"](_make_call(["lawn_mower.mower_1"]))
    coord.async_resume.assert_awaited_once_with(THING)


async def test_handle_resume_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_resume = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}
    handlers2: dict = {}

    def _register2(domain, service, handler, schema=None, supports_response=False):
        handlers2[service] = handler

    hass.services.async_register.side_effect = _register2
    await async_setup_entry(hass, entry, lambda entities: None)
    await handlers2["resume"](_make_call(["lawn_mower.does_not_exist"]))
    coord.async_resume.assert_not_awaited()


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


def test_every_registered_query_service_is_documented_in_services_yaml() -> None:
    """Drift guard: every entry in the production ``lawn_mower._QUERY_SERVICES``
    must appear in services.yaml so the HA UI's service picker renders a label
    and description rather than a bare service ID with no metadata. Reads from
    the production constant (not the test-local list) so adding a query
    service without UI metadata fails CI even if the test list isn't touched."""
    import re
    from pathlib import Path

    from lymow.lawn_mower import _QUERY_SERVICES

    yaml_path = Path(__file__).parent.parent / "custom_components" / "lymow" / "services.yaml"
    # Top-level keys in the YAML are column-0 identifiers ending with a colon;
    # no full YAML parser needed (HA's services.yaml is flat at the top level).
    documented = set(re.findall(r"^([a-z_][a-z0-9_]*):", yaml_path.read_text(), re.MULTILINE))
    missing = {svc for svc, _ in _QUERY_SERVICES} - documented
    assert not missing, f"services.yaml missing documentation for: {sorted(missing)}"


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


async def test_handle_update_zone_cut_height_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_zone_cut_height = AsyncMock()
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
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z1", "cut_height_mm": 55})
    await handlers["update_zone_cut_height"](call)
    coord.async_update_zone_cut_height.assert_awaited_once_with(THING, "z1", 55)


async def test_handle_update_zone_cut_height_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_zone_cut_height = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_id": "z1", "cut_height_mm": 40})
    await handlers["update_zone_cut_height"](call)
    coord.async_update_zone_cut_height.assert_not_awaited()


async def test_handle_set_zone_config_passes_named_fields_to_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
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
            "zone_hash_id": "wsmjco1T",
            "is_enabled": False,
            "cut_height": 40,
            "move_speed": 0.8,
            "path_spacing": 25,
        },
    )
    await handlers["set_zone_config"](call)
    coord.async_set_zone_config.assert_awaited_once_with(
        THING,
        [{"hashId": "wsmjco1T", "isEnabled": False, "cutHeight": 40, "moveSpeed": 0.8, "pathSpacing": 25}],
    )


async def test_handle_set_zone_config_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_id": "z1", "cut_height": 40})
    await handlers["set_zone_config"](call)
    coord.async_set_zone_config.assert_not_awaited()


async def test_handle_set_geofence_passes_named_fields_to_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
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
        {"latitude": 59.68, "longitude": 16.76, "radius_m": 200, "name": "Home"},
    )
    await handlers["set_geofence"](call)
    coord.async_set_geofence.assert_awaited_once_with(THING, latitude=59.68, longitude=16.76, radius_m=200, name="Home")


async def test_handle_set_geofence_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"radius_m": 100})
    await handlers["set_geofence"](call)
    coord.async_set_geofence.assert_not_awaited()


async def test_handle_set_geofence_plumbs_index_to_coordinator() -> None:
    """`index` selects which geofence region the coordinator mutates."""
    coord = _make_coord()
    coord.devices = [DEVICE]
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
    call = _make_call(["lawn_mower.mower_1"], {"radius_m": 220, "index": 1})
    await handlers["set_geofence"](call)
    coord.async_set_geofence.assert_awaited_once_with(THING, radius_m=220, index=1)


async def test_handle_update_channel_settings_passes_named_fields() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
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
        {"channel_hash_id": "ch000001", "cut_height_mm": 60, "channel_lift": 1},
    )
    await handlers["update_channel_settings"](call)
    coord.async_update_channel_settings.assert_awaited_once_with(THING, "ch000001", cut_height_mm=60, channel_lift=1)


async def test_handle_update_channel_settings_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"channel_hash_id": "ch000001", "cut_height_mm": 50})
    await handlers["update_channel_settings"](call)
    coord.async_update_channel_settings.assert_not_awaited()


async def test_handle_get_clean_history_returns_response_object() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_get_clean_history = AsyncMock(return_value=[{"clean_area": 100.0}])
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
    call = _make_call(["lawn_mower.mower_1"], {"page": 0, "page_size": 5})
    response = await handlers["get_clean_history"](call)
    assert response == {"history": {"lawn_mower.mower_1": [{"clean_area": 100.0}]}}
    coord.async_get_clean_history.assert_awaited_once_with(THING, page=0, page_size=5)


async def test_handle_get_clean_history_unknown_entity_returns_empty_history() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"page": 0, "page_size": 5})
    response = await handlers["get_clean_history"](call)
    assert response == {"history": {}}
    coord.async_get_clean_history.assert_not_awaited()


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


async def test_handle_update_nogo_polygon_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_nogo_polygon = AsyncMock()
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
    call = _make_call(["lawn_mower.mower_1"], {"nogo_hash_id": "ng1", "polygon": _TRIANGLE})
    await handlers["update_nogo_polygon"](call)
    coord.async_update_nogo_polygon.assert_awaited_once_with(THING, "ng1", _TRIANGLE)


async def test_handle_update_nogo_polygon_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_nogo_polygon = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"nogo_hash_id": "ng1", "polygon": _TRIANGLE})
    await handlers["update_nogo_polygon"](call)
    coord.async_update_nogo_polygon.assert_not_awaited()


async def test_handle_add_nogo_zone_returns_new_hash_ids() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_add_nogo_zone = AsyncMock(return_value="nogonew1")
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
        {"polygon": _TRIANGLE, "parent_zone_hash_id": "z1"},
    )
    result = await handlers["add_nogo_zone"](call)
    assert result == {"hash_ids": {"lawn_mower.mower_1": "nogonew1"}}
    coord.async_add_nogo_zone.assert_awaited_once_with(THING, _TRIANGLE, parent_zone_hash_id="z1")


async def test_handle_add_nogo_zone_unknown_entity_returns_empty_mapping() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_add_nogo_zone = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"polygon": _TRIANGLE, "parent_zone_hash_id": ""})
    result = await handlers["add_nogo_zone"](call)
    assert result == {"hash_ids": {}}
    coord.async_add_nogo_zone.assert_not_awaited()


async def test_handle_add_channel_returns_new_hash_ids() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_add_channel = AsyncMock(return_value="chnew1")
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
    poly = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]
    call = _make_call(
        ["lawn_mower.mower_1"],
        {"polygon": poly, "zone1_hash_id": "z1", "zone2_hash_id": "z2", "cut_height_mm": 50},
    )
    result = await handlers["add_channel"](call)
    assert result == {"hash_ids": {"lawn_mower.mower_1": "chnew1"}}
    coord.async_add_channel.assert_awaited_once_with(
        THING, poly, zone1_hash_id="z1", zone2_hash_id="z2", cut_height_mm=50
    )


async def test_handle_add_channel_unknown_entity_returns_empty_mapping() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_add_channel = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    poly = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}]
    call = _make_call(
        ["lawn_mower.unknown"],
        {"polygon": poly, "zone1_hash_id": "", "zone2_hash_id": "", "cut_height_mm": 40},
    )
    result = await handlers["add_channel"](call)
    assert result == {"hash_ids": {}}
    coord.async_add_channel.assert_not_awaited()


async def test_handle_set_zone_enabled_calls_coordinator() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_zone_enabled = AsyncMock()
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
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "z1", "is_enabled": False})
    await handlers["set_zone_enabled"](call)
    coord.async_update_zone_enabled.assert_awaited_once_with(THING, "z1", False)


async def test_handle_set_zone_enabled_unknown_entity_skips() -> None:
    coord = _make_coord()
    coord.devices = [DEVICE]
    coord.async_update_zone_enabled = AsyncMock()
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_and_get_handlers(hass, entry, coord)

    call = _make_call(["lawn_mower.unknown"], {"zone_hash_id": "z1", "is_enabled": True})
    await handlers["set_zone_enabled"](call)
    coord.async_update_zone_enabled.assert_not_awaited()


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


async def test_handle_ble_drive_auto_discovers_address(monkeypatch) -> None:
    import types as _types

    lm = sys.modules["lymow.lawn_mower"]

    coord = _make_coord({"deviceBluetooth": "Lymow_7B6521"})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}  # no configured address → must auto-discover
    handlers = await _setup_with_entity(coord, entry)
    monkeypatch.setattr(
        lm,
        "async_discovered_service_info",
        lambda hass, connectable=True: [_types.SimpleNamespace(name="Lymow_7B6521", address="AA:BB:CC:DD:EE:01")],
    )

    call = _make_call(["lawn_mower.mower_1"], {"linear": 0.2, "angular": 0.0, "duration": 1.0})
    await handlers["ble_drive"](call)
    coord.async_ble_drive.assert_awaited_once_with("AA:BB:CC:DD:EE:01", 0.2, 0.0, 1.0)


async def test_handle_ble_drive_no_ble_match_raises(monkeypatch) -> None:
    lm = sys.modules["lymow.lawn_mower"]

    coord = _make_coord({"deviceBluetooth": "Lymow_7B6521"})
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)
    monkeypatch.setattr(lm, "async_discovered_service_info", lambda hass, connectable=True: [])

    call = _make_call(["lawn_mower.mower_1"], {"linear": 0.2, "angular": 0.0, "duration": 1.0})
    with pytest.raises(ServiceValidationError):
        await handlers["ble_drive"](call)
    coord.async_ble_drive.assert_not_called()


async def test_handle_set_task_config_maps_and_calls() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"path_spacing": 200, "perimeter_mow_laps": 2})
    await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_awaited_once_with("mower-001", pathSpacing=200, perimeterMowLaps=2)


async def test_handle_set_task_config_new_fields_forwarded_deprecated_ignored() -> None:
    """The confirmed f17/f18 params are forwarded; the deprecated line_follow_mode
    / brush_speed the old card still sends are accepted but dropped (no wire home)."""
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {"safe_margin_mode": True, "turn_off_outer_motor": False, "line_follow_mode": True, "brush_speed": 90},
    )
    await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_awaited_once_with("mower-001", safeMarginMode=True, turnOffOuterMotor=False)


async def test_handle_set_task_config_forwards_overwrite_existing() -> None:
    """overwrite_existing rides through to the coordinator alongside the real settings."""
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"perimeter_mow_laps": 2, "overwrite_existing": True})
    await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_awaited_once_with("mower-001", perimeterMowLaps=2, overwrite_existing=True)


async def test_handle_set_task_config_overwrite_alone_raises() -> None:
    """overwrite_existing with no actual setting is nothing to write — must raise."""
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"overwrite_existing": True})
    with pytest.raises(ServiceValidationError):
        await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_not_called()


async def test_handle_set_task_config_no_params_raises() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {})
    with pytest.raises(ServiceValidationError):
        await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_not_called()


async def test_handle_set_task_config_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.other"], {"cut_speed": 100})
    await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_not_called()


async def test_handle_set_task_config_supports_float_and_bool_fields() -> None:
    """move_speed is a float (m/s); raise/lower_cut_height + path_order are bools —
    the schema coerces them and passes them through with PbZoneConfig camelCase
    names. line_follow_mode is accepted for backward-compat but dropped (it has no
    confirmed wire home), so it is NOT forwarded to the encoder."""
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {
            "move_speed": 0.4,
            "raise_cut_height": True,
            "lower_cut_height": False,
            "path_order": True,
            "line_follow_mode": True,
            "clean_mode": 1,
            "obs_dec_mode": 2,
        },
    )
    await handlers["set_task_config"](call)
    coord.async_set_task_config.assert_awaited_once_with(
        "mower-001",
        moveSpeed=0.4,
        raiseCutHeight=True,
        lowerCutHeight=False,
        pathOrder=True,
        cleanMode=1,
        obsDecMode=2,
    )


def test_set_task_config_schema_rejects_non_numeric_int_field() -> None:
    import voluptuous as vol_
    from lymow.lawn_mower import _SET_TASK_CONFIG_SCHEMA

    with pytest.raises(vol_.Invalid):
        _SET_TASK_CONFIG_SCHEMA({"entity_id": ["lawn_mower.x"], "cut_speed": "fast"})


def test_set_task_config_schema_coerces_string_bool_to_python_bool() -> None:
    """YAML-style "true"/"false" must reach the encoder as Python booleans —
    the encoder writes a varint regardless, but cv.boolean is the canonical
    input shape for downstream automations."""
    from lymow.lawn_mower import _SET_TASK_CONFIG_SCHEMA

    out = _SET_TASK_CONFIG_SCHEMA({"entity_id": ["lawn_mower.x"], "raise_cut_height": "true", "path_order": "false"})
    assert out["raise_cut_height"] is True
    assert out["path_order"] is False


async def test_handle_set_run_time_config_maps_and_calls() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"cut_height": 45, "move_speed": 0.6, "cut_speed": 120})
    await handlers["set_run_time_config"](call)
    coord.async_set_run_time_config.assert_awaited_once_with("mower-001", cutHeight=45, moveSpeed=0.6, cutSpeed=120)


async def test_handle_set_run_time_config_no_params_raises() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {})
    with pytest.raises(ServiceValidationError):
        await handlers["set_run_time_config"](call)
    coord.async_set_run_time_config.assert_not_awaited()


async def test_handle_set_run_time_config_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.other"], {"cut_height": 45})
    await handlers["set_run_time_config"](call)
    coord.async_set_run_time_config.assert_not_awaited()


def test_set_run_time_config_schema_enforces_ranges() -> None:
    """Non-UI callers (automations, REST) must not bypass the documented bounds."""
    import voluptuous as vol_
    from lymow.lawn_mower import _SET_RUN_TIME_CONFIG_SCHEMA

    valid = _SET_RUN_TIME_CONFIG_SCHEMA(
        {"entity_id": ["lawn_mower.x"], "cut_height": 40, "move_speed": 0.5, "cut_speed": 100}
    )
    assert valid["cut_height"] == 40 and valid["move_speed"] == 0.5 and valid["cut_speed"] == 100

    # cut_height bounds (20..100 mm), move_speed (0.1..1.5 m/s), cut_speed (0..1000)
    for bad in (
        {"cut_height": 5},
        {"cut_height": 500},
        {"move_speed": 0.0},
        {"move_speed": 9.9},
        {"cut_speed": -1},
        {"cut_speed": 10000},
    ):
        with pytest.raises(vol_.Invalid):
            _SET_RUN_TIME_CONFIG_SCHEMA({"entity_id": ["lawn_mower.x"], **bad})


async def test_handle_set_network_priority_4g_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_network_priority"](_make_call(["lawn_mower.mower_1"], {"preferred": "4g"}))
    coord.async_set_robot_config.assert_awaited_once_with("mower-001", metric_4g=True)


async def test_handle_set_network_priority_wifi_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_network_priority"](_make_call(["lawn_mower.mower_1"], {"preferred": "wifi"}))
    coord.async_set_robot_config.assert_awaited_once_with("mower-001", metric_4g=False)


async def test_handle_set_network_priority_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_network_priority"](_make_call(["lawn_mower.other"], {"preferred": "4g"}))
    coord.async_set_robot_config.assert_not_awaited()


def test_set_network_priority_schema_rejects_bad_choice() -> None:
    import voluptuous as vol_
    from lymow.lawn_mower import _SET_NETWORK_PRIORITY_SCHEMA

    assert _SET_NETWORK_PRIORITY_SCHEMA({"entity_id": ["lawn_mower.x"], "preferred": "4g"})["preferred"] == "4g"
    assert _SET_NETWORK_PRIORITY_SCHEMA({"entity_id": ["lawn_mower.x"], "preferred": "wifi"})["preferred"] == "wifi"
    with pytest.raises(vol_.Invalid):
        _SET_NETWORK_PRIORITY_SCHEMA({"entity_id": ["lawn_mower.x"], "preferred": "ethernet"})
    with pytest.raises(vol_.Invalid):
        _SET_NETWORK_PRIORITY_SCHEMA({"entity_id": ["lawn_mower.x"]})  # preferred missing


async def test_handle_set_recharge_resume_forwards_kwargs() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {"enable": True, "period_start": (9, 0), "period_end": (18, 30), "resume_bat": 75},
    )
    await handlers["set_recharge_resume"](call)
    coord.async_set_recharge_resume.assert_awaited_once_with(
        "mower-001",
        enable=True,
        period_start=(9, 0),
        period_end=(18, 30),
        recharge_bat=None,
        resume_bat=75,
    )


async def test_handle_set_recharge_resume_no_params_raises() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    with pytest.raises(ServiceValidationError):
        await handlers["set_recharge_resume"](_make_call(["lawn_mower.mower_1"], {}))
    coord.async_set_recharge_resume.assert_not_awaited()


async def test_handle_set_recharge_resume_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_recharge_resume"](_make_call(["lawn_mower.other"], {"enable": True}))
    coord.async_set_recharge_resume.assert_not_awaited()


async def test_handle_set_headlight_schedule_forwards_kwargs() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"enable": True, "start": (3, 17), "end": (4, 23)})
    await handlers["set_headlight_schedule"](call)
    coord.async_set_headlight_schedule.assert_awaited_once_with("mower-001", enable=True, start=(3, 17), end=(4, 23))


async def test_handle_set_headlight_schedule_enable_without_times_raises() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    with pytest.raises(ServiceValidationError):
        await handlers["set_headlight_schedule"](_make_call(["lawn_mower.mower_1"], {"enable": True}))
    coord.async_set_headlight_schedule.assert_not_awaited()


async def test_handle_set_headlight_schedule_disable_needs_no_times() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_headlight_schedule"](_make_call(["lawn_mower.mower_1"], {"enable": False}))
    coord.async_set_headlight_schedule.assert_awaited_once_with("mower-001", enable=False, start=None, end=None)


async def test_handle_set_headlight_schedule_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_headlight_schedule"](
        _make_call(["lawn_mower.other"], {"enable": True, "start": (3, 17), "end": (4, 23)})
    )
    coord.async_set_headlight_schedule.assert_not_awaited()


def test_set_recharge_resume_schema_parses_time_strings_and_bounds() -> None:
    import voluptuous as vol_
    from lymow.lawn_mower import _SET_RECHARGE_RESUME_SCHEMA

    parsed = _SET_RECHARGE_RESUME_SCHEMA({"entity_id": ["lawn_mower.x"], "period_start": "08:30", "recharge_bat": 15})
    assert parsed["period_start"] == (8, 30)
    assert parsed["recharge_bat"] == 15

    # Whitespace and single-digit hour both accepted, per the docstring.
    assert _SET_RECHARGE_RESUME_SCHEMA({"entity_id": ["lawn_mower.x"], "period_start": " 9:05 "})["period_start"] == (
        9,
        5,
    )

    # Bad time formats — covers all three guards (no colon, non-int, out-of-range)
    # plus non-string input (e.g. an int) which must raise instead of silently parsing.
    for bad_time in ("8", "abc:de", "8:60", "24:00", "not-a-time", "", 900):
        with pytest.raises(vol_.Invalid):
            _SET_RECHARGE_RESUME_SCHEMA({"entity_id": ["lawn_mower.x"], "period_start": bad_time})

    # Out-of-range battery
    with pytest.raises(vol_.Invalid):
        _SET_RECHARGE_RESUME_SCHEMA({"entity_id": ["lawn_mower.x"], "resume_bat": 150})


async def test_handle_set_device_settings_maps_choices_and_inverts_handbrake() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {
            "charging_mode": "direct_route",
            "zone_order": "optimize",
            "rainy_mowing": True,
            "charging_handbrake": True,
        },
    )
    await handlers["set_device_settings"](call)
    coord.async_set_device_settings.assert_awaited_once_with(
        "mower-001",
        charging_mode=1,  # direct_route → 1
        zone_order=0,  # optimize → 0
        rainy_mowing=True,
        charging_handbrake=True,  # passed through; encode_set_device_settings does the wire-inversion
    )


async def test_handle_set_device_settings_no_params_raises() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    with pytest.raises(ServiceValidationError):
        await handlers["set_device_settings"](_make_call(["lawn_mower.mower_1"], {}))
    coord.async_set_device_settings.assert_not_awaited()


async def test_handle_set_device_settings_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_device_settings"](_make_call(["lawn_mower.other"], {"rainy_mowing": True}))
    coord.async_set_device_settings.assert_not_awaited()


def test_set_device_settings_schema_rejects_unknown_choice() -> None:
    import voluptuous as vol_
    from lymow.lawn_mower import _SET_DEVICE_SETTINGS_SCHEMA

    # Valid choices accepted, types preserved.
    parsed = _SET_DEVICE_SETTINGS_SCHEMA(
        {"entity_id": ["lawn_mower.x"], "charging_mode": "follow_perimeter", "zone_order": "custom"}
    )
    assert parsed["charging_mode"] == "follow_perimeter" and parsed["zone_order"] == "custom"

    for bad in (
        {"charging_mode": "ethernet"},
        {"charging_mode": 0},  # raw int rejected — must use the named choice
        {"zone_order": "alphabetical"},
    ):
        with pytest.raises(vol_.Invalid):
            _SET_DEVICE_SETTINGS_SCHEMA({"entity_id": ["lawn_mower.x"], **bad})


def _validated_schedule(**overrides) -> dict:
    """A schedule entry shaped as the voluptuous schema produces (defaults filled)."""
    base = {"hour": 9, "minute": 30, "day_of_week": [1, 5], "zones": ["abc"], "repeated": True, "disabled": False}
    base.update(overrides)
    return base


async def test_handle_set_schedules_maps_and_calls() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.mower_1"], {"schedules": [_validated_schedule()]})
    await handlers["set_schedules"](call)
    coord.async_set_schedules.assert_awaited_once_with(
        "mower-001",
        [
            {
                "hour": 9,
                "minute": 30,
                "dayOfWeek": [1, 5],
                "zones": ["abc"],
                "isRepeated": True,
                "isDisabled": False,
            }
        ],
    )


async def test_handle_set_schedules_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(["lawn_mower.other"], {"schedules": [_validated_schedule()]})
    await handlers["set_schedules"](call)
    coord.async_set_schedules.assert_not_called()


async def test_handle_bind_rtk_forwards_value() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["bind_rtk"](_make_call(["lawn_mower.mower_1"], {"base_id": "LK000PLACEHOLD00"}))
    coord.async_bind_rtk.assert_awaited_once_with("mower-001", "LK000PLACEHOLD00")


async def test_handle_bind_rtk_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["bind_rtk"](_make_call(["lawn_mower.other"], {"base_id": "LK000PLACEHOLD00"}))
    coord.async_bind_rtk.assert_not_called()


async def test_handle_set_wifi_forwards_values() -> None:
    """Wi-Fi is provisioned over BLE: the handler resolves the robot's BLE
    address (from options here) and forwards it, not the MQTT thing-name."""
    from lymow.const import CONF_BLE_ADDRESS

    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {CONF_BLE_ADDRESS: "AA:BB:CC:DD:EE:FF"}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_wifi"](_make_call(["lawn_mower.mower_1"], {"ssid": "TestNet", "password": "testpass12"}))
    coord.async_set_wifi.assert_awaited_once_with("AA:BB:CC:DD:EE:FF", "TestNet", "testpass12")


async def test_handle_set_wifi_without_address_raises() -> None:
    """No configured/discoverable BLE address → loud ServiceValidationError,
    never a silent no-op (creds must not be dropped quietly)."""
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    with pytest.raises(ServiceValidationError):
        await handlers["set_wifi"](_make_call(["lawn_mower.mower_1"], {"ssid": "TestNet", "password": "testpass12"}))
    coord.async_set_wifi.assert_not_called()


async def test_handle_set_wifi_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_wifi"](_make_call(["lawn_mower.other"], {"ssid": "TestNet", "password": "x"}))
    coord.async_set_wifi.assert_not_called()


async def test_handle_set_pin_forwards_value() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_pin"](_make_call(["lawn_mower.mower_1"], {"pin": "1234"}))  # placeholder
    coord.async_set_pin.assert_awaited_once_with("mower-001", "1234")


async def test_handle_set_pin_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["set_pin"](_make_call(["lawn_mower.other"], {"pin": "1234"}))
    coord.async_set_pin.assert_not_called()


async def test_handle_add_schedule_forwards_kwargs() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {"hour": 8, "minute": 5, "day_of_week": [1], "zones": ["abc"], "repeated": True, "disabled": False},
    )
    await handlers["add_schedule"](call)
    coord.async_add_schedule.assert_awaited_once_with(
        "mower-001", hour=8, minute=5, day_of_week=[1], zones=["abc"], is_repeated=True, is_disabled=False
    )


async def test_handle_add_schedule_rejects_empty_zones() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {"hour": 8, "minute": 5, "day_of_week": [1], "zones": [], "repeated": True, "disabled": False},
    )
    with pytest.raises(ServiceValidationError):
        await handlers["add_schedule"](call)
    coord.async_add_schedule.assert_not_called()


async def test_handle_set_schedules_rejects_entry_without_zones() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    call = _make_call(
        ["lawn_mower.mower_1"],
        {"schedules": [_validated_schedule(), _validated_schedule(zones=[])]},
    )
    with pytest.raises(ServiceValidationError):
        await handlers["set_schedules"](call)
    coord.async_set_schedules.assert_not_called()


def test_require_schedule_zones_passes_and_rejects() -> None:
    from lymow.lawn_mower import _require_schedule_zones

    _require_schedule_zones(["abc"])  # no raise
    _require_schedule_zones(["abc", "def"])  # no raise
    # Empty list, or any blank/whitespace zone (even mixed with a real one) -> reject.
    for bad in ([], [""], ["  "], ["abc", ""], ["abc", " "]):
        with pytest.raises(ServiceValidationError):
            _require_schedule_zones(bad)


async def test_handle_delete_schedule_forwards_id() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["delete_schedule"](_make_call(["lawn_mower.mower_1"], {"id": 42}))
    coord.async_delete_schedule.assert_awaited_once_with("mower-001", 42)


async def test_handle_toggle_schedule_forwards_disabled() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["toggle_schedule"](_make_call(["lawn_mower.mower_1"], {"id": 42, "disabled": True}))
    coord.async_toggle_schedule.assert_awaited_once_with("mower-001", 42, disabled=True)


async def test_handle_granular_schedule_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    handlers = await _setup_with_entity(coord, entry)

    await handlers["delete_schedule"](_make_call(["lawn_mower.other"], {"id": 1}))
    await handlers["toggle_schedule"](_make_call(["lawn_mower.other"], {"id": 1, "disabled": True}))
    await handlers["add_schedule"](
        _make_call(
            ["lawn_mower.other"],
            {"hour": 8, "minute": 0, "day_of_week": [], "zones": ["abc"], "repeated": True, "disabled": False},
        )
    )
    coord.async_delete_schedule.assert_not_called()
    coord.async_toggle_schedule.assert_not_called()
    coord.async_add_schedule.assert_not_called()


def test_to_day_int_accepts_names_and_ints() -> None:
    from lymow.lawn_mower import _to_day_int

    assert _to_day_int("MON") == 1
    assert _to_day_int("sunday") == 0
    assert _to_day_int(6) == 6
    assert _to_day_int("3") == 3


def test_to_day_int_rejects_out_of_range() -> None:
    import voluptuous as vol
    from lymow.lawn_mower import _to_day_int

    with pytest.raises(vol.Invalid):
        _to_day_int(7)


def test_to_day_int_rejects_invalid_string() -> None:
    import voluptuous as vol
    from lymow.lawn_mower import _to_day_int

    with pytest.raises(vol.Invalid):
        _to_day_int("notaday")


def test_set_schedules_schema_fills_defaults_and_converts_days() -> None:
    from lymow.lawn_mower import _SET_SCHEDULES_SCHEMA

    validated = _SET_SCHEDULES_SCHEMA(
        {"entity_id": ["lawn_mower.mower_1"], "schedules": [{"hour": 8, "minute": 0, "day_of_week": ["tue"]}]}
    )
    entry = validated["schedules"][0]
    assert entry["day_of_week"] == [2]
    assert entry["repeated"] is True
    assert entry["disabled"] is False
    assert entry["zones"] == []


def test_discover_ble_address_matches_and_handles_empty(monkeypatch) -> None:
    import types as _types

    lm = sys.modules["lymow.lawn_mower"]

    monkeypatch.setattr(
        lm,
        "async_discovered_service_info",
        lambda hass, connectable=True: [
            _types.SimpleNamespace(name="Other", address="X"),
            _types.SimpleNamespace(name="Lymow_X", address="AA"),
        ],
    )
    assert lm._discover_ble_address(MagicMock(), "Lymow_X") == "AA"
    assert lm._discover_ble_address(MagicMock(), "Nope") is None
    assert lm._discover_ble_address(MagicMock(), "") is None


# ---------------------------------------------------------------------------
# Backup-map management services
# ---------------------------------------------------------------------------


async def test_handle_backup_map_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {})
    await handlers["backup_map"](call)
    coord.async_backup_map.assert_awaited_once_with(THING)


async def test_handle_create_backup_map_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["backup_map"](_make_call(["lawn_mower.nope"], {}))
    coord.async_backup_map.assert_not_called()


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


async def test_handle_rename_zone_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"zone_hash_id": "wsmjco1T", "name": "Front lawn"})
    await handlers["rename_zone"](call)
    coord.async_rename_zone.assert_awaited_once_with("mower-001", "wsmjco1T", "Front lawn")


async def test_handle_rename_zone_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.other"], {"zone_hash_id": "z", "name": "X"})
    await handlers["rename_zone"](call)
    coord.async_rename_zone.assert_not_called()


async def test_handle_rename_nogo_zone_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"nogo_hash_id": "ngabcdef", "name": "Flower bed"})
    await handlers["rename_nogo_zone"](call)
    coord.async_rename_nogo_zone.assert_awaited_once_with("mower-001", "ngabcdef", "Flower bed")


async def test_handle_rename_nogo_zone_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.other"], {"nogo_hash_id": "ng", "name": "X"})
    await handlers["rename_nogo_zone"](call)
    coord.async_rename_nogo_zone.assert_not_called()


async def test_handle_rename_channel_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"channel_hash_id": "a1b2c3d4", "name": "Back passage"})
    await handlers["rename_channel"](call)
    coord.async_rename_channel.assert_awaited_once_with("mower-001", "a1b2c3d4", "Back passage")


async def test_handle_rename_channel_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.other"], {"channel_hash_id": "a1b2c3d4", "name": "X"})
    await handlers["rename_channel"](call)
    coord.async_rename_channel.assert_not_called()


async def test_handle_clear_schedules_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["clear_schedules"](_make_call(["lawn_mower.mower_1"], {}))
    coord.async_clear_schedules.assert_awaited_once_with("mower-001")


async def test_handle_clear_schedules_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    await handlers["clear_schedules"](_make_call(["lawn_mower.other"], {}))
    coord.async_clear_schedules.assert_not_called()


async def test_handle_move_charging_station_calls_coordinator() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"x": 1.5, "y": -2.3, "theta": 0.7})
    await handlers["move_charging_station"](call)
    coord.async_move_charging_station.assert_awaited_once_with("mower-001", 1.5, -2.3, 0.7)


async def test_handle_move_charging_station_no_theta() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.mower_1"], {"x": 3.0, "y": 1.0})
    await handlers["move_charging_station"](call)
    coord.async_move_charging_station.assert_awaited_once_with("mower-001", 3.0, 1.0, None)


async def test_handle_move_charging_station_unknown_entity_skips() -> None:
    coord = _make_coord()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    handlers = await _setup_with_entity(coord, entry)
    call = _make_call(["lawn_mower.other"], {"x": 1.0, "y": 2.0})
    await handlers["move_charging_station"](call)
    coord.async_move_charging_station.assert_not_called()
