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

    # homeassistant.exceptions (needed by async_update_zone_* at call time)
    class _HomeAssistantError(Exception):
        pass

    ha_exc = types.ModuleType("homeassistant.exceptions")
    ha_exc.HomeAssistantError = _HomeAssistantError
    sys.modules.setdefault("homeassistant.exceptions", ha_exc)


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
    api.get_clean_history = AsyncMock(return_value=[])
    api.get_device_feature = AsyncMock(return_value={})
    api.update_device_feature = AsyncMock(return_value={})

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
    api.get_clean_history = AsyncMock(return_value=[])
    api.get_device_feature = AsyncMock(return_value={})

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


# ---------------------------------------------------------------------------
# Query commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_query_map_publishes_correct_command() -> None:
    from lymow.const import USER_CTRL_QUERY_MAP
    from lymow.protocol import _decode_fields

    coord, mqtt, _ = _make_coordinator()
    await coord.async_query_map(THING)

    assert mqtt.async_publish_command.await_count == 1
    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    # field 5 = userCtrl = QUERY_MAP = 19
    assert by_field.get(5) == USER_CTRL_QUERY_MAP


@pytest.mark.asyncio
async def test_async_query_all_maps_sends_one_command_per_device() -> None:
    devices = [
        {"deviceThingName": "mower-001", "deviceName": "A"},
        {"deviceThingName": "mower-002", "deviceName": "B"},
    ]
    coord, mqtt, api = _make_coordinator(devices=devices)
    api.get_device_info = AsyncMock(return_value={"workStatus": 5, "battery": 100})
    await coord._async_update_data()  # initialise data
    await coord.async_query_all_maps()
    assert mqtt.async_publish_command.await_count == 2
    called_things = [c[0][0] for c in mqtt.async_publish_command.call_args_list]
    assert "mower-001" in called_things
    assert "mower-002" in called_things


@pytest.mark.asyncio
async def test_async_query_schedules_publishes_correct_command() -> None:
    from lymow.const import USER_CTRL_QUERY_SCHEDULES
    from lymow.protocol import _decode_fields

    coord, mqtt, _ = _make_coordinator()
    await coord.async_query_schedules(THING)

    assert mqtt.async_publish_command.await_count == 1
    _, pb_bytes = mqtt.async_publish_command.call_args[0]
    fields = _decode_fields(pb_bytes)
    by_field = {fn: val for fn, _wt, val in fields}
    assert by_field.get(5) == USER_CTRL_QUERY_SCHEDULES


# ---------------------------------------------------------------------------
# Work status transition notifications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_work_status_transition_fires_event_bus() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}  # docked

    # Seed the previous work status so the transition is 5 → 1
    coord._prev_work_status[THING] = 5

    # Transition to mowing
    coord.on_mqtt_state(THING, {"workStatus": 1})

    coord.hass.bus.async_fire.assert_called()
    call_args = coord.hass.bus.async_fire.call_args[0]
    assert call_args[0] == "lymow_work_status_changed"
    payload = call_args[1]
    assert payload["work_status"] == 1
    assert payload["prev_work_status"] == 5


@pytest.mark.asyncio
async def test_work_status_error_transition_fires_notification() -> None:
    from lymow.const import WORK_STATUS_ERROR_GROUP

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 1}}  # mowing

    error_status = next(iter(WORK_STATUS_ERROR_GROUP))
    coord.on_mqtt_state(THING, {"workStatus": error_status})

    coord.hass.components.persistent_notification.async_create.assert_called()


@pytest.mark.asyncio
async def test_work_status_mow_complete_fires_notification() -> None:
    from lymow.const import WORK_STATUS_DOCKED_GROUP, WORK_STATUS_MOWING_GROUP

    coord, _, _ = _make_coordinator()
    mow_status = next(iter(WORK_STATUS_MOWING_GROUP))
    docked_status = next(iter(WORK_STATUS_DOCKED_GROUP))
    coord.data = {THING: {"workStatus": mow_status}}
    coord._prev_work_status[THING] = mow_status

    coord.on_mqtt_state(THING, {"workStatus": docked_status})

    coord.hass.components.persistent_notification.async_create.assert_called()


@pytest.mark.asyncio
async def test_on_mqtt_offline_fires_persistent_notification() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "isOnline": True}}

    coord.on_mqtt_online(THING, False)

    coord.hass.components.persistent_notification.async_create.assert_called()
    call_kwargs = coord.hass.components.persistent_notification.async_create.call_args[1]
    assert "offline" in call_kwargs.get("title", "").lower()


