"""Tests for sensor.py — LymowSensor, LymowErrorSensor, LymowRtkSensor, LymowMapSensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTime
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
    assert "remediation" not in attrs


def test_error_sensor_surfaced_code_includes_remediation() -> None:
    from lymow.const import ERROR_REMEDIATION

    coord = _make_coord({"errorCode": 71})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["description"] == "Action timeout"
    assert attrs["remediation"] == ERROR_REMEDIATION[71]


def test_error_sensor_internal_code_omits_remediation() -> None:
    coord = _make_coord({"errorCode": 4})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert "remediation" not in attrs


def test_error_sensor_warning_codes_included_when_present() -> None:
    coord = _make_coord({"errorCode": 0, "warningCodes": [1, 999]})
    desc = next(d for d in SENSORS if d.key == "error_code")
    sensor = LymowErrorSensor(coord, DEVICE, desc)
    attrs = sensor.extra_state_attributes
    assert attrs["warning_codes"] == [1, 999]
    assert attrs["warning_descriptions"] == ["Wheel over-current", "Unknown (999)"]


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


def test_map_sensor_extra_attrs_path_rtk_and_progress() -> None:
    coord = _make_coord(
        {
            "mapData": {"goZones": []},
            "pathData": {"goZones": [{"hashId": "z1", "trackPoints": [{"x": 1.23456, "y": 2.34567}]}]},
            "rtkStatus": 2,
            "mowProgress": 45,
            "mowStripCount": 12,
            "totalTaskAreaM2": 350,
        }
    )
    attrs = LymowMapSensor(coord, DEVICE).extra_state_attributes
    # mow path track points are trimmed to 4 dp like zone polygons
    assert attrs["mow_path"] == {"goZones": [{"hashId": "z1", "trackPoints": [{"x": 1.2346, "y": 2.3457}]}]}
    assert attrs["rtkLabel"] == "Fixed"
    assert attrs["mowProgress"] == 45
    assert attrs["mowStripCount"] == 12
    assert attrs["totalTaskAreaM2"] == 350


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


def test_map_sensor_prefers_live_charging_station_loc_over_map_derived() -> None:
    """PbOutput.f24 ``chargingStationLoc`` is the live update channel; when
    both the map-query dock and the live dock are present and the live one
    is full, it wins so the card reflects a moved dock immediately."""
    map_cs = {"x": 1.0, "y": 2.0, "theta": 0.0}
    live_cs = {"x": 1.5, "y": 2.5, "theta": 0.1}
    coord = _make_coord({"mapData": {"chargingStation": map_cs}, "chargingStationLoc": live_cs})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["charging_station"] == live_cs


def test_map_sensor_merges_partial_live_dock_over_map_dock() -> None:
    """``chargingStationLoc`` can legally be partial (e.g. only ``y`` if
    only the north coordinate changed). A wholesale replacement would
    drop ``x`` / ``theta`` and break the card's geometry; the merge
    behavior keeps the map fields underneath."""
    map_cs = {"x": 1.0, "y": 2.0, "theta": 0.5}
    live_cs = {"y": 9.9}  # partial — only north
    coord = _make_coord({"mapData": {"chargingStation": map_cs}, "chargingStationLoc": live_cs})
    sensor = LymowMapSensor(coord, DEVICE)
    # x and theta survive from map; y is the fresher live value
    assert sensor.extra_state_attributes["charging_station"] == {"x": 1.0, "y": 9.9, "theta": 0.5}


def test_map_sensor_uses_live_dock_when_no_map_dock_yet() -> None:
    """If we have a live PbOutput.f24 update before any QUERY_MAP reply
    has populated mapData.chargingStation, the live one still surfaces."""
    live_cs = {"x": 3.0, "y": 4.0}
    coord = _make_coord({"mapData": {}, "chargingStationLoc": live_cs})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["charging_station"] == live_cs


def test_map_sensor_ignores_empty_live_dock_dict() -> None:
    """An empty ``chargingStationLoc`` dict (shouldn't happen — the decoder
    omits the key when no scalars are present — but defend regardless) must
    fall back to the map-derived dock rather than rendering an empty dict."""
    map_cs = {"x": 1.0, "y": 2.0}
    coord = _make_coord({"mapData": {"chargingStation": map_cs}, "chargingStationLoc": {}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["charging_station"] == map_cs


def test_map_sensor_ignores_non_dict_live_dock() -> None:
    """A future-mangled state where ``chargingStationLoc`` is the wrong
    type (string, list…) must not crash the sensor — fall back to map."""
    map_cs = {"x": 1.0, "y": 2.0}
    coord = _make_coord({"mapData": {"chargingStation": map_cs}, "chargingStationLoc": "junk"})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["charging_station"] == map_cs


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


def test_map_sensor_extra_attrs_has_mowing_settings() -> None:
    ms = {"cutHeight": 60, "pathSpacing": 35, "moveSpeed": 0.6}
    coord = _make_coord({"mapData": {"globalZoneConfig": ms}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["mowing_settings"] == ms


def test_map_sensor_extra_attrs_has_channel_config() -> None:
    cc = {"detectMode": 2, "cutHeight": 60}
    coord = _make_coord({"mapData": {"globalChannelConfig": cc}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert sensor.extra_state_attributes["channel_config"] == cc


def test_map_sensor_extra_attrs_mowing_settings_absent_when_not_decoded() -> None:
    coord = _make_coord({"mapData": {"goZones": []}})
    sensor = LymowMapSensor(coord, DEVICE)
    assert "mowing_settings" not in sensor.extra_state_attributes


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


def test_mission_time_sensor_returns_value() -> None:
    coord = _make_coord({"missionTimeMin": 46})
    desc = next(s for s in SENSORS if s.key == "mission_time")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == 46


def test_mission_time_sensor_is_duration_minutes() -> None:

    desc = next(s for s in SENSORS if s.key == "mission_time")
    assert desc.device_class == SensorDeviceClass.DURATION
    assert desc.native_unit_of_measurement == UnitOfTime.MINUTES


def test_heated_lens_times_sensor_reads_counter_state() -> None:
    """PbOutput.f37 → heatedLensTimes; sensor surfaces the live counter."""
    coord = _make_coord({"heatedLensTimes": 17})
    desc = next(s for s in SENSORS if s.key == "heated_lens_times")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == 17


def test_heated_lens_times_sensor_metadata_is_a_total_increasing_counter() -> None:
    """The heater count only goes up over the install lifetime — TOTAL_INCREASING
    so HA's long-term-stats handle it correctly."""
    desc = next(s for s in SENSORS if s.key == "heated_lens_times")
    assert desc.state_class == SensorStateClass.TOTAL_INCREASING
    assert desc.entity_registry_enabled_default is False


