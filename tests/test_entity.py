"""Tests for entity.py — shared DeviceInfo."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from lymow.entity import async_prune_stale_zone_entities, lymow_device_info

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


# ---------------------------------------------------------------------------
# async_prune_stale_zone_entities
# ---------------------------------------------------------------------------


class _FakeRegistry:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}
        self.removed: list[str] = []

    def async_remove(self, entity_id):
        self.removed.append(entity_id)


def _hass_with_registry(entries):
    hass = MagicMock()
    hass._entity_registry = _FakeRegistry(entries)
    return hass


def _entry(unique_id, entity_id, platform="lymow"):
    return SimpleNamespace(unique_id=unique_id, entity_id=entity_id, platform=platform)


def test_prune_removes_only_stale_zone_entities() -> None:
    entries = [
        _entry(f"{THING}_kx1k_cut_height", "number.kx1k_ch"),  # valid
        _entry(f"{THING}_kx1k_enabled", "switch.kx1k"),  # valid
        _entry(f"{THING}_dead1_cut_height", "number.dead1_ch"),  # stale → remove
        _entry(f"{THING}_dead1_enabled", "switch.dead1"),  # stale → remove
        _entry(f"{THING}_audio_volume", "number.volume"),  # device-level, not a zone
        _entry("other-thing_dead1_enabled", "switch.other", platform="lymow"),  # different thing
        _entry(f"{THING}_dead1_enabled", "switch.foreign", platform="hue"),  # foreign platform
    ]
    hass = _hass_with_registry(entries)
    async_prune_stale_zone_entities(hass, THING, {"kx1k"})
    assert sorted(hass._entity_registry.removed) == ["number.dead1_ch", "switch.dead1"]


def test_prune_noop_when_no_valid_zones() -> None:
    # Empty valid set means the map isn't loaded yet — never prune (would wipe all).
    hass = _hass_with_registry([_entry(f"{THING}_dead1_enabled", "switch.dead1")])
    async_prune_stale_zone_entities(hass, THING, set())
    assert hass._entity_registry.removed == []