# ---------------------------------------------------------------------------
# Zone update commands — async_update_zone_cut_height
# ---------------------------------------------------------------------------

_SAMPLE_MAP_DATA = {
    "goZones": [
        {"hashId": "zone0001", "cutHeight": 40, "area": 349, "isEnabled": True, "polygon": []},
        {"hashId": "zone0002", "cutHeight": 50, "area": 100, "isEnabled": True, "polygon": []},
    ],
    "nogoZones": [],
}


@pytest.mark.asyncio
async def test_update_zone_cut_height_publishes_sync_map() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "mapData": _SAMPLE_MAP_DATA}}

    await coord.async_update_zone_cut_height(THING, "zone0001", 60)

    assert mqtt.async_publish_command.await_count == 1
    thing, _ = mqtt.async_publish_command.call_args[0]
    assert thing == THING


@pytest.mark.asyncio
async def test_update_zone_cut_height_raises_when_no_map_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}  # no mapData

    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await coord.async_update_zone_cut_height(THING, "zone0001", 60)


@pytest.mark.asyncio
async def test_update_zone_cut_height_only_modifies_target_zone() -> None:
    """The other zone's cutHeight must not change."""
    import copy

    coord, mqtt, _ = _make_coordinator()
    orig = copy.deepcopy(_SAMPLE_MAP_DATA)
    coord.data = {THING: {"workStatus": 5, "mapData": orig}}

    await coord.async_update_zone_cut_height(THING, "zone0001", 75)

    # zone0001 updated; zone0002 unchanged — verify the deep-copy didn't mutate original
    assert orig["goZones"][0]["cutHeight"] == 40  # original not mutated


# ---------------------------------------------------------------------------
# Zone update commands — async_update_zone_enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_zone_enabled_publishes_sync_map() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "mapData": _SAMPLE_MAP_DATA}}

    await coord.async_update_zone_enabled(THING, "zone0001", False)

    assert mqtt.async_publish_command.await_count == 1
    thing, _ = mqtt.async_publish_command.call_args[0]
    assert thing == THING


@pytest.mark.asyncio
async def test_update_zone_enabled_also_updates_child_nogo_zones() -> None:
    coord, mqtt, _ = _make_coordinator()
    nogo_map = {
        "goZones": [{"hashId": "zone0001", "isEnabled": True, "polygon": []}],
        "nogoZones": [
            {"hashId": "nogo0001", "parentZoneHashId": "zone0001", "isEnabled": True, "polygon": []},
            {"hashId": "nogo0002", "parentZoneHashId": "zone0002", "isEnabled": True, "polygon": []},
        ],
    }
    coord.data = {THING: {"workStatus": 5, "mapData": nogo_map}}

    # Spy on async_sync_map to capture what map_data was passed
    sent_maps: list[dict] = []

    async def _capture_sync(thing_name: str, map_data: dict) -> None:  # type: ignore[override]
        sent_maps.append(map_data)

    coord.async_sync_map = _capture_sync  # type: ignore[method-assign]

    await coord.async_update_zone_enabled(THING, "zone0001", False)

    assert len(sent_maps) == 1
    updated = sent_maps[0]
    # go-zone disabled
    assert updated["goZones"][0]["isEnabled"] is False
    # nogo child of zone0001 disabled
    assert updated["nogoZones"][0]["isEnabled"] is False
    # nogo child of zone0002 unchanged
    assert updated["nogoZones"][1]["isEnabled"] is True


@pytest.mark.asyncio
async def test_update_zone_enabled_raises_when_no_map_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}

    from homeassistant.exceptions import HomeAssistantError

    with pytest.raises(HomeAssistantError):
        await coord.async_update_zone_enabled(THING, "zone0001", False)


# ---------------------------------------------------------------------------
# Zone commands — async_delete_zone / async_start_zones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_delete_zone_publishes_command() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_delete_zone(THING, "zone0001")

    assert mqtt.async_publish_command.await_count == 1
    thing, _ = mqtt.async_publish_command.call_args[0]
    assert thing == THING


@pytest.mark.asyncio
async def test_async_start_zones_publishes_command() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_start_zones(THING, ["zone0001", "zone0002"])

    assert mqtt.async_publish_command.await_count == 1
    thing, _ = mqtt.async_publish_command.call_args[0]
    assert thing == THING