def test_ae_range_level_sensor_reads_label_string() -> None:
    """The label string is stored directly by decode_pboutput — the sensor
    renders the AE gear name (NONE / 1..6 / MAX) without re-mapping."""
    coord = _make_coord({"aeRangeLevel": "MAX"})
    desc = next(s for s in SENSORS if s.key == "ae_range_level")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "MAX"


def test_ae_range_level_sensor_disabled_by_default() -> None:
    """Diagnostic field — most users won't care about camera AE tuning."""
    desc = next(s for s in SENSORS if s.key == "ae_range_level")
    assert desc.entity_registry_enabled_default is False


def test_output_ctrl_sensor_reads_label_string() -> None:
    """outputCtrl is the robot's "what I'm replying to" indicator; the
    decoder stores the label string so the sensor renders it directly."""
    coord = _make_coord({"outputCtrl": "QUERY_MAP"})
    desc = next(s for s in SENSORS if s.key == "output_ctrl")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "QUERY_MAP"


def test_output_ctrl_sensor_disabled_by_default() -> None:
    """Diagnostic — useful for traffic debugging but noise on a normal card."""
    desc = next(s for s in SENSORS if s.key == "output_ctrl")
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


def test_dotted_value_key_sensor_walks_nested_dict() -> None:
    """``value_key="networkInfo.cellularIp"`` walks the nested networkInfo dict."""
    coord = _make_coord({"networkInfo": {"cellularIp": "100.116.126.140"}})
    desc = next(s for s in SENSORS if s.key == "cellular_ip")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "100.116.126.140"


