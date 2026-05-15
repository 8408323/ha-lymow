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
from homeassistant.const import PERCENTAGE, UnitOfArea
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ERROR_DESCRIPTIONS
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
    LymowSensorDescription(
        key="wifi_rssi_dbm",
        name="Wi-Fi RSSI",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        value_key="wifiRssiDbm",
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
    # Live MQTT sensors decoded from additional pboutput fields
    LymowSensorDescription(
        key="rtk_satellites",
        name="RTK satellites",
        value_key="rtkSatellites",
        icon="mdi:satellite-variant",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="total_area_m2",
        name="Total mowed area",
        value_key="totalAreaM2",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:grass",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="mow_progress",
        name="Mow progress",
        value_key="mowProgress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-clock",
    ),
    LymowSensorDescription(
        key="mow_strip_count",
        name="Mow strip count",
        value_key="mowStripCount",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:counter",
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[LymowSensor | LymowErrorSensor | LymowRtkSensor | LymowMapSensor] = []
    for device in coordinator.devices:
        for description in SENSORS:
            if description.key == "error_code":
                entities.append(LymowErrorSensor(coordinator, device, description))
            else:
                entities.append(LymowSensor(coordinator, device, description))
        entities.append(LymowRtkSensor(coordinator, device))
        entities.append(LymowMapSensor(coordinator, device))
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
    """Error code sensor that also exposes a human-readable description and warning codes."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data.get(self._thing_name, {})
        code = self.native_value or 0
        attrs: dict[str, Any] = {
            "description": ERROR_DESCRIPTIONS.get(int(code), f"Unknown ({code})"),
        }
        warning_codes = data.get("warningCodes")
        if warning_codes is not None:
            attrs["warning_codes"] = warning_codes
        all_error_codes = data.get("errorCodes")
        if all_error_codes is not None:
            attrs["error_codes"] = all_error_codes
        return attrs


class LymowRtkSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """RTK GPS fix quality sensor."""

    _RTK_LABELS = {
        0: "Not ready",
        1: "Float fix (~40 cm)",
        2: "Fixed (~2 cm)",
        3: "RTK fixed (~2 cm)",
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data.get(self._thing_name, {})
        attrs: dict[str, Any] = {}
        for key in ("rtkSatellites", "rtkEastM", "rtkNorthM", "poseEastM", "poseNorthM", "poseThetaRad"):
            val = data.get(key)
            if val is not None:
                attrs[key] = val
        return attrs


class LymowMapSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Sensor that exposes the full mowing map (zone polygons, GPS origin) as attributes.

    The state value is the number of go-zones currently loaded.  The
    extra_state_attributes contain the full JSON-serialisable map data that the
    ``lymow-map-card`` Lovelace card reads to draw the SVG map.

    This sensor is enabled by default so the card works out of the box, but the
    attribute payload can be large; users may disable it if it causes issues.
    """

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        device_label = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_unique_id = f"{self._thing_name}_map"
        self._attr_name = f"{device_label} Map"
        self._attr_icon = "mdi:map"

    @property
    def native_value(self) -> int | None:
        """Number of go-zones loaded, or None if map data is not yet available."""
        map_data = self.coordinator.data.get(self._thing_name, {}).get("mapData")
        if not map_data:
            return None
        return len(map_data.get("goZones", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full map data for the Lovelace card."""
        map_data = (self.coordinator.data.get(self._thing_name) or {}).get("mapData") or {}
        data = self.coordinator.data.get(self._thing_name) or {}
        attrs: dict[str, Any] = {}
        if "goZones" in map_data:
            attrs["go_zones"] = map_data["goZones"]
        if "nogoZones" in map_data:
            attrs["nogo_zones"] = map_data["nogoZones"]
        if "gpsOrigin" in map_data:
            attrs["gps_origin"] = map_data["gpsOrigin"]
        if "chargingStation" in map_data:
            attrs["charging_station"] = map_data["chargingStation"]
        # Include live robot position so the card updates without a separate entity
        for key in ("poseEastM", "poseNorthM", "poseThetaRad"):
            val = data.get(key)
            if val is not None:
                attrs[key] = val
        return attrs