# ---------------------------------------------------------------------------
# Clean history merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_last_clean_merges_real_shape() -> None:
    """Validated against a real eu-west-1 capture 2026-05-19."""
    from datetime import UTC, datetime

    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {
        "clean_history": [
            {
                "clean_area": 345,
                "clean_time": 60,
                "date": 1779184292,
                "percent": 1,
                "used_battery": 49,
            },
            {"clean_area": 1108, "clean_time": 229, "date": 1779020649, "percent": 0.5, "used_battery": 30},
        ],
        "page": 0,
        "has_more": False,
        "total_records": 14,
        "clean_summary": {"total_clean_time": 829, "total_clean_area": 4243},
    }
    result = await coord._async_update_data()
    assert result[THING]["lastCleanAreaM2"] == 345
    assert result[THING]["lastCleanDurationS"] == 60
    assert result[THING]["lastCleanAt"] == datetime.fromtimestamp(1779184292, tz=UTC)
    assert result[THING]["lastCleanPercent"] == 100.0
    assert result[THING]["lastCleanBatteryUsed"] == 49
    assert result[THING]["cleanHistoryCount"] == 14  # cumulative, from total_records
    assert result[THING]["totalCleanTimeS"] == 829
    assert result[THING]["totalCleanHistoryAreaM2"] == 4243


@pytest.mark.asyncio
async def test_fetch_last_clean_uses_page_zero_and_pagesize_15() -> None:
    """App was observed to call ?page=0&pageSize=15."""
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {"clean_history": []}
    await coord._async_update_data()
    api.get_clean_history.assert_awaited_with(THING, page=0, page_size=15)


@pytest.mark.asyncio
async def test_fetch_last_clean_empty_returns_zero_count() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {"clean_history": []}
    result = await coord._async_update_data()
    assert result[THING]["cleanHistoryCount"] == 0
    assert "lastCleanAt" not in result[THING]


@pytest.mark.asyncio
async def test_fetch_last_clean_swallows_errors() -> None:
    coord, _, api = _make_coordinator(rest_data={"workStatus": 5})
    api.get_clean_history.side_effect = RuntimeError("403")
    result = await coord._async_update_data()
    assert result[THING]["workStatus"] == 5  # device-info still merged
    assert "lastCleanAt" not in result[THING]


@pytest.mark.asyncio
async def test_fetch_last_clean_ignores_non_dict_response() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = "not-a-dict"
    result = await coord._async_update_data()
    assert "lastCleanAt" not in result[THING]


@pytest.mark.asyncio
async def test_fetch_last_clean_ignores_dict_without_clean_history_key() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {"some_other_key": [1, 2]}
    result = await coord._async_update_data()
    assert "lastCleanAt" not in result[THING]
    assert "cleanHistoryCount" not in result[THING]


@pytest.mark.asyncio
async def test_fetch_last_clean_handles_bad_epoch() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {"clean_history": [{"clean_area": 10, "clean_time": 60, "date": "not-an-int"}]}
    result = await coord._async_update_data()
    # Other fields still extracted; bad date silently dropped
    assert result[THING]["lastCleanAreaM2"] == 10
    assert "lastCleanAt" not in result[THING]


# ---------------------------------------------------------------------------
# Static device-list-query fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_static_device_fields_merged_into_update() -> None:
    devices = [
        {
            "deviceThingName": THING,
            "sn": "LR011A09A17B6521",
            "deviceType": "Lymow one",
            "deviceBluetooth": "Lymow_7B6521",
            "simId": " 89320420000094505458",
            "fwMinVersion": "v2.1.43",
            "createdAt": "2026-05-06T16:33:39.243Z",
            "deviceLocked": False,
        }
    ]
    coord, _, _ = _make_coordinator(devices=devices)
    result = await coord._async_update_data()
    assert result[THING]["serialNumber"] == "LR011A09A17B6521"
    assert result[THING]["deviceType"] == "Lymow one"
    assert result[THING]["deviceBluetooth"] == "Lymow_7B6521"
    assert result[THING]["simId"] == "89320420000094505458"
    assert result[THING]["fwMinVersion"] == "v2.1.43"
    assert result[THING]["deviceLocked"] is False
    from datetime import datetime, timezone

    expected = datetime(2026, 5, 6, 16, 33, 39, 243000, tzinfo=timezone.utc)
    assert result[THING]["createdAt"] == expected


