"""Lymow sensors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ERROR_DESCRIPTIONS, RTK_STATUS_FIXED, RTK_STATUS_FLOAT_FIX
from .coordinator import LymowCoordinator


@dataclass(frozen=True, kw_only=True)
class LymowSensorDescription(SensorEntityDescription):
    value_key: str


SENSORS: tuple[LymowSensorDescription, ...] = (
    # Live MQTT sensors
    LymowSensorDescription(
        key="battery",
        name="Battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_key="battery",
    ),
    LymowSensorDescription(
        key="error_code",
        name="Error code",
        value_key="errorCode",
        icon="mdi:alert-circle-outline",
    ),
    LymowSensorDescription(
        key="wifi_signal",
        name="Wi-Fi signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        value_key="wifiSignalQuality",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="lte_signal",
        name="LTE signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        value_key="lteSignalQuality",
        entity_registry_enabled_default=False,
    ),
    # REST sensors
    LymowSensorDescription(
        key="connectivity",
        name="Connectivity",
        value_key="deviceState",
        icon="mdi:wifi",
    ),
    LymowSensorDescription(
        key="firmware",
        name="Firmware version",
        value_key="softwareVersion",
        icon="mdi:tag",
    ),
    LymowSensorDescription(
        key="mcu_version",
        name="MCU version",
        value_key="mcuVersion",
        icon="mdi:chip",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="ip_address",
        name="IP address",
        value_key="ipAddress",
        icon="mdi:ip-network",
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[LymowSensor | LymowErrorSensor | LymowRtkSensor] = []
    for device in coordinator.devices:
        for description in SENSORS:
            if description.key == "error_code":
                entities.append(LymowErrorSensor(coordinator, device, description))
            else:
                entities.append(LymowSensor(coordinator, device, description))
        entities.append(LymowRtkSensor(coordinator, device))
    async_add_entities(entities)


class LymowSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    entity_description: LymowSensorDescription

    def __init__(self, coordinator: LymowCoordinator, device: dict, description: LymowSensorDescription) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self.entity_description = description
        self._attr_unique_id = f"{self._thing_name}_{description.key}"
        device_label = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} {description.name}"

    @property
    def native_value(self) -> Any:
        return self.coordinator.data.get(self._thing_name, {}).get(self.entity_description.value_key)


class LymowErrorSensor(LymowSensor):
    """Error code sensor that also exposes a human-readable description."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        code = self.native_value or 0
        return {"description": ERROR_DESCRIPTIONS.get(int(code), f"Unknown ({code})")}


class LymowRtkSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """RTK GPS fix quality sensor."""

    _RTK_LABELS = {
        0: "Not ready",
        1: "Float fix (~40 cm)",
        2: "Fixed (~2 cm)",
    }

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_rtk_status"
        device_label = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} RTK status"
        self._attr_icon = "mdi:satellite-variant"
        self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        status = self.coordinator.data.get(self._thing_name, {}).get("rtkStatus")
        if status is None:
            return None
        return self._RTK_LABELS.get(int(status), f"Unknown ({status})")
