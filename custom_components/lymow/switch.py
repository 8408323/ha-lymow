"""Per-zone enable/disable switch entities for Lymow."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
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
        new_entities: list[ZoneEnabledSwitch] = []
        for device in coordinator.devices:
            thing = device["deviceThingName"]
            map_data = (coordinator.data or {}).get(thing, {}).get("mapData") or {}
            for zone in map_data.get("goZones", []):
                key = (thing, zone["hashId"])
                if key not in added:
                    added.add(key)
                    new_entities.append(ZoneEnabledSwitch(coordinator, device, zone["hashId"]))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_zones))
    _add_new_zones()


class ZoneEnabledSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Enable / disable a single go-zone. Backed by SYNC_MAP on toggle."""

    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: LymowCoordinator, device: dict, hash_id: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._hash_id = hash_id
        self._attr_unique_id = f"{self._thing_name}_{hash_id}_enabled"
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} Zone {hash_id[:4]}"

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
    def is_on(self) -> bool | None:
        z = self._zone
        return bool(z.get("isEnabled", True)) if z is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_zone_enabled(self._thing_name, self._hash_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_zone_enabled(self._thing_name, self._hash_id, False)
