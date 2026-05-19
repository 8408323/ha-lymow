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
            ]
        )
    if entities:
        async_add_entities(entities)


class _LymowBinarySensor(CoordinatorEntity[LymowCoordinator], BinarySensorEntity):
    """Shared base — pulls a single boolean field from coordinator data."""

    _field: str = ""

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, suffix: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} {name}"
        self._attr_unique_id = f"{self._thing_name}_{suffix}"

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
