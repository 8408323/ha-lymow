"""Lymow sensors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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

from .const import DOMAIN, ERROR_DESCRIPTIONS, MOW_END_TYPES, WARNING_DESCRIPTIONS
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
    # PbRobotInfo extras (decoded from PbOutput field 5 — APK fn #9734).
    LymowSensorDescription(
        key="bt_signal",
        name="Bluetooth signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        value_key="btSignalQuality",
        entity_registry_enabled_default=False,
    ),
    # PbDeviceProfile extras (decoded from PbOutput field 10 — APK fn #9170).
    LymowSensorDescription(
        key="wifi_ssid",
        name="Wi-Fi SSID",
        value_key="wifiSsid",
        icon="mdi:wifi-marker",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="rtk_serial",
        name="RTK base serial",
        value_key="rtkSn",
        icon="mdi:satellite-uplink",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="wheel_firmware",
        name="Wheel firmware",
        value_key="wheelVer",
        icon="mdi:car-cog",
        entity_registry_enabled_default=False,
    ),
    LymowSensorDescription(
        key="blade_firmware",
        name="Blade firmware",
        value_key="knifeVer",
        icon="mdi:saw-blade",
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
        # PbCleanInfo.cleanTime (f1, int seconds): time spent mowing the
        # current session — initial APK RE mislabelled this as a "strip
        # count" before the wire layout was confirmed against
        # PbCleanInfo.encode (Hermes #9770). The entity ``key`` is kept
        # to preserve existing user automations / entity_ids; only the
        # display name, device class, and unit have moved to match
        # what the field actually carries.
        key="mow_strip_count",
        name="Mow elapsed time",
        value_key="mowStripCount",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:timer-outline",
        entity_registry_enabled_default=False,
    ),
    # PbCleanInfo (PbOutput field 12) additional fields surfaced from APK RE.
    LymowSensorDescription(
        key="remain_clean_time",
        name="Mow time remaining",
        value_key="remainCleanTimeSec",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:clock-end",
    ),
    LymowSensorDescription(
        key="map_area",
        name="Total map area",
        value_key="mapAreaM2",
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:texture-box",
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
        entities.append(LymowLastCleanSensor(coordinator, device))
        entities.append(LymowRobotTimezoneSensor(coordinator, device))
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
        return self.coordinator.data.get(self._thing_name, {}).get(self.entity_description.value_key)


class LymowErrorSensor(LymowSensor):
    """Error code sensor that also exposes a human-readable description and warning codes."""

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data.get(self._thing_name, {})
        code = self.native_value or 0
        attrs: dict[str, Any] = {
            "description": _describe(ERROR_DESCRIPTIONS, code),
        }
        warning_codes = data.get("warningCodes")
        if warning_codes is not None:
            attrs["warning_codes"] = warning_codes
            attrs["warning_descriptions"] = [_describe(WARNING_DESCRIPTIONS, w) for w in warning_codes]
        all_error_codes = data.get("errorCodes")
        if all_error_codes is not None:
            attrs["error_codes"] = all_error_codes
            attrs["error_descriptions"] = [_describe(ERROR_DESCRIPTIONS, e) for e in all_error_codes]
        return attrs


def _describe(table: dict[int, str], code: Any) -> str:
    """Look up ``code`` in ``table`` and return its label, or ``"Unknown (...)"``.

    Wire data is untrusted: a future firmware (or a malformed payload) may put
    a non-numeric value in the warning/error code list. Treat any conversion
    failure as an unknown code rather than letting the sensor's state-update
    blow up — the user's automations would silently stop firing otherwise.
    """
    try:
        key = int(code)
    except (TypeError, ValueError):
        return f"Unknown ({code!r})"
    return table.get(key, f"Unknown ({key})")


class LymowRtkSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """RTK GPS fix quality sensor."""

    _attr_has_entity_name = True

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
        if "channels" in map_data:
            attrs["channels"] = map_data["channels"]
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


class LymowLastCleanSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """Last mowing session — PbCleanReport from QUERY_CLEANING_SUMMARY.

    Native value is the session start timestamp; end-type (completed,
    user-cancelled, or none) and battery-used are surfaced as attributes
    so a Lovelace card can render a single 'Last mow' tile with both
    'when' and 'how it ended'.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-end"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_last_mow_session"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Last mow session"

    @property
    def _report(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._thing_name, {}).get("cleanReport") or {}

    @property
    def native_value(self) -> datetime | None:
        # decode_clean_report already bounds cleanStartTime to a sane POSIX
        # epoch range, so fromtimestamp can't raise here. We still re-check
        # the type/positivity in case a future code path skips the decoder.
        start = self._report.get("cleanStartTime")
        if not isinstance(start, int) or start <= 0:
            return None
        return datetime.fromtimestamp(start, tz=UTC)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        report = self._report
        attrs: dict[str, Any] = {}
        end_type = report.get("mowEndType")
        if isinstance(end_type, int) and end_type in MOW_END_TYPES:
            attrs["end_type"] = MOW_END_TYPES[end_type]
        used = report.get("usedBattery")
        if isinstance(used, int):
            attrs["used_battery_pct"] = used
        status_times = report.get("statusTimes")
        if isinstance(status_times, list) and status_times:
            # Packed repeated int32 from PbCleanReport.f5: array[i] = seconds
            # spent at workStatus i during the session. Card can render the
            # raw breakdown directly; an additional total saves the user from
            # summing it themselves.
            attrs["status_times_sec"] = status_times
            attrs["total_active_sec"] = sum(status_times)
        error_list = report.get("errorList")
        if isinstance(error_list, list) and error_list:
            # Each entry from PbCleanReport.f4 has {code: int, percent: int (0-100)?}.
            # Surface the raw list plus a human-readable label per code so the
            # card can render "ERR 64 (Robot inside no-go zone) at 73.0%"
            # without re-implementing the lookup. Skip entries that lost their
            # code (malformed wire / future-shape entry) — if the filter empties
            # the list entirely, drop the attribute rather than render an
            # empty-array placeholder.
            decorated = [
                {
                    **e,
                    "description": ERROR_DESCRIPTIONS.get(e["code"], f"Unknown ({e['code']})"),
                }
                for e in error_list
                if isinstance(e, dict) and isinstance(e.get("code"), int)
            ]
            if decorated:
                attrs["error_list"] = decorated
        return attrs


