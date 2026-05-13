"""Tests for LymowCoordinator: MQTT state merge and command dispatch."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Minimal stubs so coordinator.py can import without the HA stack
# ---------------------------------------------------------------------------
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_ha_stubs() -> None:
    """Create just enough HA module stubs to import coordinator."""
    # homeassistant.core
    ha = types.ModuleType("homeassistant")
    ha_core = types.ModuleType("homeassistant.core")
    ha_core.HomeAssistant = object
    ha.core = ha_core
    sys.modules.setdefault("homeassistant", ha)
    sys.modules.setdefault("homeassistant.core", ha_core)

    # homeassistant.helpers
    ha_helpers = types.ModuleType("homeassistant.helpers")
    ha.helpers = ha_helpers
    sys.modules.setdefault("homeassistant.helpers", ha_helpers)

    # homeassistant.helpers.update_coordinator
    class _UpdateFailed(Exception):
        pass

    class _DataUpdateCoordinator:
        def __init__(self, hass, logger, *, name, update_interval):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: dict | None = None
            self._listeners: list = []

        def __class_getitem__(cls, _item):
            return cls

        async def async_shutdown(self):
            pass

        def async_set_updated_data(self, data):
            self.data = data

    ha_coord = types.ModuleType("homeassistant.helpers.update_coordinator")
    ha_coord.DataUpdateCoordinator = _DataUpdateCoordinator
    ha_coord.UpdateFailed = _UpdateFailed
    sys.modules.setdefault("homeassistant.helpers.update_coordinator", ha_coord)


_make_ha_stubs()

# Now we can import coordinator (const, protocol, mqtt already loaded by conftest)
import importlib.util  # noqa: E402
import os  # noqa: E402

_BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")


def _load(name: str) -> None:
    if f"lymow.{name}" in sys.modules:
        return
    path = os.path.join(_BASE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"lymow.{name}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"lymow.{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_load("coordinator")

from lymow.const import (  # noqa: E402
    USER_CTRL_CLEAN,
    USER_CTRL_PAUSE,
    USER_CTRL_PAUSE_DOCK,
    USER_CTRL_RECHARGE_DOCK,
    USER_CTRL_RESUME,
    USER_CTRL_RESUME_DOCK,
    WORK_STATUS_DOCKING,
    WORK_STATUS_PAUSE_DOCKING,
)
from lymow.coordinator import LymowCoordinator  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Test Mower"}


def _make_coordinator(
    devices: list[dict] | None = None,
    rest_data: dict[str, Any] | None = None,
) -> tuple[LymowCoordinator, MagicMock, AsyncMock]:
    """Return (coordinator, mqtt_mock, api_mock)."""
    mqtt = MagicMock()
    mqtt.disconnect = AsyncMock()
    mqtt.async_publish_command = AsyncMock()

    api = MagicMock()
    api.get_device_info = AsyncMock(return_value=rest_data or {"workStatus": 5, "battery": 100})

    coord = LymowCoordinator(
        hass=MagicMock(),
        client=api,
        mqtt_client=mqtt,
        devices=devices or [DEVICE],
    )
    return coord, mqtt, api


# ---------------------------------------------------------------------------
# REST polling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_data_returns_rest_data() -> None:
    coord, _, api = _make_coordinator(rest_data={"workStatus": 2, "battery": 80})
    result = await coord._async_update_data()
    assert result[THING]["workStatus"] == 2
    assert result[THING]["battery"] == 80
    api.get_device_info.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_async_update_data_merges_mqtt_state() -> None:
    coord, _, _ = _make_coordinator(rest_data={"workStatus": 5, "battery": 100})
    # Pre-load some MQTT state
    coord._mqtt_state[THING] = {"battery": 77, "isCharging": True}
    result = await coord._async_update_data()
    # MQTT values override REST
    assert result[THING]["battery"] == 77
    assert result[THING]["isCharging"] is True
    # REST-only keys survive
    assert result[THING]["workStatus"] == 5


@pytest.mark.asyncio
async def test_async_update_data_raises_update_failed_on_exception() -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coord, _, api = _make_coordinator()
    api.get_device_info.side_effect = RuntimeError("network error")
    with pytest.raises(UpdateFailed, match="network error"):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_async_update_data_multiple_devices() -> None:
    devices = [
        {"deviceThingName": "mower-001"},
        {"deviceThingName": "mower-002"},
    ]
    mqtt = MagicMock()
    mqtt.disconnect = AsyncMock()
    mqtt.async_publish_command = AsyncMock()

    api = MagicMock()
    api.get_device_info = AsyncMock(side_effect=lambda thing: {"thing": thing, "battery": 50})

    coord = LymowCoordinator(hass=MagicMock(), client=api, mqtt_client=mqtt, devices=devices)
    result = await coord._async_update_data()
    assert "mower-001" in result
    assert "mower-002" in result
    assert result["mower-001"]["thing"] == "mower-001"
    assert result["mower-002"]["thing"] == "mower-002"


# ---------------------------------------------------------------------------
# MQTT state callback
# ---------------------------------------------------------------------------


def test_on_mqtt_state_accumulates() -> None:
    coord, _, _ = _make_coordinator()
    coord.on_mqtt_state(THING, {"battery": 55})
    assert coord._mqtt_state[THING]["battery"] == 55

    coord.on_mqtt_state(THING, {"workStatus": 3})
    assert coord._mqtt_state[THING]["workStatus"] == 3
    assert coord._mqtt_state[THING]["battery"] == 55  # still there


def test_on_mqtt_state_pushes_merged_data_when_coordinator_has_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2, "battery": 90}}

    coord.on_mqtt_state(THING, {"battery": 50})

    # coord.data updated with merged result
    assert coord.data[THING]["battery"] == 50
    assert coord.data[THING]["workStatus"] == 2  # REST field preserved


def test_on_mqtt_state_no_push_when_no_coordinator_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = None
    # Should not raise
    coord.on_mqtt_state(THING, {"battery": 50})
    assert coord._mqtt_state[THING]["battery"] == 50


def test_on_mqtt_state_unknown_thing_ignored() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"battery": 90}}
    # A different device not in coord.data shouldn't blow up
    coord.on_mqtt_state("other-mower", {"battery": 10})
    # Original data untouched
    assert coord.data[THING]["battery"] == 90


# ---------------------------------------------------------------------------
# MQTT online callback
# ---------------------------------------------------------------------------


def test_on_mqtt_online_sets_is_online_true() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    coord.on_mqtt_online(THING, True)
    assert coord.data[THING]["isOnline"] is True
    assert coord.data[THING]["deviceState"] == "online"


def test_on_mqtt_online_sets_is_online_false() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    coord.on_mqtt_online(THING, False)
    assert coord.data[THING]["isOnline"] is False
    assert coord.data[THING]["deviceState"] == "offline"


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_start_mowing_sends_clean_command() -> None:
    from lymow.protocol import _decode_fields

    coord, mqtt, _ = _make_coordinator()
    await coord.async_start_mowing(THING)

    mqtt.async_publish_command.assert_awaited_once()
    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_CLEAN


@pytest.mark.asyncio
async def test_async_pause_sends_pause_when_not_docking() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2}}  # mowing
    await coord.async_pause(THING)

    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    from lymow.protocol import _decode_fields

    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_PAUSE


@pytest.mark.asyncio
async def test_async_pause_sends_pause_dock_when_docking() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": WORK_STATUS_DOCKING}}
    await coord.async_pause(THING)

    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    from lymow.protocol import _decode_fields

    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_PAUSE_DOCK


@pytest.mark.asyncio
async def test_async_dock_sends_resume_dock_when_pause_docking() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": WORK_STATUS_PAUSE_DOCKING}}
    await coord.async_dock(THING)

    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    from lymow.protocol import _decode_fields

    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_RESUME_DOCK


@pytest.mark.asyncio
async def test_async_dock_sends_recharge_dock_when_not_pause_docking() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2}}  # mowing
    await coord.async_dock(THING)

    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    from lymow.protocol import _decode_fields

    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_RECHARGE_DOCK


@pytest.mark.asyncio
async def test_async_resume_sends_resume_dock_when_pause_docking() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": WORK_STATUS_PAUSE_DOCKING}}
    await coord.async_resume(THING)

    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    from lymow.protocol import _decode_fields

    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_RESUME_DOCK


@pytest.mark.asyncio
async def test_async_resume_sends_resume_when_paused() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 3}}  # paused
    await coord.async_resume(THING)

    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    from lymow.protocol import _decode_fields

    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field[5] == USER_CTRL_RESUME


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_shutdown_disconnects_mqtt() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_shutdown()
    mqtt.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_current_work_status_returns_minus_one_when_no_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = None
    assert coord._current_work_status(THING) == -1


@pytest.mark.asyncio
async def test_current_work_status_returns_value_from_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 4}}
    assert coord._current_work_status(THING) == 4