def test_lcd_pin_sensor_disabled_by_default_diagnostic_and_reads_value() -> None:
    from homeassistant.const import EntityCategory

    desc = next(s for s in SENSORS if s.key == "lcd_pin")
    assert desc.value_key == "robotConfig.lcdPin"
    assert desc.entity_registry_enabled_default is False  # opt-in: PIN is sensitive
    assert desc.entity_category == EntityCategory.DIAGNOSTIC
    coord = _make_coord({"robotConfig": {"lcdPin": "0000"}})
    assert LymowSensor(coord, DEVICE, desc).native_value == "0000"


def test_lcd_pin_sensor_none_when_absent() -> None:
    desc = next(s for s in SENSORS if s.key == "lcd_pin")
    coord = _make_coord({"robotConfig": {}})
    assert LymowSensor(coord, DEVICE, desc).native_value is None


def test_wifi_ssid_sensor_returns_none_when_network_info_missing() -> None:
    """Defensive: a stale state with no networkInfo dict must not raise."""
    coord = _make_coord({"battery": 80})
    desc = next(s for s in SENSORS if s.key == "wifi_ssid")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value is None


def test_cellular_ip_sensor_reads_dotted_path() -> None:
    coord = _make_coord({"networkInfo": {"cellularIp": "100.116.126.140"}})
    desc = next(s for s in SENSORS if s.key == "cellular_ip")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "100.116.126.140"


def test_rtk_advanced_sensors_read_dotted_l2_fields() -> None:
    """The Advanced Diagnostics sensors walk the nested rtkL2 dict."""
    coord = _make_coord(
        {
            "rtkL2": {
                "loraBandwidthL1Bps": 268,
                "hwDcVoltageL5V": 1.79,
                "cwInterferenceL2": 94,
                "antennaGainL5": 53,
            }
        }
    )
    by_key = {s.key: s for s in SENSORS}
    assert LymowSensor(coord, DEVICE, by_key["rtk_lora_bandwidth_l1"]).native_value == 268
    assert LymowSensor(coord, DEVICE, by_key["rtk_dc_voltage_l5"]).native_value == 1.79
    assert LymowSensor(coord, DEVICE, by_key["rtk_cw_interference_l2"]).native_value == 94
    assert LymowSensor(coord, DEVICE, by_key["rtk_antenna_gain_l5"]).native_value == 53


def test_mac_address_sensor_reads_value() -> None:
    coord = _make_coord({"macAddress": "F8:3D:C6:82:56:C1"})
    desc = next(s for s in SENSORS if s.key == "mac_address")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor.native_value == "F8:3D:C6:82:56:C1"


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


def test_last_clean_sensor_attrs_include_status_times_breakdown() -> None:
    """statusTimes packed-int32 array surfaces as both the raw per-status
    breakdown (so a card can render each bucket) and a total."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"statusTimes": [120, 0, 60, 30]}})
    attrs = LymowLastCleanSensor(coord, DEVICE).extra_state_attributes
    assert attrs == {"status_times_sec": [120, 0, 60, 30], "total_active_sec": 210}


def test_last_clean_sensor_attrs_omit_empty_status_times() -> None:
    """An empty statusTimes list shouldn't render as a zero-total attribute —
    suppress so the card can fall back to 'no data'."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"statusTimes": []}})
    assert LymowLastCleanSensor(coord, DEVICE).extra_state_attributes == {}


