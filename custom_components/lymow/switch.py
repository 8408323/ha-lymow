"""Switch entities for Lymow: per-zone enable + device feature toggles."""

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

    # One-shot setup: device-feature switches (one set per device)
    feature_entities: list[SwitchEntity] = []
    for device in coordinator.devices:
        feature_entities.extend(
            [
                TheftDetectionSwitch(coordinator, device),
                TheftLockSwitch(coordinator, device),
                FindRobotSwitch(coordinator, device),
                MobileNotificationSwitch(coordinator, device),
                RtkAutoPauseSwitch(coordinator, device),
            ]
        )
    if feature_entities:
        async_add_entities(feature_entities)

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


class _DeviceFeatureSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Base class for boolean device-feature switches backed by /update-device-feature."""

    _feature_key: str = ""

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} {name}"
        self._attr_unique_id = f"{self._thing_name}_{self._feature_key}"
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        value = data.get(self._feature_key)
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: False})


class TheftDetectionSwitch(_DeviceFeatureSwitch):
    """Anti-theft motion-detection feature (notify on unexpected movement)."""

    _feature_key = "theftDetectionSwitch"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Theft detection", "mdi:shield-alert")


class TheftLockSwitch(_DeviceFeatureSwitch):
    """Anti-theft lock: prevents mowing/movement until unlocked from the app."""

    _feature_key = "theftLock"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Theft lock", "mdi:lock")


class FindRobotSwitch(_DeviceFeatureSwitch):
    """Find-my-robot beep / locate signal toggle."""

    _feature_key = "findRobotSwitch"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Find robot beep", "mdi:bell-ring")


class MobileNotificationSwitch(_DeviceFeatureSwitch):
    """Push notification toggle. Wire value is integer ``0`` (off) / ``2`` (on)
    — not a Python bool — so the on/off methods PATCH the matching int."""

    _feature_key = "mobileNotificationSwitch"
    _OFF_VALUE = 0
    _ON_VALUE = 2

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Mobile notifications", "mdi:bell-outline")

    @property
    def is_on(self) -> bool | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        value = data.get(self._feature_key)
        if value is None:
            return None
        return value == self._ON_VALUE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: self._ON_VALUE})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: self._OFF_VALUE})


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


class RtkAutoPauseSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Opt-in safety switch: when on, the coordinator auto-pauses the mower
    if RTK status drops to or below the configured threshold, and auto-resumes
    once it recovers — protects against the mower wandering on a degraded fix."""

    _attr_icon = "mdi:satellite-variant"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        device_label = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_unique_id = f"{self._thing_name}_rtk_auto_pause"
        self._attr_name = f"{device_label} RTK auto-pause"

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_rtk_guard_enabled(self._thing_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_rtk_guard_enabled(self._thing_name, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_rtk_guard_enabled(self._thing_name, False)
        self.async_write_ha_state()
