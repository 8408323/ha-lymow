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


async def test_async_install_uses_prefix_plus_latest_version() -> None:
    coord = _make_coord({"softwareVersion": "v2.1.43", "latestVersion": "v2.1.48_20260518", "otaPrefix": ""})
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_install(version=None, backup=False)
    coord.async_install_firmware_update.assert_awaited_once_with(THING, "v2.1.48_20260518")


async def test_async_install_concatenates_non_empty_prefix() -> None:
    coord = _make_coord(
        {
            "softwareVersion": "v2.1.43",
            "latestVersion": "v2.1.48",
            "otaPrefix": "firmware/",
        }
    )
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_install(version=None, backup=False)
    coord.async_install_firmware_update.assert_awaited_once_with(THING, "firmware/v2.1.48")


async def test_async_install_ignores_version_arg() -> None:
    """The HA `version` arg is a version string, not the objectKey we need —
    so it must be ignored. The install must build objectKey from cached fields."""
    coord = _make_coord({"softwareVersion": "v2.1.43", "latestVersion": "v2.1.48", "otaPrefix": "fw/"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    await e.async_install(version="something-else", backup=False)
    coord.async_install_firmware_update.assert_awaited_once_with(THING, "fw/v2.1.48")


async def test_async_install_raises_when_no_latest_version_cached() -> None:
    """Refuse to install if no check_update has populated latestVersion yet —
    using HA's `version` string as the objectKey would start an invalid OTA."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    coord = _make_coord({"softwareVersion": "v2.1.43"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    with pytest.raises(HomeAssistantError):
        await e.async_install(version="anything", backup=False)
    coord.async_install_firmware_update.assert_not_awaited()


def test_release_summary_converts_escaped_newlines() -> None:
    coord = _make_coord({"otaReleaseNote": "first line\\nsecond line\\nthird"})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.release_summary == "first line\nsecond line\nthird"


def test_release_summary_none_when_missing() -> None:
    coord = _make_coord({})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.release_summary is None


def test_release_summary_none_when_not_a_string() -> None:
    coord = _make_coord({"otaReleaseNote": 123})
    e = LymowFirmwareUpdate(coord, DEVICE)
    assert e.release_summary is None


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
