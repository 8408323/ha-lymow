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
                WifiWorkingBinarySensor(coordinator, device),
                LteWorkingBinarySensor(coordinator, device),
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


class WifiWorkingBinarySensor(_LymowBinarySensor):
    """Live Wi-Fi link state from PbRobotInfo.wifiWorking (field 9, bool)."""

    _field = "wifiWorking"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Wi-Fi link", "wifi_working")


class LteWorkingBinarySensor(_LymowBinarySensor):
    """Live LTE link state from PbRobotInfo.lteWorking (f10, bool) — distinct from the radio-on switch."""

    _field = "lteWorking"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "LTE link", "lte_working")
