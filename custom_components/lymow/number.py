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
from .entity import lymow_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    added: set[tuple[str, str]] = set()

    # Per-device numbers (one of each): geofence radius (geoFence feature),
    # RTK auto-pause threshold (coordinator-state knob), and mower volume
    # (PbRobotConfig.audioVolume).
    device_numbers: list[NumberEntity] = [GeofenceRadiusNumber(coordinator, device) for device in coordinator.devices]
    device_numbers.extend(RtkPauseThresholdNumber(coordinator, device) for device in coordinator.devices)
    device_numbers.extend(MowerVolumeNumber(coordinator, device) for device in coordinator.devices)
    if device_numbers:
        async_add_entities(device_numbers)

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


class GeofenceRadiusNumber(CoordinatorEntity[LymowCoordinator], NumberEntity):
    """Radius (m) of the theft-detection geofence circle.

    Backed by /update-device-feature → \"geoFence\": [{..., radius}]. Requires
    the geofence centre to already be set from the Lymow app — we only know
    how to mutate the radius, not set the initial coords.
    """

    _attr_has_entity_name = True
    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 10
    _attr_native_max_value = 500
    _attr_native_step = 5
    _attr_icon = "mdi:radius-outline"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_geofence_radius"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Geofence radius"

    @property
    def _geofence(self) -> dict[str, Any] | None:
        gf = (self.coordinator.data or {}).get(self._thing_name, {}).get("geoFence")
        if isinstance(gf, list) and gf and isinstance(gf[0], dict):
            return gf[0]
        return None

    @property
    def available(self) -> bool:
        return self._geofence is not None

    @property
    def native_value(self) -> float | None:
        gf = self._geofence
        if not gf:
            return None
        val = gf.get("radius")
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_geofence_radius(self._thing_name, int(value))


class ZoneCutHeightNumber(CoordinatorEntity[LymowCoordinator], NumberEntity):
    """Cut-height (mm) for a single go-zone. Backed by SYNC_MAP on change."""

    _attr_has_entity_name = True
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
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = f"Zone {hash_id[:4]} Cut Height"

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


class RtkPauseThresholdNumber(CoordinatorEntity[LymowCoordinator], NumberEntity):
    """Minimum acceptable RTK status while mowing.

    When the dedicated ``RTK auto-pause`` switch is on and the live ``rtkStatus``
    drops to or below this value during an active mow, the coordinator publishes
    PAUSE; once it climbs back above, RESUME. Valid range maps to the same
    rtkStatus codes ``LymowRtkSensor`` decodes (0=Not ready … 3=RTK fixed).
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 3
    _attr_native_step = 1
    _attr_icon = "mdi:satellite-uplink"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_rtk_pause_threshold"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "RTK pause threshold"

    @property
    def native_value(self) -> float:
        return float(self.coordinator.get_rtk_guard_threshold(self._thing_name))

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_rtk_guard_threshold(self._thing_name, int(value))
        self.async_write_ha_state()


class MowerVolumeNumber(CoordinatorEntity[LymowCoordinator], NumberEntity):
    """Mower beep/voice volume (the app's Device Settings volume slider).

    Backed by PbRobotConfig.audioVolume (field 6, int). Range mirrors the
    app's UI (0-100 %). State comes from the decoded robotConfig submessage of
    the next pboutput; before the first sighting native_value is None.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:volume-high"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_audio_volume"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Volume"

    @property
    def native_value(self) -> float | None:
        config = (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig") or {}
        v = config.get("audioVolume")
        # Untrusted wire data: out-of-range → unknown (don't silently clamp,
        # since that would hide a misbehaving robot/payload).
        if v is None or not 0 <= v <= 100:
            return None
        return float(v)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_robot_config(self._thing_name, audioVolume=int(value))
