"""Lymow lawn mower entity."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    WORK_STATUS_DOCKED_GROUP,
    WORK_STATUS_ERROR_GROUP,
    WORK_STATUS_MOWING_GROUP,
    WORK_STATUS_OFFLINE,
    WORK_STATUS_PAUSED_GROUP,
    WORK_STATUS_RETURNING_GROUP,
)
from .coordinator import LymowCoordinator

_LOGGER = logging.getLogger(__name__)

_SERVICE_DELETE_ZONE = "delete_zone"
_ATTR_ZONE_HASH_ID = "zone_hash_id"
_SERVICE_START_ZONE = "start_zone"
_ATTR_ZONE_HASH_IDS = "zone_hash_ids"
_SERVICE_QUERY_MAP = "query_map"
_SERVICE_QUERY_SCHEDULES = "query_schedules"

_ENTITY_ID_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})
_DELETE_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_ID): cv.string,
    }
)
_START_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_IDS): vol.All(cv.ensure_list, [cv.string]),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = list(LymowMower(coordinator, device) for device in coordinator.devices)
    async_add_entities(entities)

    async def handle_delete_zone(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_ZONE_HASH_ID]

        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            thing_name = entity._thing_name
            # Validate zone exists in cached map (best-effort — map may not be loaded yet)
            map_data = coordinator.data.get(thing_name, {}).get("mapData") or {}
            go_ids = {z.get("hashId") for z in map_data.get("goZones", [])}
            if go_ids and hash_id not in go_ids:
                raise ServiceValidationError(f"Zone {hash_id!r} not found in map. Known go zones: {sorted(go_ids)}")
            await coordinator.async_delete_zone(thing_name, hash_id)

    async def handle_start_zone(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        zone_hash_ids: list[str] = call.data[_ATTR_ZONE_HASH_IDS]

        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            thing_name = entity._thing_name
            await coordinator.async_start_zones(thing_name, zone_hash_ids)

    async def handle_query_map(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_query_map(entity._thing_name)

    async def handle_query_schedules(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_query_schedules(entity._thing_name)

    hass.services.async_register(DOMAIN, _SERVICE_DELETE_ZONE, handle_delete_zone, schema=_DELETE_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_START_ZONE, handle_start_zone, schema=_START_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_QUERY_MAP, handle_query_map, schema=_ENTITY_ID_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_QUERY_SCHEDULES, handle_query_schedules, schema=_ENTITY_ID_SCHEMA)


class LymowMower(CoordinatorEntity[LymowCoordinator], LawnMowerEntity):
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING | LawnMowerEntityFeature.PAUSE | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = self._thing_name
        device_label = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = device_label

    @property
    def _device_data(self) -> dict:
        return self.coordinator.data.get(self._thing_name, {})

    @property
    def activity(self) -> LawnMowerActivity:
        if not self._device_data.get("isOnline", True):
            return LawnMowerActivity.ERROR

        ws = self._device_data.get("workStatus", WORK_STATUS_OFFLINE)

        if ws in WORK_STATUS_MOWING_GROUP:
            return LawnMowerActivity.MOWING
        if ws in WORK_STATUS_RETURNING_GROUP:
            return LawnMowerActivity.RETURNING
        if ws in WORK_STATUS_DOCKED_GROUP:
            return LawnMowerActivity.DOCKED
        if ws in WORK_STATUS_PAUSED_GROUP:
            return LawnMowerActivity.PAUSED
        if ws in WORK_STATUS_ERROR_GROUP:
            return LawnMowerActivity.ERROR
        # Offline or unknown
        return LawnMowerActivity.ERROR

    async def async_start_mowing(self) -> None:
        await self.coordinator.async_start_mowing(self._thing_name)

    async def async_pause(self) -> None:
        await self.coordinator.async_pause(self._thing_name)

    async def async_dock(self) -> None:
        await self.coordinator.async_dock(self._thing_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        map_data = self._device_data.get("mapData") or {}
        return {
            "zones": [
                {
                    "hash_id": z.get("hashId", ""),
                    "area_m2": z.get("area"),
                    "enabled": z.get("isEnabled", True),
                }
                for z in map_data.get("goZones", [])
            ]
        }