def test_last_clean_sensor_attrs_include_error_list_with_descriptions() -> None:
    """errorList entries surface with their ERROR_DESCRIPTIONS label so the
    card can render the human-readable cause without re-implementing the
    lookup itself."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"errorList": [{"code": 64, "percent": 73.0}, {"code": 55}]}})
    attrs = LymowLastCleanSensor(coord, DEVICE).extra_state_attributes
    assert attrs["error_list"] == [
        {"code": 64, "percent": 73.0, "description": "Robot inside no-go zone"},
        {"code": 55, "description": "Charging station not found"},
    ]


def test_last_clean_sensor_attrs_label_unknown_error_code() -> None:
    """An error code outside ERROR_DESCRIPTIONS surfaces with an Unknown(NN)
    label so the card still has *something* to render."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"errorList": [{"code": 9999}]}})
    attrs = LymowLastCleanSensor(coord, DEVICE).extra_state_attributes
    assert attrs["error_list"] == [{"code": 9999, "description": "Unknown (9999)"}]


def test_last_clean_sensor_attrs_skip_malformed_error_entries() -> None:
    """A future malformed decode that puts a non-dict or no-code entry into
    errorList must not blow up attr rendering — silently skip the bad one."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"errorList": [{"code": 31}, "not a dict", {"percent": 50.0}]}})
    attrs = LymowLastCleanSensor(coord, DEVICE).extra_state_attributes
    assert attrs["error_list"] == [{"code": 31, "description": "Low battery"}]


def test_last_clean_sensor_attrs_omit_empty_error_list() -> None:
    """An empty errorList shouldn't render — let the card render the no-errors
    case the same way it handles no-cleanReport-at-all."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"errorList": []}})
    assert LymowLastCleanSensor(coord, DEVICE).extra_state_attributes == {}


def test_last_clean_sensor_attrs_omit_error_list_when_filter_drops_all_entries() -> None:
    """If every entry in the raw errorList is malformed (no int code), the
    filter empties the list — drop the attribute entirely rather than render
    an empty-array placeholder."""
    from lymow.sensor import LymowLastCleanSensor

    coord = _make_coord({"cleanReport": {"errorList": ["junk", {"percent": 50.0}]}})
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


# ---------------------------------------------------------------------------
# LymowRobotTimezoneSensor — robotConfig.timezoneOffset (signed seconds east of UTC)
# ---------------------------------------------------------------------------


def _make_tz_coord(offset_seconds: int | None = None) -> MagicMock:
    state: dict = {"robotConfig": {}}
    if offset_seconds is not None:
        state["robotConfig"]["timezoneOffset"] = offset_seconds
    return _make_coord(state)


