"""End-to-end setup/unload through the real Home Assistant stack via PHCC."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

# Skip the whole file if PHCC + real HA aren't available. The regular `pytest
# tests/` invocation in the main env does NOT have these — and that's fine,
# the rest of the suite covers the unit-level behavior.
pytest.importorskip(
    "pytest_homeassistant_custom_component",
    reason="pytest-homeassistant-custom-component not installed; run under uv "
    "run --isolated --with pytest-homeassistant-custom-component",
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    MockModule,
    mock_integration,
)

# The conftest.py at tests/ has its own importlib loader that creates a parallel
# ``lymow.*`` module tree for the unit tests. That conflicts with HA's
# ``custom_components.lymow.*`` discovery: both ends up running module-level
# init code, and entity classes from one tree don't match isinstance() checks
# against the other. Strip the parallel tree before HA discovers the integration
# so there's only one canonical set of module objects in sys.modules.
for _mod in list(sys.modules):
    if _mod == "lymow" or _mod.startswith("lymow."):
        del sys.modules[_mod]

# Force-import the integration as a submodule of ``custom_components`` so that
# ``patch("custom_components.lymow.X")`` can resolve via getattr — mock.patch
# uses pkgutil which doesn't trigger lazy submodule discovery.
__import__("custom_components.lymow")


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):  # noqa: ARG001
    """Make HA discover custom_components/lymow without pip-installing requirements."""
    yield


@pytest.fixture(autouse=True)
def _stub_heavy_ha_dependencies(hass):
    """Stub bluetooth/ffmpeg manifest deps to avoid pulling BLE/camera transitives."""
    for stub_domain in ("bluetooth", "ffmpeg"):
        mock_integration(hass, MockModule(stub_domain))
    # Pre-set the "www static path registered" key so async_setup_entry skips
    # the hass.http.async_register_static_paths call — http isn't loaded in
    # this minimal env and the static-path side effect doesn't affect the
    # state transitions / entity creation we're verifying.
    hass.data["lymow_www_registered"] = True
    # Same for the dashboard auto-create — depends on the lovelace component
    # which isn't loaded here.
    hass.data["lymow_dashboard_created"] = True


@pytest.fixture
def _patched_lymow_boundaries():
    """Mock Cognito/REST/MQTT boundaries; patch the custom_components.lymow bound names."""
    auth_inst = MagicMock()
    auth_inst.login = AsyncMock(return_value={"AccessToken": "tok", "IdToken": "id-tok", "region": "eu-west-1"})
    auth_inst.login_region = AsyncMock(return_value={"AccessToken": "tok", "IdToken": "id-tok", "region": "eu-west-1"})
    auth_inst.get_aws_credentials = AsyncMock(
        return_value={
            "identity_id": "eu-west-1:fake-identity",
            "credentials": {
                "AccessKeyId": "AKIA-fake",
                "SecretKey": "secret-fake",
                "SessionToken": "session-fake",
            },
        }
    )

    api_inst = MagicMock()
    api_inst.get_devices = AsyncMock(
        return_value=[{"deviceThingName": "thing-test-1", "deviceName": "Test Mower", "sn": "SN-TEST"}]
    )
    api_inst.get_device_info = AsyncMock(return_value={"workStatus": 5, "battery": 80})
    api_inst.get_device_feature = AsyncMock(return_value={})
    api_inst.get_clean_history = AsyncMock(return_value={"clean_history": [], "total_records": 0})
    api_inst.get_backup_map_list = AsyncMock(return_value=[])
    api_inst.check_update = AsyncMock(return_value={})

    mqtt_inst = MagicMock()
    mqtt_inst.connect = AsyncMock()
    mqtt_inst.disconnect = AsyncMock()
    mqtt_inst.async_publish_command = AsyncMock()

    with (
        patch("custom_components.lymow.LymowAuth", return_value=auth_inst),
        patch("custom_components.lymow.LymowApiClient", return_value=api_inst),
        patch("custom_components.lymow.LymowMqttClient", return_value=mqtt_inst),
    ):
        yield {"auth": auth_inst, "api": api_inst, "mqtt": mqtt_inst}


def _make_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain="lymow",
        title="Test Mower",
        data={
            "username": "test@example.com",
            "password": "fake-password",
            "region": "eu-west-1",
        },
        unique_id="test@example.com",
    )
    entry.add_to_hass(hass)
    return entry


# ---------------------------------------------------------------------------
# Setup / unload state transitions
# ---------------------------------------------------------------------------


async def test_async_setup_entry_transitions_entry_to_loaded(hass, _patched_lymow_boundaries):
    """async_setup_entry must drive entry to LOADED via real config-entries machinery."""
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED


async def test_async_unload_entry_transitions_entry_to_not_loaded(hass, _patched_lymow_boundaries):
    """async_unload_entry releases platforms + MQTT and leaves entry NOT_LOADED."""
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.NOT_LOADED
    # MQTT disconnect was called as part of teardown (real coordinator owns it).
    assert _patched_lymow_boundaries["mqtt"].disconnect.await_count >= 1


async def test_full_reload_cycle_succeeds(hass, _patched_lymow_boundaries):
    """Setup → unload → setup must succeed (catches listener leaks, dupe IDs, dup tasks)."""
    entry = _make_entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED


# ---------------------------------------------------------------------------
# Entity / device registry validation — catches unique-ID collisions
# ---------------------------------------------------------------------------


async def test_entities_appear_in_entity_registry_after_setup(hass, _patched_lymow_boundaries):
    """Entity registry must hold core entities — catches cross-platform unique_id drops."""
    from homeassistant.helpers import entity_registry as er

    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    # All entries owned by this config entry.
    entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert entries, "no entities registered for the config entry"

    # The integration should produce at least one lawn_mower + one sensor for
    # the device. Anything less means a platform failed to forward.
    domains = {e.domain for e in entries}
    assert "lawn_mower" in domains
    assert "sensor" in domains

    # Cross-platform unique_id sanity: HA's registry already enforces (platform,
    # unique_id) tuples to be unique, but assert nothing was silently dropped
    # by checking we got at least the canonical lymow mower entity.
    mower_entries = [e for e in entries if e.domain == "lawn_mower"]
    assert len(mower_entries) == 1
    assert mower_entries[0].unique_id  # not empty


async def test_device_is_registered_with_correct_identifier(hass, _patched_lymow_boundaries):
    """Device registered with thing-name identifier so user sees one card per mower."""
    from homeassistant.helpers import device_registry as dr

    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    dev_reg = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
    assert len(devices) == 1
    device = devices[0]
    # Identifier must match the thing-name we returned from the mocked API.
    identifiers = device.identifiers
    assert ("lymow", "thing-test-1") in identifiers, f"thing-name not in device identifiers: {identifiers}"


# ---------------------------------------------------------------------------
# Service registration through HA's service registry
# ---------------------------------------------------------------------------


async def test_services_are_registered_with_lymow_domain(hass, _patched_lymow_boundaries):
    """Custom lymow.* services must surface in real hass.services (not just mocks)."""
    entry = _make_entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    services = hass.services.async_services()
    lymow_services = services.get("lymow", {})
    # Pick a representative sample. If even one of these is missing, something
    # broke the dispatcher.
    for expected in (
        "delete_zone",
        "start_zone",
        "pin_and_go",
        "set_recharge_resume",
        "set_device_settings",
        "ble_drive",
        "rename_zone",
        "set_schedules",
    ):
        assert expected in lymow_services, f"service lymow.{expected} not registered"