@pytest.mark.asyncio
async def test_static_device_fields_skipped_when_missing() -> None:
    devices = [{"deviceThingName": THING}]
    coord, _, _ = _make_coordinator(devices=devices)
    result = await coord._async_update_data()
    for absent in ("serialNumber", "deviceType", "deviceBluetooth", "simId", "fwMinVersion", "createdAt"):
        assert absent not in result[THING]


@pytest.mark.asyncio
async def test_static_device_fields_skips_empty_string() -> None:
    devices = [{"deviceThingName": THING, "deviceBluetooth": "   ", "simId": ""}]
    coord, _, _ = _make_coordinator(devices=devices)
    result = await coord._async_update_data()
    assert "deviceBluetooth" not in result[THING]
    assert "simId" not in result[THING]


@pytest.mark.asyncio
async def test_static_device_fields_invalid_created_at_ignored() -> None:
    devices = [{"deviceThingName": THING, "createdAt": "not-an-iso-date"}]
    coord, _, _ = _make_coordinator(devices=devices)
    result = await coord._async_update_data()
    assert "createdAt" not in result[THING]


@pytest.mark.asyncio
async def test_static_fields_do_not_override_live_state() -> None:
    """REST get_device_info and MQTT win over the static merge."""
    devices = [{"deviceThingName": THING, "deviceType": "static-type"}]
    coord, _, _ = _make_coordinator(devices=devices, rest_data={"deviceType": "fresh", "battery": 50})
    coord._mqtt_state[THING] = {"battery": 90}
    result = await coord._async_update_data()
    assert result[THING]["deviceType"] == "fresh"
    assert result[THING]["battery"] == 90


@pytest.mark.asyncio
async def test_fetch_last_clean_handles_non_dict_entry() -> None:
    """A malformed API response with non-dict entries must not crash the
    whole coordinator refresh. Aggregates are kept; per-entry fields skipped."""
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {
        "clean_history": ["unexpected string", None],
        "total_records": 7,
        "clean_summary": {"total_clean_time": 100, "total_clean_area": 50},
    }
    result = await coord._async_update_data()
    # Aggregates still surface
    assert result[THING]["cleanHistoryCount"] == 7
    assert result[THING]["totalCleanTimeS"] == 100
    assert result[THING]["totalCleanHistoryAreaM2"] == 50
    # No per-entry fields extracted because entries[0] isn't a dict
    assert "lastCleanAreaM2" not in result[THING]
    assert "lastCleanAt" not in result[THING]


@pytest.mark.asyncio
async def test_fetch_last_clean_forwards_details_fields() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {
        "clean_history": [
            {
                "clean_area": 345,
                "clean_time": 60,
                "date": 1779184292,
                "status_times": [{"status": 4, "duration": 50}, {"status": 5, "duration": 10}],
                "soc_version": "v1.2.3",
                "start_type": 1,
                "error_list": [7, 12],
                "map_total_area": 850.5,
            }
        ],
    }
    result = await coord._async_update_data()
    assert result[THING]["lastCleanStatusTimes"] == [
        {"status": 4, "duration": 50},
        {"status": 5, "duration": 10},
    ]
    assert result[THING]["lastCleanSocVersion"] == "v1.2.3"
    assert result[THING]["lastCleanStartType"] == 1
    assert result[THING]["lastCleanErrorList"] == [7, 12]
    assert result[THING]["lastCleanMapTotalAreaM2"] == 850.5


@pytest.mark.asyncio
async def test_fetch_last_clean_skips_details_when_missing() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {
        "clean_history": [{"clean_area": 10, "clean_time": 60, "date": 1779184292}],
    }
    result = await coord._async_update_data()
    for absent in (
        "lastCleanStatusTimes",
        "lastCleanSocVersion",
        "lastCleanStartType",
        "lastCleanErrorList",
        "lastCleanMapTotalAreaM2",
    ):
        assert absent not in result[THING]


@pytest.mark.asyncio
async def test_fetch_last_clean_ignores_non_list_status_times() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history.return_value = {
        "clean_history": [
            {
                "clean_area": 10,
                "clean_time": 60,
                "date": 1779184292,
                "status_times": "not-a-list",
                "error_list": "also-not-a-list",
            }
        ],
    }
    result = await coord._async_update_data()
    assert "lastCleanStatusTimes" not in result[THING]
    assert "lastCleanErrorList" not in result[THING]