def test_robot_timezone_sensor_metadata_and_disabled_default() -> None:
    from lymow.sensor import LymowRobotTimezoneSensor

    e = LymowRobotTimezoneSensor(_make_tz_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_robot_timezone"
    assert e._attr_name == "Robot timezone"
    # Disabled by default — most users only need the Sync Timezone button.
    assert e._attr_entity_registry_enabled_default is False


def test_robot_timezone_sensor_formats_positive_offset_as_signed_hhmm() -> None:
    from lymow.sensor import LymowRobotTimezoneSensor

    e = LymowRobotTimezoneSensor(_make_tz_coord(9 * 3600), DEVICE)  # Asia/Tokyo
    assert e.native_value == "+09:00"
    assert e.extra_state_attributes == {"offset_seconds": 9 * 3600, "offset_hours": 9.0}


def test_robot_timezone_sensor_formats_negative_offset_and_half_hour() -> None:
    from lymow.sensor import LymowRobotTimezoneSensor

    # America/New_York during standard time: UTC-5
    e_ny = LymowRobotTimezoneSensor(_make_tz_coord(-5 * 3600), DEVICE)
    assert e_ny.native_value == "-05:00"
    assert e_ny.extra_state_attributes == {"offset_seconds": -5 * 3600, "offset_hours": -5.0}

    # Asia/Kolkata: UTC+5:30 — half-hour offset must format correctly.
    e_in = LymowRobotTimezoneSensor(_make_tz_coord(5 * 3600 + 30 * 60), DEVICE)
    assert e_in.native_value == "+05:30"
    assert e_in.extra_state_attributes["offset_hours"] == 5.5


def test_robot_timezone_sensor_unknown_when_offset_missing_or_out_of_bounds() -> None:
    from lymow.sensor import LymowRobotTimezoneSensor

    # Field absent → unknown (rather than guessing UTC). Both the state and the
    # attribute dict drop out so HA doesn't show a stale offset under an
    # already-unknown state.
    e_missing = LymowRobotTimezoneSensor(_make_tz_coord(None), DEVICE)
    assert e_missing.native_value is None
    assert e_missing.extra_state_attributes is None
    # Non-int wire payload (e.g. a string the robot shouldn't send but might
    # if firmware ever changes types) → unknown rather than crashing the
    # bound check or stringifying garbage. Build the state dict directly so
    # we can put a non-int in the slot _make_tz_coord otherwise restricts.
    coord_str = MagicMock()
    coord_str.data = {THING: {"robotConfig": {"timezoneOffset": "+09:00"}}}
    assert LymowRobotTimezoneSensor(coord_str, DEVICE).native_value is None
    # Outside the [-12h, +14h] real-world range — drop as hostile.
    assert LymowRobotTimezoneSensor(_make_tz_coord(15 * 3600), DEVICE).native_value is None
    assert LymowRobotTimezoneSensor(_make_tz_coord(-13 * 3600), DEVICE).native_value is None
    # Sub-minute offset — no real timezone has one; rejecting prevents the
    # ±HH:MM formatter from silently truncating stray seconds (e.g. 5h0m33s
    # would render as "+05:00" and lie about the actual configured value).
    assert LymowRobotTimezoneSensor(_make_tz_coord(5 * 3600 + 33), DEVICE).native_value is None


async def test_async_setup_entry_registers_robot_timezone_sensor() -> None:
    from lymow.const import DOMAIN
    from lymow.sensor import LymowRobotTimezoneSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert any(isinstance(e, LymowRobotTimezoneSensor) for e in added)


# ---------------------------------------------------------------------------
# LymowHeadlightWindowSensor — robotConfig.openLedTime / closeLedTime
# ---------------------------------------------------------------------------


def _make_hw_coord(open_tz: dict | None = None, close_tz: dict | None = None) -> MagicMock:
    state: dict = {"robotConfig": {}}
    if open_tz is not None:
        state["robotConfig"]["openLedTime"] = open_tz
    if close_tz is not None:
        state["robotConfig"]["closeLedTime"] = close_tz
    return _make_coord(state)


def test_headlight_window_sensor_metadata_and_disabled_default() -> None:
    from lymow.sensor import LymowHeadlightWindowSensor

    e = LymowHeadlightWindowSensor(_make_hw_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_headlight_window"
    assert e._attr_name == "Headlight schedule"
    assert e._attr_entity_registry_enabled_default is False


def test_headlight_window_sensor_formats_window_with_en_dash() -> None:
    from lymow.sensor import LymowHeadlightWindowSensor

    e = LymowHeadlightWindowSensor(
        _make_hw_coord({"hour": 21, "minute": 0}, {"hour": 6, "minute": 30}),
        DEVICE,
    )
    # En-dash (U+2013), matches the app's UI typography.
    assert e.native_value == "21:00–06:30"
    assert e.extra_state_attributes == {"open_time": "21:00", "close_time": "06:30"}


def test_headlight_window_sensor_unknown_when_either_end_missing() -> None:
    """One end missing → unknown state, but the present end still shows up in
    attributes so the user can spot which side dropped."""
    from lymow.sensor import LymowHeadlightWindowSensor

    only_open = LymowHeadlightWindowSensor(_make_hw_coord({"hour": 21, "minute": 0}, None), DEVICE)
    assert only_open.native_value is None
    assert only_open.extra_state_attributes == {"open_time": "21:00", "close_time": None}

    only_close = LymowHeadlightWindowSensor(_make_hw_coord(None, {"hour": 6, "minute": 0}), DEVICE)
    assert only_close.native_value is None
    assert only_close.extra_state_attributes == {"open_time": None, "close_time": "06:00"}


def test_headlight_window_sensor_unknown_when_robot_config_missing() -> None:
    from lymow.sensor import LymowHeadlightWindowSensor

    # No robotConfig key at all → state unknown, no attributes (avoids a
    # bare {"open_time": None, "close_time": None} attribute dict that
    # would just be noise in the UI).
    e = LymowHeadlightWindowSensor(_make_coord({}), DEVICE)
    assert e.native_value is None
    assert e.extra_state_attributes is None


def test_headlight_window_sensor_unknown_when_wire_payload_not_dict() -> None:
    """decode_robot_config already validates, but guard the sensor too in
    case state is built from a different source someday."""
    from lymow.sensor import LymowHeadlightWindowSensor

    coord = MagicMock()
    coord.data = {THING: {"robotConfig": {"openLedTime": "21:00", "closeLedTime": 600}}}
    assert LymowHeadlightWindowSensor(coord, DEVICE).native_value is None


def test_headlight_window_sensor_unknown_when_robot_config_not_dict() -> None:
    """A non-dict ``robotConfig`` from a malformed cache hydrate (list, str,
    int, …) must not crash ``.get`` — the sensor returns unknown."""
    from lymow.sensor import LymowHeadlightWindowSensor

    for bad in ([], ["junk"], "not-a-dict", 42):
        coord = MagicMock()
        coord.data = {THING: {"robotConfig": bad}}
        e = LymowHeadlightWindowSensor(coord, DEVICE)
        assert e.native_value is None
        assert e.extra_state_attributes is None


def test_headlight_window_sensor_unknown_when_outer_layers_not_dict() -> None:
    """The same defensive guard applies to every layer above ``robotConfig``:
    a truthy non-dict at ``coordinator.data`` or ``data[thing]`` (e.g. from a
    malformed cache hydrate) must drop to unknown, not raise AttributeError
    halfway through the walk. Build the entity with a sane coordinator (so
    DeviceInfo construction works), then mutate coordinator.data to the
    abnormal shape and re-read."""
    from lymow.sensor import LymowHeadlightWindowSensor

    # coordinator.data itself flips to a non-dict.
    e_data = LymowHeadlightWindowSensor(_make_coord({}), DEVICE)
    e_data.coordinator.data = "not-a-dict"
    assert e_data.native_value is None
    assert e_data.extra_state_attributes is None

    # data[thing] flips to a non-dict.
    e_thing = LymowHeadlightWindowSensor(_make_coord({}), DEVICE)
    e_thing.coordinator.data = {THING: ["bogus"]}
    assert e_thing.native_value is None
    assert e_thing.extra_state_attributes is None


def test_headlight_window_sensor_unknown_when_partial_or_out_of_range_dict() -> None:
    """A dict missing one key, with wrong types, or with out-of-range values
    must return None — never raise during state rendering."""
    from lymow.sensor import LymowHeadlightWindowSensor

    # Missing 'minute' key.
    coord_missing = MagicMock()
    coord_missing.data = {THING: {"robotConfig": {"openLedTime": {"hour": 21}}}}
    assert LymowHeadlightWindowSensor(coord_missing, DEVICE).native_value is None
    # Non-int 'hour'.
    coord_str_hour = MagicMock()
    coord_str_hour.data = {THING: {"robotConfig": {"openLedTime": {"hour": "21", "minute": 0}}}}
    assert LymowHeadlightWindowSensor(coord_str_hour, DEVICE).native_value is None
    # Out-of-range hour.
    e_bad_hour = LymowHeadlightWindowSensor(_make_hw_coord({"hour": 24, "minute": 0}, None), DEVICE)
    assert e_bad_hour.native_value is None
    # Out-of-range minute.
    e_bad_min = LymowHeadlightWindowSensor(_make_hw_coord({"hour": 5, "minute": 60}, None), DEVICE)
    assert e_bad_min.native_value is None


async def test_async_setup_entry_registers_headlight_window_sensor() -> None:
    from lymow.const import DOMAIN
    from lymow.sensor import LymowHeadlightWindowSensor

    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {}}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert any(isinstance(e, LymowHeadlightWindowSensor) for e in added)
