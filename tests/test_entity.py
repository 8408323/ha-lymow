"""Tests for entity.py — shared DeviceInfo."""

from __future__ import annotations

from unittest.mock import MagicMock

from lymow.entity import lymow_device_info

THING = "mower-001"


def _coord(data: dict | None = None) -> MagicMock:
    c = MagicMock()
    c.data = {THING: data} if data is not None else {}
    return c


def test_device_info_basic_identifiers_and_name() -> None:
    info = lymow_device_info(_coord(), {"deviceThingName": THING, "deviceName": "Mower 1"})
    assert info["identifiers"] == {("lymow", THING)}
    assert info["name"] == "Mower 1"
    assert info["manufacturer"] == "Lymow"
    assert info["model"] == "Robotic Lawn Mower"  # default when no deviceType
    assert "serial_number" not in info
    assert "sw_version" not in info


def test_device_info_enriches_from_coordinator_data_keys() -> None:
    # coordinator.data uses merged keys: deviceType / serialNumber / softwareVersion
    info = lymow_device_info(
        _coord({"deviceType": "L2-3000", "serialNumber": "SN9", "softwareVersion": "1.4.2"}),
        {"deviceThingName": THING, "deviceName": "Mower 1"},
    )
    assert info["model"] == "L2-3000"
    assert info["serial_number"] == "SN9"
    assert info["sw_version"] == "1.4.2"


def test_device_info_falls_back_to_raw_device_and_fwversion() -> None:
    info = lymow_device_info(
        _coord({"fwVersion": "2.0.0"}),  # MQTT-decoded firmware key
        {"deviceThingName": THING, "sn": "RAWSN", "deviceType": "L2"},
    )
    assert info["name"] == "RAWSN"  # deviceName absent → sn
    assert info["model"] == "L2"  # raw device deviceType
    assert info["serial_number"] == "RAWSN"  # raw device sn
    assert info["sw_version"] == "2.0.0"  # fwVersion fallback


def test_device_info_name_falls_back_to_thing_name() -> None:
    info = lymow_device_info(_coord(), {"deviceThingName": THING})
    assert info["name"] == THING
