"""Tests for sensor.py — LymowSensor, LymowErrorSensor, LymowRtkSensor, LymowMapSensor."""

from __future__ import annotations

from unittest.mock import MagicMock

from lymow.sensor import (
    SENSORS,
    LymowErrorSensor,
    LymowMapSensor,
    LymowRtkSensor,
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


def test_sensor_name_uses_device_name() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE, desc)
    assert sensor._attr_name == "Mower 1 Battery"


def test_sensor_name_falls_back_to_sn() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE_NO_NAME, desc)
    assert "SN123" in sensor._attr_name


def test_sensor_name_falls_back_to_thing_name() -> None:
    coord = _make_coord()
    desc = next(d for d in SENSORS if d.key == "battery")
    sensor = LymowSensor(coord, DEVICE_BARE, desc)
    assert THING in sensor._attr_name


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


def test_map_sensor_extra_attrs_has_gps_origin() -> None:
    origin = {"lat": 59.0, "lon": 18.0}
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
