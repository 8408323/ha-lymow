"""Binary sensors for Lymow: charging, returning-for-charge, and theft alert."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    entities: list[BinarySensorEntity] = []
    for device in coordinator.devices:
        entities.extend(
            [
                ChargingBinarySensor(coordinator, device),
                RechargingBinarySensor(coordinator, device),
                StolenBinarySensor(coordinator, device),
                DeviceLockedBinarySensor(coordinator, device),
                RainyMowingBinarySensor(coordinator, device),
                ChargingHandbrakeBinarySensor(coordinator, device),
            ]
        )
    if entities:
        async_add_entities(entities)


class _LymowBinarySensor(CoordinatorEntity[LymowCoordinator], BinarySensorEntity):
    """Shared base — pulls a single boolean field from coordinator data."""

    _field: str = ""
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = name
        self._attr_unique_id = f"{self._thing_name}_{suffix}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def _device_data(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._thing_name) or {}

    @property
    def is_on(self) -> bool | None:
        value = self._device_data.get(self._field)
        return bool(value) if value is not None else None


class ChargingBinarySensor(_LymowBinarySensor):
    """True while the robot is actively charging at the dock."""

    _field = "isCharging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Charging", "is_charging")


class RechargingBinarySensor(_LymowBinarySensor):
    """True while the robot has interrupted a mow to return for a top-up."""

    _field = "isRecharging"
    _attr_icon = "mdi:battery-arrow-down"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Returning for charge", "is_recharging")


class StolenBinarySensor(_LymowBinarySensor):
    """True when the robot has flagged itself as stolen (anti-theft trigger)."""

    _field = "stolenStatus"
    _attr_device_class = BinarySensorDeviceClass.TAMPER

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Stolen alert", "stolen")


class DeviceLockedBinarySensor(_LymowBinarySensor):
    """Account-level lock state from /device-list-query (distinct from theftLock)."""

    _field = "deviceLocked"
    _attr_device_class = BinarySensorDeviceClass.LOCK
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Device locked", "device_locked")

    @property
    def is_on(self) -> bool | None:
        """LOCK device class: ``on`` means *unlocked*. Invert the underlying flag."""
        value = self._device_data.get(self._field)
        if value is None:
            return None
        return not bool(value)


class _TaskConfigBinarySensor(CoordinatorEntity[LymowCoordinator], BinarySensorEntity):
    """Read-only base for PbTaskConfig bool fields (decoded from PbOutput f32).

    These mirror the app's Device Settings → Rainy Mowing / Charging
    Handbrake toggles. Read-only for now (the write path is a separate
    PbInput shape from the per-zone task-config encoder); decoded into
    coordinator.data[thing]["taskConfig"] by ``decode_pboutput``.
    """

    _config_key: str = ""
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, suffix: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = name
        self._attr_unique_id = f"{self._thing_name}_{suffix}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = False

    @property
    def is_on(self) -> bool | None:
        config = (self.coordinator.data or {}).get(self._thing_name, {}).get("taskConfig") or {}
        value = config.get(self._config_key)
        return bool(value) if value is not None else None


class RainyMowingBinarySensor(_TaskConfigBinarySensor):
    """Mirrors the app's Device Settings → Rainy Mowing toggle (read-only).

    Wire: PbTaskConfig.rainCleaning (field 3, bool). When true, the robot is
    allowed to mow in light rain.
    """

    _config_key = "rainCleaning"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Rainy mowing", "rainy_mowing", "mdi:weather-pouring")


class ChargingHandbrakeBinarySensor(_TaskConfigBinarySensor):
    """Mirrors the app's Device Settings → Charging Handbrake toggle (read-only).

    Wire: PbTaskConfig.disableChargingPark (field 4, bool), reported INVERTED
    here — the app shows "Charging Handbrake: on" when ``disableChargingPark``
    is *false*. Prevents the mower sliding off the dock on a slope.
    """

    _config_key = "disableChargingPark"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Charging handbrake", "charging_handbrake", "mdi:car-brake-hold")

    @property
    def is_on(self) -> bool | None:
        config = (self.coordinator.data or {}).get(self._thing_name, {}).get("taskConfig") or {}
        value = config.get(self._config_key)
        if value is None:
            return None
        return not bool(value)
