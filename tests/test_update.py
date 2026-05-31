"""Tests for the LymowFirmwareUpdate entity."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import _load_lymow_module

_load_lymow_module("update")

from lymow.update import LymowFirmwareUpdate, async_setup_entry  # noqa: E402

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Test Mower"}


def _make_entity(device_data: dict[str, Any] | None = None) -> LymowFirmwareUpdate:
    coord = MagicMock()
    coord.data = {THING: device_data or {}}
    coord.async_install_firmware_update = AsyncMock(return_value="JOB-42")
    entity = LymowFirmwareUpdate(coord, DEVICE)
    return entity


def test_unique_id_and_name() -> None:
    entity = _make_entity()
    assert entity._attr_unique_id == f"{THING}_firmware_update"
    assert entity._attr_has_entity_name is True
    assert entity._attr_name == "Firmware"
    assert entity._attr_device_info["name"] == "Test Mower"


def test_device_name_falls_back_to_serial_then_thing() -> None:
    entity = LymowFirmwareUpdate(MagicMock(data={THING: {}}), {"deviceThingName": THING, "sn": "SN-99"})
    assert entity._attr_device_info["name"] == "SN-99"
    bare = LymowFirmwareUpdate(MagicMock(data={THING: {}}), {"deviceThingName": THING})
    assert bare._attr_device_info["name"] == THING


def test_installed_version_from_coordinator() -> None:
    entity = _make_entity({"softwareVersion": "v2.1.40"})
    assert entity.installed_version == "v2.1.40"


def test_latest_version_falls_back_to_installed_when_unknown() -> None:
    """Before the first check_update, HA must not show "update available"."""
    entity = _make_entity({"softwareVersion": "v2.1.40"})
    assert entity.latest_version == "v2.1.40"


def test_latest_version_uses_ota_state_when_available() -> None:
    entity = _make_entity({"softwareVersion": "v2.1.40", "latestVersion": "v2.1.48"})
    assert entity.latest_version == "v2.1.48"


def test_latest_version_strips_date_suffix() -> None:
    # The OTA API returns "<base>_<date>"; the date suffix is stripped so HA
    # doesn't show a false update against the installed base version.
    entity = _make_entity({"softwareVersion": "v2.1.48.1", "latestVersion": "v2.1.48.1_20260528"})
    assert entity.latest_version == "v2.1.48.1"


def test_in_progress_flips_with_job_id() -> None:
    entity = _make_entity({"otaJobId": "JOB-42"})
    assert entity.in_progress is True
    entity_idle = _make_entity({"otaJobId": None})
    assert entity_idle.in_progress is False


def test_release_summary_renders_escaped_newlines() -> None:
    entity = _make_entity({"otaReleaseNote": "Fix one.\\nFix two."})
    assert entity.release_summary == "Fix one.\nFix two."


def test_release_summary_handles_missing_or_non_string() -> None:
    assert _make_entity({}).release_summary is None
    assert _make_entity({"otaReleaseNote": 42}).release_summary is None


async def test_async_release_notes_returns_full_formatted_text() -> None:
    """We advertise UpdateEntityFeature.RELEASE_NOTES; without overriding
    async_release_notes the HA frontend shows 'Unknown error' when the
    user opens the entity card. Return the same formatted text so the
    notes modal renders the full release info."""
    entity = _make_entity({"otaReleaseNote": "Fix one.\\nFix two."})
    assert await entity.async_release_notes() == "Fix one.\nFix two."


async def test_async_release_notes_returns_none_when_missing() -> None:
    assert await _make_entity({}).async_release_notes() is None
    assert await _make_entity({"otaReleaseNote": None}).async_release_notes() is None
    assert await _make_entity({"otaReleaseNote": 42}).async_release_notes() is None


def test_device_data_handles_empty_coordinator() -> None:
    """The entity must not raise when coordinator.data is None."""
    entity = _make_entity()
    entity.coordinator.data = None
    assert entity._device_data == {}
    assert entity.installed_version is None
    assert entity.latest_version is None
    assert entity.in_progress is False


@pytest.mark.asyncio
async def test_install_calls_coordinator_with_prefix_plus_version() -> None:
    entity = _make_entity({"latestVersion": "v2.1.48", "otaPrefix": "fw/"})
    await entity.async_install(version=None, backup=False)
    entity.coordinator.async_install_firmware_update.assert_awaited_once_with(THING, "fw/v2.1.48")


@pytest.mark.asyncio
async def test_install_works_with_empty_prefix() -> None:
    entity = _make_entity({"latestVersion": "v2.1.48"})
    await entity.async_install(version=None, backup=False)
    entity.coordinator.async_install_firmware_update.assert_awaited_once_with(THING, "v2.1.48")


@pytest.mark.asyncio
async def test_install_raises_when_no_check_update_cached() -> None:
    from homeassistant.exceptions import HomeAssistantError

    entity = _make_entity({"softwareVersion": "v2.1.40"})
    with pytest.raises(HomeAssistantError, match="No firmware-update info"):
        await entity.async_install(version=None, backup=False)
    entity.coordinator.async_install_firmware_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_setup_entry_adds_one_entity_per_device() -> None:
    devices = [
        {"deviceThingName": "mower-001"},
        {"deviceThingName": "mower-002"},
    ]
    coord = MagicMock()
    coord.devices = devices
    coord.data = {}
    hass = MagicMock()
    hass.data = {"lymow": {"entry-id": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-id"
    added: list = []

    def _add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, _add_entities)
    assert len(added) == 2
    assert all(isinstance(e, LymowFirmwareUpdate) for e in added)


@pytest.mark.asyncio
async def test_async_setup_entry_skips_when_no_devices() -> None:
    coord = MagicMock()
    coord.devices = []
    coord.data = {}
    hass = MagicMock()
    hass.data = {"lymow": {"entry-id": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-id"
    called = False

    def _add_entities(entities):
        nonlocal called
        called = True

    await async_setup_entry(hass, entry, _add_entities)
    assert called is False


# Reference sys so the module-level import remains used after refactors.
assert sys.modules["lymow.update"] is not None