# ---------------------------------------------------------------------------
# Device feature endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_data_merges_device_feature() -> None:
    coord, _, api = _make_coordinator(rest_data={"workStatus": 5})
    api.get_device_feature.return_value = {"theftDetectionSwitch": True, "findRobotSwitch": False}
    result = await coord._async_update_data()
    assert result[THING]["theftDetectionSwitch"] is True
    assert result[THING]["findRobotSwitch"] is False
    assert result[THING]["workStatus"] == 5


@pytest.mark.asyncio
async def test_async_update_data_swallows_device_feature_error() -> None:
    coord, _, api = _make_coordinator(rest_data={"workStatus": 5})
    api.get_device_feature.side_effect = RuntimeError("403 forbidden")
    result = await coord._async_update_data()
    assert result[THING]["workStatus"] == 5  # device-info still merged
    assert "theftDetectionSwitch" not in result[THING]


@pytest.mark.asyncio
async def test_async_set_device_feature_patches_and_publishes_snapshot() -> None:
    coord, _, api = _make_coordinator()
    original_data = {THING: {"theftDetectionSwitch": False, "battery": 80}}
    coord.data = original_data
    publishes: list[dict] = []
    coord.async_set_updated_data = publishes.append  # type: ignore[method-assign]

    await coord.async_set_device_feature(THING, theftDetectionSwitch=True)

    api.update_device_feature.assert_awaited_once_with(THING, theftDetectionSwitch=True)
    assert len(publishes) == 1
    snapshot = publishes[0]
    # New value is in the published snapshot
    assert snapshot[THING]["theftDetectionSwitch"] is True
    # Unrelated keys preserved
    assert snapshot[THING]["battery"] == 80
    # Per-device dict is a NEW object (not the same reference) — proves no in-place mutation
    assert snapshot[THING] is not original_data[THING]
    # Original snapshot is untouched (immutability invariant)
    assert original_data[THING]["theftDetectionSwitch"] is False


@pytest.mark.asyncio
async def test_async_set_device_feature_no_publish_when_no_data() -> None:
    coord, _, api = _make_coordinator()
    coord.data = None
    publishes: list[dict] = []
    coord.async_set_updated_data = publishes.append  # type: ignore[method-assign]

    await coord.async_set_device_feature(THING, theftLock=True)

    api.update_device_feature.assert_awaited_once_with(THING, theftLock=True)
    assert publishes == []


@pytest.mark.asyncio
async def test_async_start_video_session_delegates_to_client() -> None:
    coord, _, api = _make_coordinator()
    api.start_video_session = AsyncMock(return_value={"channelARN": "arn:test", "region": "eu-west-1"})
    result = await coord.async_start_video_session(THING)
    assert result == {"channelARN": "arn:test", "region": "eu-west-1"}
    api.start_video_session.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_async_set_geofence_radius_resends_full_array() -> None:
    """Mutating the radius must preserve the centre coords + name in the array."""
    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Home", "latitude": 59.0, "longitude": 16.0, "radius": 150},
            ]
        }
    }
    publishes: list = []
    coord.async_set_updated_data = publishes.append  # type: ignore[method-assign]

    await coord.async_set_geofence_radius(THING, 200)

    api.update_device_feature.assert_awaited_once()
    args, kwargs = api.update_device_feature.call_args
    assert args[0] == THING
    sent = kwargs["geoFence"]
    assert len(sent) == 1
    assert sent[0]["radius"] == 200
    assert sent[0]["latitude"] == 59.0
    assert sent[0]["longitude"] == 16.0
    assert sent[0]["name"] == "Home"


@pytest.mark.asyncio
async def test_async_set_geofence_radius_raises_when_no_geofence_set() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, api = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError):
        await coord.async_set_geofence_radius(THING, 200)
    api.update_device_feature.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_set_geofence_radius_raises_when_first_entry_not_dict() -> None:
    """If the API ever returns malformed entries the spread `{**first, ...}`
    would raise TypeError. Surface a controlled HomeAssistantError instead."""
    from homeassistant.exceptions import HomeAssistantError

    coord, _, api = _make_coordinator()
    coord.data = {THING: {"geoFence": ["malformed-string-entry"]}}
    with pytest.raises(HomeAssistantError, match="malformed"):
        await coord.async_set_geofence_radius(THING, 200)
    api.update_device_feature.assert_not_awaited()
