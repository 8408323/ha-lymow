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
from homeassistant.const import DEGREE, PERCENTAGE, UnitOfArea, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ERROR_DESCRIPTIONS
from .coordinator import LymowCoordinator
from .entity import lymow_device_info


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
    LymowSensorDescription(
        key="mac_address",
        name="MAC address",
        value_key="macAddress",
        icon="mdi:network",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="wifi_ssid",
        name="Wi-Fi SSID",
        value_key="networkInfo.wifiSsid",
        icon="mdi:wifi",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="cellular_ip",
        name="Cellular IP",
        value_key="networkInfo.cellularIp",
        icon="mdi:signal-cellular-3",
        entity_registry_enabled_default=False,
    ),
    # RTK diagnostic sensors — populated by query_rtk_diagnostic_l1 (#57)
    # and query_rtk_diagnostic_l2 (#58). Labels cross-referenced live against
    # the app's Settings → RTK Diagnostic page (basic + Advanced Diagnostics).
    # All disabled by default — RTK details are mostly relevant during setup
    # / troubleshooting, not for normal day-to-day automations.
    LymowSensorDescription(
        key="rtk_location_precision",
        name="Location precision",
        value_key="rtkL1.locationPrecisionM",
        native_unit_of_measurement="m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        icon="mdi:crosshairs-gps",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_gnss_satellites",
        name="GNSS satellites",
        value_key="rtkL1.gnssSatellites",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:satellite-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_l1_sat_count",
        name="L1 satellites",
        value_key="rtkL1.l1SatCount",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:satellite-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_l2_sat_count",
        name="L2 satellites",
        value_key="rtkL1.l2SatCount",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:satellite-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_l5_sat_count",
        name="L5 satellites",
        value_key="rtkL1.l5SatCount",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:satellite-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_l1_snr",
        name="L1 SNR",
        value_key="rtkL1.l1SnrMedian",
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:signal-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_l2_snr",
        name="L2 SNR",
        value_key="rtkL1.l2SnrMedian",
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:signal-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_l5_snr",
        name="L5 SNR",
        value_key="rtkL1.l5SnrMedian",
        native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:signal-variant",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_data_error_rate",
        name="RTK data error rate",
        value_key="rtkL1.dataErrorRatePct",
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:percent",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_differential_age",
        name="RTK differential age",
        value_key="rtkL2.differentialAgeSec",
        native_unit_of_measurement="s",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:timer-sand",
        entity_registry_enabled_default=False,
    ),
    # RTK Advanced Diagnostics (app's "Advanced Diagnostics" expander) —
    # per-band Lora bandwidth, hardware DC voltage, CW interference, and
    # antenna gain. Labels live-correlated against the app's RTK Diagnostic
    # page. All disabled by default like the basic RTK sensors above.
    LymowSensorDescription(
        key="rtk_lora_bandwidth_l1",
        name="Lora bandwidth L1",
        value_key="rtkL2.loraBandwidthL1Bps",
        native_unit_of_measurement="bps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radio-tower",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_lora_bandwidth_l2",
        name="Lora bandwidth L2",
        value_key="rtkL2.loraBandwidthL2Bps",
        native_unit_of_measurement="bps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radio-tower",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_lora_bandwidth_l5",
        name="Lora bandwidth L5",
        value_key="rtkL2.loraBandwidthL5Bps",
        native_unit_of_measurement="bps",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:radio-tower",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_dc_voltage_l1",
        name="RTK DC voltage L1",
        value_key="rtkL2.hwDcVoltageL1V",
        native_unit_of_measurement="V",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:flash",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_dc_voltage_l2",
        name="RTK DC voltage L2",
        value_key="rtkL2.hwDcVoltageL2V",
        native_unit_of_measurement="V",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:flash",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_dc_voltage_l5",
        name="RTK DC voltage L5",
        value_key="rtkL2.hwDcVoltageL5V",
        native_unit_of_measurement="V",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:flash",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_cw_interference_l1",
        name="RTK CW interference L1",
        value_key="rtkL2.cwInterferenceL1",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:waveform",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_cw_interference_l2",
        name="RTK CW interference L2",
        value_key="rtkL2.cwInterferenceL2",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:waveform",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_cw_interference_l5",
        name="RTK CW interference L5",
        value_key="rtkL2.cwInterferenceL5",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:waveform",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_antenna_gain_l1",
        name="RTK antenna gain L1",
        value_key="rtkL2.antennaGainL1",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:antenna",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_antenna_gain_l2",
        name="RTK antenna gain L2",
        value_key="rtkL2.antennaGainL2",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:antenna",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_antenna_gain_l5",
        name="RTK antenna gain L5",
        value_key="rtkL2.antennaGainL5",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:antenna",
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
        name="Map area",
        value_key="totalTaskAreaM2",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
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
    # Robot pose in local ENU frame (pboutput field 14), disabled by default —
    # mostly useful for debugging and advanced visualisations.
    LymowSensorDescription(
        key="pose_east_m",
        name="Pose East",
        value_key="poseEastM",
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:axis-arrow",
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    LymowSensorDescription(
        key="pose_north_m",
        name="Pose North",
        value_key="poseNorthM",
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:axis-arrow",
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    # poseThetaRad is exposed by PoseHeadingSensor (separate class — needs radians→degrees).
    # Clean history (REST /get-clean-history-collect, page=0, pageSize=15)
    LymowSensorDescription(
        key="last_clean_at",
        name="Last mow",
        value_key="lastCleanAt",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
    ),
    LymowSensorDescription(
        key="last_clean_area",
        name="Last mow area",
        value_key="lastCleanAreaM2",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:grass",
    ),
    LymowSensorDescription(
        key="last_clean_duration",
        name="Last mow duration",
        value_key="lastCleanDurationMin",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
    ),
    LymowSensorDescription(
        key="last_clean_percent",
        name="Last mow completion",
        value_key="lastCleanPercent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:progress-check",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="last_clean_battery_used",
        name="Last mow battery used",
        value_key="lastCleanBatteryUsed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="clean_history_count",
        name="Total mow sessions",
        value_key="cleanHistoryCount",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:counter",
    ),
    LymowSensorDescription(
        key="total_clean_time",
        name="Total mow time",
        value_key="totalCleanTimeMin",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:timer-sand",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="total_clean_history_area",
        name="Total mowed area (history)",
        value_key="totalCleanHistoryAreaM2",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:chart-areaspline",
        entity_registry_enabled_default=False,
    ),
    # Static device-list-query fields (set once at registration, exposed as
    # diagnostic sensors — disabled by default so they don't clutter the UI).
    LymowSensorDescription(
        key="serial_number",
        name="Serial number",
        value_key="serialNumber",
        icon="mdi:barcode",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="model",
        name="Model",
        value_key="deviceType",
        icon="mdi:robot-mower",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="bluetooth_name",
        name="Bluetooth name",
        value_key="deviceBluetooth",
        icon="mdi:bluetooth",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="sim_id",
        name="SIM ID",
        value_key="simId",
        icon="mdi:sim",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="firmware_minimum",
        name="Minimum firmware",
        value_key="fwMinVersion",
        icon="mdi:tag-arrow-down",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="registered_at",
        name="Registered",
        value_key="createdAt",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-plus",
        entity_registry_enabled_default=False,
    ),
    # Map backups (from /get-backup-map). The full list is exposed as
    # extra_state_attributes on the dedicated LymowBackupMapsSensor below.
    LymowSensorDescription(
        key="backup_map_latest_at",
        name="Latest map backup",
        value_key="backupMapLatestAt",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:cloud-upload",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="robot_state",
        name="Robot state (raw)",
        value_key="robotState",
        icon="mdi:robot",
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for device in coordinator.devices:
        for description in SENSORS:
            if description.key == "error_code":
                entities.append(LymowErrorSensor(coordinator, device, description))
            else:
                entities.append(LymowSensor(coordinator, device, description))
        entities.append(LymowRtkSensor(coordinator, device))
        entities.append(LymowMapSensor(coordinator, device))
        entities.append(LymowPoseHeadingSensor(coordinator, device))
        entities.append(LymowRemainingAreaSensor(coordinator, device))
        entities.append(LymowCleanHistoryDetailsSensor(coordinator, device))
        entities.append(LymowBackupMapsSensor(coordinator, device))
        entities.append(LymowSchedulesSensor(coordinator, device))
    async_add_entities(entities)


class LymowSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    entity_description: LymowSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict, description: LymowSensorDescription) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self.entity_description = description
        self._attr_unique_id = f"{self._thing_name}_{description.key}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data.get(self._thing_name, {})
        # Dotted ``value_key`` (e.g. ``networkInfo.wifiSsid``) walks nested dicts;
        # plain keys still read directly so existing descriptions keep working.
        for part in self.entity_description.value_key.split("."):
            if not isinstance(data, dict):
                return None
            data = data.get(part)
        return data


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

    _attr_has_entity_name = True

    _RTK_LABELS = {
        0: "No fix",
        1: "Float fix",
        2: "Fixed",
        3: "RTK fixed",
    }

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_rtk_status"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "RTK status"
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

    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_map"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Map"
        self._attr_icon = "mdi:map"

    @staticmethod
    def _trim_poly(points: list[dict]) -> list[dict]:
        """Round polygon coordinates to 4 decimal places (~1 cm precision in ENU metres).

        Full float64 precision uses ~18 chars per coordinate; 4 dp uses ~7 chars,
        cutting polygon size by ~60% and keeping the map sensor under HA's 16 kB
        attribute limit even for large multi-zone maps.
        """
        return [{"x": round(p["x"], 4), "y": round(p["y"], 4)} for p in points]

    @property
    def native_value(self) -> int | None:
        """Number of go-zones loaded, or None if map data is not yet available."""
        map_data = (self.coordinator.data.get(self._thing_name) or {}).get("mapData") or {}
        if not map_data:
            return None
        # Only count zones that have a hashId — empty {} entries are stale decode artifacts
        return sum(1 for z in map_data.get("goZones", []) if z.get("hashId"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Full map data for the Lovelace card."""
        map_data = (self.coordinator.data.get(self._thing_name) or {}).get("mapData") or {}
        data = self.coordinator.data.get(self._thing_name) or {}
        attrs: dict[str, Any] = {}

        if "goZones" in map_data:
            # Filter out stale empty zone entries (no hashId) that accumulate when
            # MQTT delivers repeated partial map responses without full zone data
            valid_zones = [z for z in map_data["goZones"] if z.get("hashId")]
            attrs["go_zones"] = [
                {**z, "polygon": self._trim_poly(z["polygon"])} if "polygon" in z else z
                for z in valid_zones
            ]
        if "nogoZones" in map_data:
            attrs["nogo_zones"] = [
                {**z, "polygon": self._trim_poly(z["polygon"])} if "polygon" in z else z
                for z in map_data["nogoZones"]
            ]
        if "channels" in map_data:
            attrs["channels"] = [
                {**ch, "polygon": self._trim_poly(ch["polygon"])} if "polygon" in ch else ch
                for ch in map_data["channels"]
            ]
        if "gpsOrigin" in map_data:
            attrs["gps_origin"] = map_data["gpsOrigin"]
        if "chargingStation" in map_data:
            attrs["charging_station"] = map_data["chargingStation"]
        if "globalZoneConfig" in map_data:
            attrs["mowing_settings"] = map_data["globalZoneConfig"]
        if "globalChannelConfig" in map_data:
            attrs["channel_config"] = map_data["globalChannelConfig"]

        path_data = (self.coordinator.data.get(self._thing_name) or {}).get("pathData")
        if path_data:
            # Also trim mow-path track points to 4 dp
            trimmed_zones = [
                {**gz, "trackPoints": self._trim_poly(gz.get("trackPoints", []))}
                for gz in path_data.get("goZones", [])
            ]
            attrs["mow_path"] = {"goZones": trimmed_zones}

        # Live robot + RTK position and fix quality
        for key in ("poseEastM", "poseNorthM", "poseThetaRad", "rtkEastM", "rtkNorthM", "rtkStatus", "workStatus"):
            val = data.get(key)
            if val is not None:
                attrs[key] = val

        rtk_raw = data.get("rtkStatus")
        if rtk_raw is not None:
            _RTK_LABELS = {0: "No fix", 1: "Float fix", 2: "Fixed", 3: "RTK fixed"}
            attrs["rtkLabel"] = _RTK_LABELS.get(int(rtk_raw), f"Unknown ({rtk_raw})")
        # Live mow progress so the card status bar shows % without needing a separate entity
        for key in ("mowProgress", "mowStripCount", "totalTaskAreaM2"):
            val = data.get(key)
            if val is not None:
                attrs[key] = val
        return attrs


class LymowSchedulesSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Mowing schedules reported by the robot (USER_CTRL_QUERY_SCHEDULES).

    State is the number of schedules. Each schedule's days, UTC time, target
    zones, repeat/disabled flags and id are exposed in the ``schedules``
    attribute. None until the first reply arrives.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_schedules"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Mow schedules"

    @property
    def native_value(self) -> int | None:
        schedules = (self.coordinator.data.get(self._thing_name) or {}).get("schedules")
        return None if schedules is None else len(schedules)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        schedules = (self.coordinator.data.get(self._thing_name) or {}).get("schedules") or []
        return {"schedules": schedules}


class LymowPoseHeadingSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Robot heading converted to degrees from the radians on the wire.

    Wraps the result into 0..360 so a compass-style display reads correctly.
    Disabled by default — pose data is diagnostic, not user-facing.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = DEGREE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:compass"
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_pose_heading"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Pose heading"

    @property
    def native_value(self) -> float | None:
        import math

        data = self.coordinator.data.get(self._thing_name) or {}
        rad = data.get("poseThetaRad")
        if rad is None:
            return None
        try:
            # Don't round here — _attr_suggested_display_precision tells HA
            # how many decimals to render; rounding at the source would
            # double-truncate and disagree with long-term statistics.
            return math.degrees(float(rad)) % 360.0
        except (TypeError, ValueError):
            return None


class LymowRemainingAreaSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Area still to mow in the current task, mirroring the app's remaining-area
    figure. Derived from the live ``totalTaskAreaM2`` and ``mowProgress`` (0–100)
    fields — the robot doesn't report remaining area directly in pboutput."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfArea.SQUARE_METERS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:grass"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_remaining_area"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Remaining area"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get(self._thing_name) or {}
        task = data.get("totalTaskAreaM2")
        progress = data.get("mowProgress")
        if task is None or progress is None:
            return None
        try:
            task_f = float(task)
            remaining = task_f * (1.0 - float(progress) / 100.0)
        except (TypeError, ValueError):
            return None
        # Bound to [0, task]: progress outside 0–100 (bad/echoed wire data)
        # must not yield negative area or more than the whole task.
        return min(max(remaining, 0.0), max(task_f, 0.0))


class LymowCleanHistoryDetailsSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Exposes per-session details from the most recent clean-history entry as attributes."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:history"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_last_clean_details"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Last mow details"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data.get(self._thing_name) or {}
        st = data.get("lastCleanStartType")
        if st is None:
            return None
        try:
            return int(st)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data.get(self._thing_name) or {}
        attrs: dict[str, Any] = {}
        for key, attr in (
            ("lastCleanStatusTimes", "status_times"),
            ("lastCleanSocVersion", "soc_version"),
            ("lastCleanErrorList", "error_list"),
            ("lastCleanMapTotalAreaM2", "map_total_area_m2"),
        ):
            val = data.get(key)
            if val is not None:
                attrs[attr] = val
        return attrs


class LymowBackupMapsSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Exposes the count of available map backups and the full list as an attribute."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:cloud-download"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_backup_maps"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Backup maps"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data.get(self._thing_name) or {}
        count = data.get("backupMapCount")
        return int(count) if count is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data.get(self._thing_name) or {}
        entries = data.get("backupMapList") or []
        return {"backups": entries}
