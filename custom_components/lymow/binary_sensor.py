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

# The robot has no dedicated "picked up" flag; being carried or tilted past a
# threshold mid-mow is reported as these body error codes (const.ERROR_NAMES).
_LIFTED_ERROR_CODES = (17, 18)  # ERROR_ROBOT_CLIFF, ERROR_ROBOT_INCLINE


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
                TheftLockBinarySensor(coordinator, device),
                RobotLiftedBinarySensor(coordinator, device),
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


class TheftLockBinarySensor(_LymowBinarySensor):
    """Live anti-theft lock state from PbOutput.f27 — whether the lock is
    currently engaged on the robot.

    Distinct from the REST ``theftLock`` feature flag (read/written by
    ``TheftLockSwitch``), which is whether the *feature is enabled*. The
    decoder writes this under ``theftLockEngaged`` to keep the two values
    from clobbering each other when MQTT updates land between REST polls.
    Also distinct from ``DeviceLockedBinarySensor`` (account-level lock)
    and ``StolenBinarySensor`` (the stolen-alert flag).
    """

    _field = "theftLockEngaged"
    _attr_device_class = BinarySensorDeviceClass.LOCK
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Anti-theft lock", "theft_lock")

    @property
    def is_on(self) -> bool | None:
        """LOCK device class: ``on`` means *unlocked*. Invert the wire flag
        so True = locked-on-wire renders as off (= "locked" in the UI)."""
        value = self._device_data.get(self._field)
        if value is None:
            return None
        return not bool(value)


class RobotLiftedBinarySensor(_LymowBinarySensor):
    """On when the robot reports being lifted or tilted (cliff / incline error).

    There is no dedicated "picked up" flag on the wire; the robot raises
    ERROR_ROBOT_CLIFF / ERROR_ROBOT_INCLINE when its wheels leave the ground or
    it tilts past a threshold — e.g. carried mid-mow — so this surfaces those
    already-decoded error codes as a single automation-friendly boolean.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Robot lifted or tilted", "robot_lifted")

    @property
    def is_on(self) -> bool | None:
        codes = self._device_data.get("errorCodes")
        if not isinstance(codes, list):
            return None  # no pboutput yet -> unknown
        return any(code in _LIFTED_ERROR_CODES for code in codes)
