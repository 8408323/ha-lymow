"""Tests for sensor.py — LymowSensor, LymowErrorSensor, LymowRtkSensor, LymowMapSensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from lymow.sensor import (
    SENSORS,
    LymowErrorSensor,
    LymowMapSensor,
    LymowRtkSensor,
    LymowSchedulesSensor,
    LymowSensor,
    LymowSensorDescription,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}
DEVICE_NO_NAME = {"deviceThingName": THING, "sn": "SN123"}
DEVICE_BARE = {"deviceThingName": THING}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: state or {}}
    return coord


# ---------------------------------------------------------------------------
# SENSORS description list
# ---------------------------------------------------------------------------


def test_sensors_have_unique_keys() -> None:
    keys = [d.key for d in SENSORS]
    assert len(keys) == len(set(keys))


def test_sensors_all_have_value_key() -> None:
    for desc in SENSORS:
        assert isinstance(desc, LymowSensorDescription)
        assert desc.value_key


# ---------------------------------------------------------------------------
# LymowSensor
# ---------------------------------------------------------------------------


def test_sensor_init_sets_unique_id() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor._attr_unique_id == f"{THING}_battery"


def test_sensor_uses_has_entity_name_and_description_name() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE, desc)
    # has_entity_name=True with no _attr_name → HA renders description.name under the device.
    assert sensor._attr_has_entity_name is True
    assert sensor.entity_description.name == "Battery"
    assert sensor._attr_device_info["name"] == "Mower 1"


def test_sensor_device_name_falls_back_to_sn() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE_NO_NAME, desc)
    assert sensor._attr_device_info["name"] == "SN123"


def test_sensor_device_name_falls_back_to_thing_name() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE_BARE, desc)
    assert sensor._attr_device_info["name"] == THING


def test_sensor_native_value_returns_value() -> None:
    coord = _make_coord({"battery": 75})
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == 75


def test_sensor_native_value_missing_key_returns_none() -> None:
    coord = _make_coord({})
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value is None


def test_sensor_native_value_no_thing_data_returns_none() -> None:
    coord = MagicMock()
    coord.data = {}  # no entry for THING
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# LymowErrorSensor
# ---------------------------------------------------------------------------


def test_error_sensor_extra_attrs_has_description() -> None:
    coord = _make_coord({"errorCode": 0})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert "description" in attrs


def test_error_sensor_extra_attrs_known_code() -> None:
    from lymow.const import ERROR_DESCRIPTIONS

    first_code = next(iter(ERROR_DESCRIPTIONS))
    coord = _make_coord({"errorCode": first_code})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["description"] == ERROR_DESCRIPTIONS[first_code]


def test_error_sensor_unknown_code_has_unknown_description() -> None:
    coord = _make_coord({"errorCode": 9999})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert "Unknown" in attrs["description"]


def test_error_sensor_warning_codes_included_when_present() -> None:
    coord = _make_coord({"errorCode": 0, "warningCodes": [1, 2]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["warning_codes"] == [1, 2]


def test_error_sensor_warning_codes_absent_when_missing() -> None:
    coord = _make_coord({"errorCode": 0})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert "warning_codes" not in attrs


def test_error_sensor_error_codes_included_when_present() -> None:
    coord = _make_coord({"errorCode": 0, "errorCodes": [7]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["error_codes"] == [7]


def test_error_sensor_emits_warning_descriptions_when_warning_codes_present() -> None:
    """warning_codes → warning_descriptions: every code maps to its APK label."""
    coord = _make_coord({"errorCode": 0, "warningCodes": [1, 19]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["warning_descriptions"] == ["Wheel over-current", "Localisation: RTK signal bad"]


def test_error_sensor_emits_error_descriptions_when_error_codes_present() -> None:
    coord = _make_coord({"errorCode": 0, "errorCodes": [55, 64]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["error_descriptions"] == ["Charging station not found", "Robot inside no-go zone"]


def test_error_sensor_unknown_warning_code_falls_back_to_unknown_label() -> None:
    """Untrusted wire data — a future firmware code shouldn't break the sensor."""
    coord = _make_coord({"errorCode": 0, "warningCodes": [9999]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert "Unknown (9999)" in attrs["warning_descriptions"]


def test_error_sensor_non_numeric_code_does_not_crash_sensor() -> None:
    """A malformed cloud payload (non-numeric entry in warningCodes /
    errorCodes) must not raise during attribute rendering — the sensor
    should fall back to an "Unknown" label so state updates keep flowing."""
    coord = _make_coord({"errorCode": 0, "warningCodes": ["junk"], "errorCodes": [None]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["warning_descriptions"] == ["Unknown ('junk')"]
    assert attrs["error_descriptions"] == ["Unknown (None)"]


def test_error_sensor_native_value_none_treated_as_zero() -> None:
    coord = _make_coord({})  # no errorCode key → native_value is None
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    # Should not raise; code 0 treated as zero
    attrs = sensor.extra_state_attributes
    assert "description" in attrs


# ---------------------------------------------------------------------------
# LymowRtkSensor
# ---------------------------------------------------------------------------


def test_rtk_sensor_init() -> None:
    coord = _make_coord()
    sensor = LymowRtkSensor(coord, DEVICE)
    assert "rtk_status" in sensor._attr_unique_id
    assert sensor._attr_entity_registry_enabled_default is False


def test_rtk_sensor_native_value_none_when_absent() -> None:
    coord = _make_coord({})
    sensor = LymowRtkSensor(coord, DEVICE)
    assert sensor.native_value is None


def test_rtk_sensor_native_value_known_status() -> None:
    coord = _make_coord({"rtkStatus": 2})
    sensor = LymowRtkSensor(coord, DEVICE)
    assert "Fixed" in (sensor.native_value or "")


def test_rtk_sensor_native_value_float_status() -> None:
    coord = _make_coord({"rtkStatus": 1})
    sensor = LymowRtkSensor(coord, DEVICE)
    assert "Float" in (sensor.native_value or "")


def test_rtk_sensor_native_value_unknown_status() -> None:
    coord = _make_coord({"rtkStatus": 99})
    sensor = LymowRtkSensor(coord, DEVICE)
    assert "Unknown" in (sensor.native_value or "")


def test_rtk_sensor_extra_attrs_empty_when_no_data() -> None:
    coord = _make_coord({})
    sensor = LymowRtkSensor(coord, DEVICE)
    assert sensor.extra_state_attributes == {}


def test_rtk_sensor_extra_attrs_includes_rtk_fields() -> None:
    coord = _make_coord({"rtkSatellites": 12, "rtkEastM": 1.0, "rtkNorthM": 2.0})
    sensor = LymowRtkSensor(coord, DEVICE)
    attrs = sensor.extra_state_attributes
    assert attrs["rtkSatellites"] == 12
    assert attrs["rtkEastM"] == 1.0


def test_rtk_sensor_extra_attrs_includes_pose_fields() -> None:
    coord = _make_coord({"poseEastM": 0.5, "poseNorthM": 0.3, "poseThetaRad": 1.2})
    sensor = LymowRtkSensor(coord, DEVICE)
    attrs = sensor.extra_state_attributes
    assert "poseEastM" in attrs
    assert "poseThetaRad" in attrs


# ---------------------------------------------------------------------------
# LymowMapSensor
# ---------------------------------------------------------------------------


def test_map_sensor_init() -> None:
    coord = _make_coord()
    sensor = LymowMapSensor(coord, DEVICE)
    assert "_map" in sensor._attr_unique_id


def test_map_sensor_native_value_none_when_no_map_data() -> None:
    coord = _make_coord({})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.native_value is None


def test_map_sensor_native_value_counts_go_zones() -> None:
    coord = _make_coord({"mapData": {"goZones": [{"hashId": "z1"}, {"hashId": "z2"}]}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.native_value == 2


def test_map_sensor_native_value_empty_go_zones() -> None:
    coord = _make_coord({"mapData": {"goZones": []}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.native_value == 0


def test_map_sensor_extra_attrs_has_go_zones() -> None:
    zones = [{"hashId": "z1", "polygon": []}]
    coord = _make_coord({"mapData": {"goZones": zones}})
    sensor = LymowMapSensor(coord, DEVICE)
    attrs = sensor.extra_state_attributes
    assert attrs["go_zones"] == zones


def test_map_sensor_extra_attrs_has_nogo_zones() -> None:
    nogo = [{"hashId": "n1"}]
    coord = _make_coord({"mapData": {"nogoZones": nogo}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["nogo_zones"] == nogo


def test_map_sensor_extra_attrs_has_channels() -> None:
    channels = [{"hashId": "ch1", "zone1": "z1", "zone2": "z2"}]
    coord = _make_coord({"mapData": {"channels": channels}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["channels"] == channels


def test_map_sensor_extra_attrs_has_gps_origin() -> None:
    origin = {"lat": 12.0, "lon": 65.0}
    coord = _make_coord({"mapData": {"gpsOrigin": origin}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["gps_origin"] == origin


def test_map_sensor_extra_attrs_has_charging_station() -> None:
    cs = {"x": 1.0, "y": 2.0}
    coord = _make_coord({"mapData": {"chargingStation": cs}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["charging_station"] == cs


def test_map_sensor_extra_attrs_includes_robot_pose() -> None:
    coord = _make_coord({"poseEastM": 0.1, "poseNorthM": 0.2, "poseThetaRad": 0.3, "mapData": {}})
    sensor = LymowMapSensor(coord, DEVICE)
    attrs = sensor.extra_state_attributes
    assert attrs["poseEastM"] == 0.1
    assert attrs["poseThetaRad"] == 0.3


def test_map_sensor_extra_attrs_omits_absent_pose_fields() -> None:
    coord = _make_coord({"mapData": {}})
    sensor = LymowMapSensor(coord, DEVICE)
    attrs = sensor.extra_state_attributes
    assert "poseEastM" not in attrs


def test_map_sensor_extra_attrs_empty_when_no_data() -> None:
    coord = _make_coord({})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes == {}


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_creates_entities() -> None:
    coord = _make_coord({"battery": 80})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {"lymow": {"entry-1": coord}}

    entry = MagicMock()
    entry.entry_id = "entry-1"

    from lymow.const import DOMAIN

    hass.data = {DOMAIN: {"entry-1": coord}}

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert any(isinstance(e, LymowSensor) for e in added)
    assert any(isinstance(e, LymowErrorSensor) for e in added)
    assert any(isinstance(e, LymowRtkSensor) for e in added)
    assert any(isinstance(e, LymowMapSensor) for e in added)


async def test_async_setup_entry_multiple_devices() -> None:
    device2 = {"deviceThingName": "mower-002", "deviceName": "Mower 2"}
    coord = MagicMock()
    coord.data = {THING: {}, "mower-002": {}}
    coord.devices = [DEVICE, device2]

    from lymow.const import DOMAIN

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    unique_ids = [e._attr_unique_id for e in added]
    assert any("mower-001" in uid for uid in unique_ids)
    assert any("mower-002" in uid for uid in unique_ids)


# ---------------------------------------------------------------------------
# mow_progress / mow_strip_count sensors
# ---------------------------------------------------------------------------


def test_mow_progress_sensor_returns_value() -> None:
    coord = _make_coord({"mowProgress": 52.6})
    desc = next(s for s in SENSORS if s.key == "mow_progress")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == 52.6


def test_mow_progress_sensor_returns_none_when_absent() -> None:
    coord = _make_coord({})
    desc = next(s for s in SENSORS if s.key == "mow_progress")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value is None


def test_mow_strip_count_sensor_returns_value() -> None:
    coord = _make_coord({"mowStripCount": 42})
    desc = next(s for s in SENSORS if s.key == "mow_strip_count")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == 42


def test_mow_strip_count_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "mow_strip_count")
    assert desc.entity_registry_enabled_default is False


# ---------------------------------------------------------------------------
# robot_state sensor
# ---------------------------------------------------------------------------


def test_robot_state_sensor_returns_value() -> None:
    coord = _make_coord({"robotState": 5})
    desc = next(s for s in SENSORS if s.key == "robot_state")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == 5


def test_robot_state_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "robot_state")
    assert desc.entity_registry_enabled_default is False


# ---------------------------------------------------------------------------
# RTK sensor — status 3 = RTK fixed
# ---------------------------------------------------------------------------


def test_rtk_sensor_native_value_rtk_fixed_status() -> None:
    coord = _make_coord({"rtkStatus": 3})
    sensor = LymowRtkSensor(coord, DEVICE)
    assert "RTK fixed" in (sensor.native_value or "")


# ---------------------------------------------------------------------------
# wifi_rssi_dbm sensor
# ---------------------------------------------------------------------------


def test_wifi_rssi_dbm_sensor_returns_value() -> None:
    coord = _make_coord({"wifiRssiDbm": -77})
    desc = next(s for s in SENSORS if s.key == "wifi_rssi_dbm")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == -77


def test_wifi_rssi_dbm_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "wifi_rssi_dbm")
    assert desc.entity_registry_enabled_default is False


# ---------------------------------------------------------------------------
# Pose sensors (poseEastM / poseNorthM via SENSORS descriptions; poseThetaRad
# via the dedicated LymowPoseHeadingSensor that converts radians→degrees)
# ---------------------------------------------------------------------------


def test_pose_east_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "pose_east_m")
    assert desc.entity_registry_enabled_default is False
    assert desc.value_key == "poseEastM"


def test_pose_north_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "pose_north_m")
    assert desc.entity_registry_enabled_default is False
    assert desc.value_key == "poseNorthM"


# ---------------------------------------------------------------------------
# Static device-list-query diagnostic sensors (serial, model, BT, SIM, fw, registered)
# ---------------------------------------------------------------------------


def test_serial_number_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "serial_number")
    assert desc.entity_registry_enabled_default is False
    assert desc.value_key == "serialNumber"


def test_serial_number_sensor_reads_value() -> None:
    coord = _make_coord({"serialNumber": "LR011A09A17B6521"})
    desc = next(s for s in SENSORS if s.key == "serial_number")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "LR011A09A17B6521"


def test_model_sensor_disabled_by_default() -> None:
    desc = next(s for s in SENSORS if s.key == "model")
    assert desc.entity_registry_enabled_default is False
    assert desc.value_key == "deviceType"


def test_bluetooth_name_sensor_reads_value() -> None:
    coord = _make_coord({"deviceBluetooth": "Lymow_7B6521"})
    desc = next(s for s in SENSORS if s.key == "bluetooth_name")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "Lymow_7B6521"


def test_sim_id_sensor_reads_value() -> None:
    coord = _make_coord({"simId": "89320420000094505458"})
    desc = next(s for s in SENSORS if s.key == "sim_id")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "89320420000094505458"


def test_firmware_minimum_sensor_reads_value() -> None:
    coord = _make_coord({"fwMinVersion": "v2.1.43"})
    desc = next(s for s in SENSORS if s.key == "firmware_minimum")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "v2.1.43"


def test_registered_at_sensor_is_timestamp() -> None:
    from datetime import datetime, timezone

    from lymow.sensor import SensorDeviceClass

    desc = next(s for s in SENSORS if s.key == "registered_at")
    assert desc.device_class == SensorDeviceClass.TIMESTAMP
    coord = _make_coord({"createdAt": datetime(2026, 5, 6, 16, 33, 39, tzinfo=timezone.utc)})
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == datetime(2026, 5, 6, 16, 33, 39, tzinfo=timezone.utc)


def test_pose_heading_sensor_converts_radians_to_degrees() -> None:
    import math
    from unittest.mock import MagicMock

    from lymow.sensor import LymowPoseHeadingSensor

    coord = MagicMock()
    coord.data = {"mower-001": {"poseThetaRad": math.pi / 2}}
    e = LymowPoseHeadingSensor(coord, {"deviceThingName": "mower-001"})
    # pi/2 rad → 90°
    assert e.native_value == 90.0


def test_pose_heading_sensor_wraps_to_zero_to_360() -> None:
    import math
    from unittest.mock import MagicMock

    from lymow.sensor import LymowPoseHeadingSensor

    coord = MagicMock()
    # -pi/2 rad → -90° → wrapped to 270°
    coord.data = {"mower-001": {"poseThetaRad": -math.pi / 2}}
    e = LymowPoseHeadingSensor(coord, {"deviceThingName": "mower-001"})
    assert e.native_value == 270.0


def test_pose_heading_sensor_none_when_missing() -> None:
    from unittest.mock import MagicMock

    from lymow.sensor import LymowPoseHeadingSensor

    coord = MagicMock()
    coord.data = {"mower-001": {}}
    e = LymowPoseHeadingSensor(coord, {"deviceThingName": "mower-001"})
    assert e.native_value is None


def test_pose_heading_sensor_none_when_non_numeric() -> None:
    from unittest.mock import MagicMock

    from lymow.sensor import LymowPoseHeadingSensor

    coord = MagicMock()
    coord.data = {"mower-001": {"poseThetaRad": "not-a-number"}}
    e = LymowPoseHeadingSensor(coord, {"deviceThingName": "mower-001"})
    assert e.native_value is None


def test_pose_heading_sensor_unique_id_and_name() -> None:
    from unittest.mock import MagicMock

    from lymow.sensor import LymowPoseHeadingSensor

    coord = MagicMock()
    coord.data = {"mower-001": {}}
    e = LymowPoseHeadingSensor(coord, {"deviceThingName": "mower-001", "deviceName": "Mower 1"})
    assert e._attr_unique_id == "mower-001_pose_heading"
    assert e._attr_name == "Pose heading"
    assert e._attr_device_info["name"] == "Mower 1"


async def test_async_setup_entry_registers_pose_heading_sensor() -> None:
    """One LymowPoseHeadingSensor should be registered per device."""
    from unittest.mock import MagicMock

    from lymow.const import DOMAIN
    from lymow.sensor import LymowPoseHeadingSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    pose = [e for e in added if isinstance(e, LymowPoseHeadingSensor)]
    assert len(pose) == 1
    assert pose[0]._thing_name == THING


# ---------------------------------------------------------------------------
# LymowCleanHistoryDetailsSensor — exposes per-session attrs from last entry
# ---------------------------------------------------------------------------


def test_clean_history_details_native_value_is_start_type() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({"lastCleanStartType": 1})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e.native_value == 1


def test_clean_history_details_native_value_none_when_missing() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e.native_value is None


def test_clean_history_details_native_value_none_when_non_numeric() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({"lastCleanStartType": "manual"})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e.native_value is None


def test_clean_history_details_attrs_includes_status_times() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({"lastCleanStatusTimes": [{"status": 4, "duration": 120}]})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e.extra_state_attributes["status_times"] == [{"status": 4, "duration": 120}]


def test_clean_history_details_attrs_includes_soc_version_and_error_list() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord(
        {
            "lastCleanSocVersion": "v1.2.3",
            "lastCleanErrorList": [7, 12],
            "lastCleanMapTotalAreaM2": 850.5,
        }
    )
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    attrs = e.extra_state_attributes
    assert attrs["soc_version"] == "v1.2.3"
    assert attrs["error_list"] == [7, 12]
    assert attrs["map_total_area_m2"] == 850.5


def test_clean_history_details_attrs_empty_when_no_data() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e.extra_state_attributes == {}


def test_clean_history_details_disabled_by_default() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e._attr_entity_registry_enabled_default is False


def test_clean_history_details_unique_id_and_name() -> None:
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = _make_coord({})
    e = LymowCleanHistoryDetailsSensor(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_last_clean_details"
    assert e._attr_name == "Last mow details"
    assert e._attr_device_info["name"] == "Mower 1"


async def test_async_setup_entry_registers_clean_history_details_sensor() -> None:
    from unittest.mock import MagicMock

    from lymow.const import DOMAIN
    from lymow.sensor import LymowCleanHistoryDetailsSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    details = [e for e in added if isinstance(e, LymowCleanHistoryDetailsSensor)]
    assert len(details) == 1
    assert details[0]._thing_name == THING


# ---------------------------------------------------------------------------
# LymowBackupMapsSensor — count + full backup list as attribute
# ---------------------------------------------------------------------------


def test_backup_maps_sensor_native_value_is_count() -> None:
    from lymow.sensor import LymowBackupMapsSensor

    coord = _make_coord({"backupMapCount": 3})
    e = LymowBackupMapsSensor(coord, DEVICE)
    assert e.native_value == 3


def test_backup_maps_sensor_native_value_none_when_missing() -> None:
    from lymow.sensor import LymowBackupMapsSensor

    coord = _make_coord({})
    e = LymowBackupMapsSensor(coord, DEVICE)
    assert e.native_value is None


def test_backup_maps_sensor_attrs_include_list() -> None:
    from lymow.sensor import LymowBackupMapsSensor

    backups = [{"file": "a.pb", "backupTime": 200, "name": ""}]
    coord = _make_coord({"backupMapList": backups})
    e = LymowBackupMapsSensor(coord, DEVICE)
    assert e.extra_state_attributes == {"backups": backups}


def test_backup_maps_sensor_attrs_empty_when_no_list() -> None:
    from lymow.sensor import LymowBackupMapsSensor

    coord = _make_coord({})
    e = LymowBackupMapsSensor(coord, DEVICE)
    assert e.extra_state_attributes == {"backups": []}


def test_backup_maps_sensor_unique_id_and_disabled_default() -> None:
    from lymow.sensor import LymowBackupMapsSensor

    coord = _make_coord({})
    e = LymowBackupMapsSensor(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_backup_maps"
    assert "Backup maps" in e._attr_name
    assert e._attr_entity_registry_enabled_default is False


def test_backup_map_latest_at_sensor_is_timestamp() -> None:
    from datetime import datetime, timezone

    from lymow.sensor import SensorDeviceClass

    desc = next(s for s in SENSORS if s.key == "backup_map_latest_at")
    assert desc.device_class == SensorDeviceClass.TIMESTAMP
    assert desc.entity_registry_enabled_default is False
    coord = _make_coord({"backupMapLatestAt": datetime(2026, 5, 14, 14, 23, tzinfo=timezone.utc)})
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == datetime(2026, 5, 14, 14, 23, tzinfo=timezone.utc)


async def test_async_setup_entry_registers_backup_maps_sensor() -> None:
    from unittest.mock import MagicMock

    from lymow.const import DOMAIN
    from lymow.sensor import LymowBackupMapsSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    backup = [e for e in added if isinstance(e, LymowBackupMapsSensor)]
    assert len(backup) == 1
    assert backup[0]._thing_name == THING


# ---------------------------------------------------------------------------
# LymowSchedulesSensor
# ---------------------------------------------------------------------------


def test_schedules_sensor_none_until_first_reply() -> None:
    sensor = LymowSchedulesSensor(_make_coord({}), DEVICE)
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {"schedules": []}


def test_schedules_sensor_counts_and_exposes_entries() -> None:
    entries = [
        {"dayOfWeek": [1, 3, 5], "hour": 7, "minute": 30, "zones": ["z1"], "isRepeated": True, "isDisabled": False},
        {"dayOfWeek": [0], "hour": 19, "minute": 0, "zones": [], "isRepeated": False, "isDisabled": True},
    ]
    sensor = LymowSchedulesSensor(_make_coord({"schedules": entries}), DEVICE)
    assert sensor.native_value == 2
    assert sensor.extra_state_attributes == {"schedules": entries}
    assert sensor._attr_unique_id == f"{THING}_schedules"
    assert sensor._attr_name == "Mow schedules"


def test_schedules_sensor_empty_list_is_zero() -> None:
    sensor = LymowSchedulesSensor(_make_coord({"schedules": []}), DEVICE)
    assert sensor.native_value == 0


def test_remaining_area_derived_from_task_and_progress() -> None:
    from lymow.sensor import LymowRemainingAreaSensor

    # 1567 m² task, 25% done -> 1175.25 remaining
    e = LymowRemainingAreaSensor(_make_coord({"totalTaskAreaM2": 1567.0, "mowProgress": 25.0}), DEVICE)
    assert e._attr_unique_id == f"{THING}_remaining_area"
    assert abs(e.native_value - 1175.25) < 0.01


def test_remaining_area_full_when_progress_zero() -> None:
    from lymow.sensor import LymowRemainingAreaSensor

    e = LymowRemainingAreaSensor(_make_coord({"totalTaskAreaM2": 1567.0, "mowProgress": 0.0}), DEVICE)
    assert e.native_value == 1567.0


def test_remaining_area_clamps_at_zero() -> None:
    from lymow.sensor import LymowRemainingAreaSensor

    e = LymowRemainingAreaSensor(_make_coord({"totalTaskAreaM2": 1567.0, "mowProgress": 110.0}), DEVICE)
    assert e.native_value == 0.0


def test_remaining_area_clamps_at_task_for_negative_progress() -> None:
    from lymow.sensor import LymowRemainingAreaSensor

    e = LymowRemainingAreaSensor(_make_coord({"totalTaskAreaM2": 1567.0, "mowProgress": -10.0}), DEVICE)
    assert e.native_value == 1567.0


def test_remaining_area_none_when_fields_missing() -> None:
    from lymow.sensor import LymowRemainingAreaSensor

    assert LymowRemainingAreaSensor(_make_coord({"totalTaskAreaM2": 1567.0}), DEVICE).native_value is None
    assert LymowRemainingAreaSensor(_make_coord({"mowProgress": 10.0}), DEVICE).native_value is None


def test_remaining_area_none_on_bad_type() -> None:
    from lymow.sensor import LymowRemainingAreaSensor

    e = LymowRemainingAreaSensor(_make_coord({"totalTaskAreaM2": "x", "mowProgress": 10.0}), DEVICE)
    assert e.native_value is None


async def test_async_setup_entry_registers_remaining_area_sensor() -> None:
    from unittest.mock import MagicMock

    from lymow.const import DOMAIN
    from lymow.sensor import LymowRemainingAreaSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert any(isinstance(e, LymowRemainingAreaSensor) for e in added)


# ---------------------------------------------------------------------------
# LymowLastCleanSensor — PbCleanReport timestamp + end-type + battery
# ---------------------------------------------------------------------------


def test_last_clean_sensor_native_value_is_utc_timestamp() -> None:
    from datetime import UTC, datetime

    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"cleanStartTime": 1_700_000_000}})
    e = LymowLastCleanSensor(coord, DEVICE)
    assert e.native_value == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_last_clean_sensor_native_value_none_when_missing() -> None:
    from lymow.sensor import LymowLastCleanSensor

    assert LymowLastCleanSensor(_make_coord(None), DEVICE).native_value is None
    assert LymowLastCleanSensor(_make_coord({}), DEVICE).native_value is None
    assert LymowLastCleanSensor(_make_coord({"cleanReport": {}}), DEVICE).native_value is None


def test_last_clean_sensor_native_value_none_when_start_invalid() -> None:
    """A non-int or non-positive timestamp must not surface as a 1970-epoch date."""
    from lymow.sensor import LymowLastCleanSensor

    assert LymowLastCleanSensor(_make_coord({"cleanReport": {"cleanStartTime": 0}}), DEVICE).native_value is None
    assert LymowLastCleanSensor(_make_coord({"cleanReport": {"cleanStartTime": "bad"}}), DEVICE).native_value is None


def test_last_clean_sensor_attrs_resolve_end_type_and_battery() -> None:
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"cleanStartTime": 1_700_000_000, "mowEndType": 1, "usedBattery": 30}})
    e = LymowLastCleanSensor(coord, DEVICE)
    assert e.extra_state_attributes == {"end_type": "COMPLETED", "used_battery_pct": 30}


def test_last_clean_sensor_drops_out_of_range_end_type() -> None:
    """The decoder filters mowEndType to 0-2; if anything else ever reaches
    the sensor (skipped decoder, future test fixture), drop it silently
    rather than rendering a fake 'UNKNOWN_*' label."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"mowEndType": 7}})
    assert LymowLastCleanSensor(coord, DEVICE).extra_state_attributes == {}


def test_last_clean_sensor_attrs_empty_when_no_report() -> None:
    from lymow.sensor import LymowLastCleanSensor

    assert LymowLastCleanSensor(_make_coord({}), DEVICE).extra_state_attributes == {}


def test_last_clean_sensor_unique_id_and_disabled_default() -> None:
    from lymow.sensor import LymowLastCleanSensor

    e = LymowLastCleanSensor(_make_coord({}), DEVICE)
    assert e._attr_unique_id == f"{THING}_last_mow_session"
    assert e._attr_name == "Last mow session"
    assert e._attr_entity_registry_enabled_default is False


async def test_async_setup_entry_registers_last_clean_sensor() -> None:
    from unittest.mock import MagicMock

    from lymow.const import DOMAIN
    from lymow.sensor import LymowLastCleanSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert any(isinstance(e, LymowLastCleanSensor) for e in added)
