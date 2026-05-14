"""Per-zone cut-height and path-spacing number entities for Lymow."""

from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LymowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    added: set[tuple[str, str]] = set()

    @callback
    def _add_new_zones() -> None:
        new_entities: list[ZoneCutHeightNumber] = []
        for device in coordinator.devices:
            thing = device["deviceThingName"]
            map_data = (coordinator.data or {}).get(thing, {}).get("mapData") or {}
            for zone in map_data.get("goZones", []):
                key = (thing, zone["hashId"])
                if key not in added:
                    added.add(key)
                    new_entities.append(ZoneCutHeightNumber(coordinator, device, zone["hashId"]))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_zones))
    _add_new_zones()


class ZoneCutHeightNumber(CoordinatorEntity[LymowCoordinator], NumberEntity):
    """Cut-height (mm) for a single go-zone. Backed by SYNC_MAP on change."""

    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.MILLIMETERS
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 20
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_icon = "mdi:ruler"

    def __init__(self, coordinator: LymowCoordinator, device: dict, hash_id: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._hash_id = hash_id
        self._attr_unique_id = f"{self._thing_name}_{hash_id}_cut_height"
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} Zone {hash_id[:4]} Cut Height"

    @property
    def _zone(self) -> dict[str, Any] | None:
        map_data = (self.coordinator.data or {}).get(self._thing_name, {}).get("mapData") or {}
        for z in map_data.get("goZones", []):
            if z.get("hashId") == self._hash_id:
                return z
        return None

    @property
    def available(self) -> bool:
        return self._zone is not None

    @property
    def native_value(self) -> float | None:
        z = self._zone
        return float(z["cutHeight"]) if z and z.get("cutHeight") is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_update_zone_cut_height(self._thing_name, self._hash_id, int(value))
