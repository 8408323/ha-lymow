"""Lymow lawn mower entity."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
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
_SERVICE_START_VIDEO_SESSION = "start_video_session"
_SERVICE_UPDATE_ZONE_POLYGON = "update_zone_polygon"
_SERVICE_ADD_ZONE = "add_zone"
_ATTR_POLYGON = "polygon"
_ATTR_NAME = "name"
_ATTR_CUT_HEIGHT_MM = "cut_height_mm"

# Read-only diagnostic queries: each publishes a bare userCtrl=<code> pbinput.
# The robot's pboutput reply is handled by decode_pboutput; new field decoders
# land in a separate slice (issue #40).
_QUERY_SERVICES: tuple[tuple[str, str], ...] = (
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
)

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

# A polygon vertex is {"x": float, "y": float} in the robot's local ENU frame.
_POINT_SCHEMA = vol.Schema(
    {
        vol.Required("x"): vol.Coerce(float),
        vol.Required("y"): vol.Coerce(float),
    },
    extra=vol.ALLOW_EXTRA,
)
_UPDATE_ZONE_POLYGON_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_ID): cv.string,
        vol.Required(_ATTR_POLYGON): vol.All([_POINT_SCHEMA], vol.Length(min=3)),
    }
)
_ADD_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_POLYGON): vol.All([_POINT_SCHEMA], vol.Length(min=3)),
        vol.Optional(_ATTR_NAME, default=""): cv.string,
        vol.Optional(_ATTR_CUT_HEIGHT_MM, default=40): vol.All(vol.Coerce(int), vol.Range(min=20, max=100)),
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

    async def handle_update_zone_polygon(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_ZONE_HASH_ID]
        polygon: list[dict] = call.data[_ATTR_POLYGON]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_update_zone_polygon(entity._thing_name, hash_id, polygon)

    async def handle_add_zone(call: ServiceCall) -> dict[str, Any]:
        entity_ids: list[str] = call.data["entity_id"]
        polygon: list[dict] = call.data[_ATTR_POLYGON]
        name: str = call.data[_ATTR_NAME]
        cut_height: int = call.data[_ATTR_CUT_HEIGHT_MM]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        new_ids: dict[str, str] = {}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            new_id = await coordinator.async_add_zone(entity._thing_name, polygon, name=name, cut_height_mm=cut_height)
            new_ids[eid] = new_id
        return {"hash_ids": new_ids}

    async def handle_start_video_session(call: ServiceCall) -> dict[str, Any]:
        """Open a Kinesis Video Streams viewer session for the first matched device.

        Returns the channelARN + temporary AWS credentials needed for a
        WebRTC viewer (e.g. go2rtc / aiortc). Caller is responsible for
        consuming the response and completing the WebRTC handshake within
        the credentials' ~15-minute lifetime.
        """
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            return await coordinator.async_start_video_session(entity._thing_name)
        raise ServiceValidationError(f"No matching Lymow entity in {entity_ids!r}")

    def _make_query_handler(method_name: str):
        async def _handler(call: ServiceCall) -> None:
            entity_ids: list[str] = call.data["entity_id"]
            entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
            for eid in entity_ids:
                entity = entity_map.get(eid)
                if entity is None:
                    continue
                await getattr(coordinator, method_name)(entity._thing_name)

        return _handler

    hass.services.async_register(DOMAIN, _SERVICE_DELETE_ZONE, handle_delete_zone, schema=_DELETE_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_START_ZONE, handle_start_zone, schema=_START_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_QUERY_MAP, handle_query_map, schema=_ENTITY_ID_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_QUERY_SCHEDULES, handle_query_schedules, schema=_ENTITY_ID_SCHEMA)
    for service_name, method_name in _QUERY_SERVICES:
        hass.services.async_register(
            DOMAIN,
            service_name,
            _make_query_handler(method_name),
            schema=_ENTITY_ID_SCHEMA,
        )
    hass.services.async_register(
        DOMAIN, _SERVICE_UPDATE_ZONE_POLYGON, handle_update_zone_polygon, schema=_UPDATE_ZONE_POLYGON_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_ADD_ZONE,
        handle_add_zone,
        schema=_ADD_ZONE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_START_VIDEO_SESSION,
        handle_start_video_session,
        schema=_ENTITY_ID_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


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