class LymowRobotTimezoneSensor(CoordinatorEntity[LymowCoordinator], SensorEntity):
    """The robot's configured timezone offset from UTC.

    Reads ``PbRobotConfig.timezoneOffset`` (f21, signed int32 seconds east of
    UTC — what the app's setTimezone (Hermes #9036) writes). Surfaces it as an
    ``±HH:MM`` string and exposes ``offset_seconds`` / ``offset_hours`` for
    automations that prefer numerics. Disabled by default because most users
    only need the Sync Timezone button — this is for spotting drift between
    the robot and HA.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:earth"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_robot_timezone"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "Robot timezone"

    @property
    def _offset_seconds(self) -> int | None:
        offset = (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig", {}).get("timezoneOffset")
        # The robot can plausibly land anywhere from UTC-12 to UTC+14 — bound
        # the wire data so a corrupted pboutput can't surface a 200-hour
        # offset. Also reject sub-minute resolution: real-world timezones are
        # always whole minutes (no zone has a sub-minute offset), and the
        # ±HH:MM format below would silently truncate stray seconds, so a
        # corrupted payload like 5*3600+33 must report unknown instead.
        if not isinstance(offset, int) or not -12 * 3600 <= offset <= 14 * 3600 or offset % 60 != 0:
            return None
        return offset

    @property
    def native_value(self) -> str | None:
        seconds = self._offset_seconds
        if seconds is None:
            return None
        sign = "-" if seconds < 0 else "+"
        magnitude = abs(seconds)
        hours, remainder = divmod(magnitude, 3600)
        minutes = remainder // 60
        return f"{sign}{hours:02d}:{minutes:02d}"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        seconds = self._offset_seconds
        if seconds is None:
            return None
        return {"offset_seconds": seconds, "offset_hours": round(seconds / 3600, 2)}
