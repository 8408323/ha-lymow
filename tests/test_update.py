"""Tests for update.py — LymowFirmwareUpdate entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lymow.const import DOMAIN
from lymow.update import LymowFirmwareUpdate, async_setup_entry

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: state or {}}
    coord.async_check_firmware_update = AsyncMock()
    coord.async_install_firmware_update = AsyncMock()
    return coord


def test_metadata() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_firmware_update"
    assert "Firmware" in e._attr_name
    assert "Mower 1" in e._attr_name


def test_installed_version_from_software_version() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.installed_version == "12.0.0.125"


def test_latest_version_falls_back_to_installed() -> None:
    """Without a check-update call, latest mirrors installed so HA doesn't
    misleadingly show "update available"."""
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.latest_version == "12.0.0.125"


def test_latest_version_uses_coordinator_value() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125", "latestVersion": "12.0.0.130"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.latest_version == "12.0.0.130"


def test_in_progress_true_when_job_id_present() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125", "otaJobId": "JOB-1"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.in_progress is True


def test_in_progress_false_when_no_job_id() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.in_progress is False


async def test_async_update_calls_coordinator_check() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_update()
    coord.async_check_firmware_update.assert_awaited_once_with(THING)


async def test_async_update_swallows_errors() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    coord.async_check_firmware_update.side_effect = RuntimeError("network")
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_update()  # must not raise


async def test_async_install_uses_object_key_from_data() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125", "otaObjectKey": "firmware/x.bin"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_install(version=None, backup=False)
    coord.async_install_firmware_update.assert_awaited_once_with(THING, "firmware/x.bin")


async def test_async_install_falls_back_to_version_arg() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_install(version="firmware/12.0.0.130.bin", backup=False)
    coord.async_install_firmware_update.assert_awaited_once_with(THING, "firmware/12.0.0.130.bin")


async def test_async_install_noop_when_no_object_key() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_install(version=None, backup=False)
    coord.async_install_firmware_update.assert_not_awaited()


async def test_async_setup_entry_creates_one_entity_per_device() -> None:
    coord = _make_coord({"softwareVersion": "12.0.0.125"})

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], LymowFirmwareUpdate)


async def test_async_setup_entry_no_devices() -> None:
    coord = _make_coord()
    coord.devices = []

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    assert added == []


def test_device_data_empty_when_no_data() -> None:
    coord = _make_coord()
    coord.data = None
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e._device_data == {}
