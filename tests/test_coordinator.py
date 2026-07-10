"""Tests for LymowCoordinator: MQTT state merge and command dispatch."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Minimal stubs so coordinator.py can import without the HA stack
# ---------------------------------------------------------------------------
import asyncio
import sys
import types
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed


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
    api.get_backup_map_list = AsyncMock(return_value=[])
    api.check_update = AsyncMock(return_value={})
    api.create_ota_job = AsyncMock(return_value={})
    api.get_ota_job_summary = AsyncMock(return_value={})

    coord = LymowCoordinator(
        hass=MagicMock(),
        client=api,
        mqtt_client=mqtt,
        devices=devices or [DEVICE],
    )
    return coord, mqtt, api


def _make_task_closer(captured: list | None = None) -> MagicMock:
    """A hass.async_create_task replacement that records and closes scheduled
    coroutines so they don't emit 'never awaited' warnings in tests."""

    def _create(coro):
        if captured is not None:
            captured.append(coro)
        if asyncio.iscoroutine(coro):
            coro.close()

    return MagicMock(side_effect=_create)


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


# ---------------------------------------------------------------------------
# Auth refresh — keep Cognito tokens + AWS creds fresh so polls don't 401
# ---------------------------------------------------------------------------


def _setup_auth(coord, *, refresh_ok=True, login_ok=True):
    auth = MagicMock()
    auth.refresh_tokens = (
        AsyncMock(return_value={"AccessToken": "at2", "IdToken": "it2", "ExpiresIn": 3600})
        if refresh_ok
        else AsyncMock(side_effect=ValueError("refresh token expired"))
    )
    auth.login_region = (
        AsyncMock(return_value={"AccessToken": "at3", "IdToken": "it3", "RefreshToken": "rt3", "ExpiresIn": 3600})
        if login_ok
        else AsyncMock(side_effect=ValueError("bad creds"))
    )
    auth.get_aws_credentials = AsyncMock(
        return_value={
            "credentials": {"AccessKeyId": "ak", "SecretKey": "sk", "SessionToken": "st", "Expiration": 9999999999}
        }
    )
    coord.set_auth_context(
        auth,
        "user",
        "pass",
        "eu-west-1",
        {"AccessToken": "at1", "IdToken": "it1", "RefreshToken": "rt1", "ExpiresIn": 3600},
        {"credentials": {"AccessKeyId": "ak0", "SecretKey": "sk0", "SessionToken": "st0", "Expiration": 9999999999}},
    )
    return auth


@pytest.mark.asyncio
async def test_ensure_auth_noop_when_no_context() -> None:
    coord, _, _ = _make_coordinator()
    await coord._async_ensure_auth()  # _auth is None → silent no-op


@pytest.mark.asyncio
async def test_ensure_auth_noop_when_not_due() -> None:
    coord, _, _ = _make_coordinator()
    auth = _setup_auth(coord)  # expiries ~1h out, margin 10m → not due
    await coord._async_ensure_auth()
    auth.refresh_tokens.assert_not_awaited()
    auth.get_aws_credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_auth_refreshes_token_and_creds_when_token_due() -> None:
    coord, _, api = _make_coordinator()
    auth = _setup_auth(coord)
    coord._token_expiry = datetime.now(UTC)  # due now
    await coord._async_ensure_auth()
    auth.refresh_tokens.assert_awaited_once_with("rt1", "eu-west-1")
    api.update_tokens.assert_called_once_with("at2")
    # New id token forces fresh AWS creds.
    auth.get_aws_credentials.assert_awaited_once_with("it2", "eu-west-1")
    api.update_aws_credentials.assert_called_once_with("ak", "sk", "st")


@pytest.mark.asyncio
async def test_ensure_auth_refreshes_only_creds_when_token_valid() -> None:
    coord, _, api = _make_coordinator()
    auth = _setup_auth(coord)
    coord._aws_creds_expiry = datetime.now(UTC)  # creds due, token still valid
    await coord._async_ensure_auth()
    auth.refresh_tokens.assert_not_awaited()
    auth.get_aws_credentials.assert_awaited_once_with("it1", "eu-west-1")
    api.update_aws_credentials.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_auth_falls_back_to_relogin_when_refresh_fails() -> None:
    coord, _, api = _make_coordinator()
    auth = _setup_auth(coord, refresh_ok=False)
    coord._token_expiry = datetime.now(UTC)
    await coord._async_ensure_auth()
    auth.login_region.assert_awaited_once_with("user", "pass", "eu-west-1")
    api.update_tokens.assert_called_once_with("at3")
    assert coord._refresh_token == "rt3"  # re-login rotates the refresh token


@pytest.mark.asyncio
async def test_ensure_auth_raises_config_entry_auth_failed_when_relogin_fails() -> None:
    coord, _, _ = _make_coordinator()
    _setup_auth(coord, refresh_ok=False, login_ok=False)
    coord._token_expiry = datetime.now(UTC)
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_ensure_auth()


@pytest.mark.asyncio
async def test_update_data_propagates_auth_failed_not_update_failed() -> None:
    from homeassistant.helpers.update_coordinator import UpdateFailed

    coord, _, _ = _make_coordinator(rest_data={"deviceState": "online"})
    _setup_auth(coord, refresh_ok=False, login_ok=False)
    coord._token_expiry = datetime.now(UTC)
    coord.hass.async_create_task = _make_task_closer()
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()
    # Must not be wrapped — UpdateFailed would suppress HA's reauth flow.
    assert not isinstance(ConfigEntryAuthFailed(), UpdateFailed)


def test_set_auth_context_and_expiry_helpers() -> None:
    from datetime import timedelta

    coord, _, _ = _make_coordinator()
    _setup_auth(coord)
    assert coord._refresh_token == "rt1" and coord._id_token == "it1"
    assert coord._region == "eu-west-1"
    # ExpiresIn 3600 → ~1h out
    assert coord._token_expiry > datetime.now(UTC) + timedelta(minutes=50)
    # Expiry parsing variants
    assert coord._expiry_from_expires_in(None) > datetime.now(UTC)
    naive = datetime(2030, 1, 1, 0, 0, 0)
    assert coord._expiry_from_timestamp(naive).tzinfo is not None
    aware = datetime(2030, 1, 1, tzinfo=UTC)
    assert coord._expiry_from_timestamp(aware) == aware
    assert coord._expiry_from_timestamp(1893456000).year == 2030
    assert coord._expiry_from_timestamp(None) <= datetime.now(UTC)


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
    api.get_backup_map_list = AsyncMock(return_value=[])
    api.check_update = AsyncMock(return_value={})
    api.create_ota_job = AsyncMock(return_value={})
    api.get_ota_job_summary = AsyncMock(return_value={})

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


def test_on_mqtt_state_publishes_full_schedule_list() -> None:
    # A QUERY_SCHEDULES reply decodes the full list at once; it flows through as-is.
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    schedules = [{"hour": 6, "minute": 0, "zones": ["z1"]}, {"hour": 19, "minute": 30, "zones": []}]
    coord.on_mqtt_state(THING, {"schedules": schedules})
    assert coord.data[THING]["schedules"] == schedules


def test_on_mqtt_state_deep_merges_robot_config_partial_patch() -> None:
    """A partial robotConfig push must not drop other known robotConfig keys."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"robotConfig": {"isOpenLed": True, "metric_4g": False}}}
    coord._mqtt_state[THING] = {"robotConfig": {"isOpenLed": True, "metric_4g": False}}

    # Robot pushes only metric_4g — isOpenLed must stick around.
    coord.on_mqtt_state(THING, {"robotConfig": {"metric_4g": True}})
    assert coord.data[THING]["robotConfig"] == {"isOpenLed": True, "metric_4g": True}
    assert coord._mqtt_state[THING]["robotConfig"] == {"isOpenLed": True, "metric_4g": True}


def test_on_mqtt_state_no_deep_merge_when_existing_lacks_key() -> None:
    """First robotConfig sighting is stored verbatim — nothing to merge with."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"battery": 70}}
    coord.on_mqtt_state(THING, {"robotConfig": {"metric_4g": True}})
    assert coord.data[THING]["robotConfig"] == {"metric_4g": True}


def test_on_mqtt_state_non_robot_config_patches_unchanged() -> None:
    """Patches without robotConfig take the fast path (no extra dict copy)."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"battery": 70, "robotConfig": {"isOpenLed": True}}}
    coord.on_mqtt_state(THING, {"battery": 65})
    assert coord.data[THING] == {"battery": 65, "robotConfig": {"isOpenLed": True}}


def test_on_mqtt_state_deep_merges_nested_rr_config_partial_patch() -> None:
    """A partial robotConfig.rrConfig push (e.g. only ``enable`` after a
    toggle) must not drop the sibling battery thresholds / period times."""
    coord, _, _ = _make_coordinator()
    full_rr = {
        "enable": True,
        "rechargeBat": 15,
        "resumeBat": 75,
        "periodStart": {"hour": 4, "minute": 0},
        "periodEnd": {"hour": 20, "minute": 0},
    }
    coord.data = {THING: {"robotConfig": {"isOpenLed": True, "rrConfig": full_rr}}}
    coord._mqtt_state[THING] = {"robotConfig": {"isOpenLed": True, "rrConfig": dict(full_rr)}}

    coord.on_mqtt_state(THING, {"robotConfig": {"rrConfig": {"enable": False}}})
    assert coord.data[THING]["robotConfig"]["isOpenLed"] is True
    assert coord.data[THING]["robotConfig"]["rrConfig"] == {**full_rr, "enable": False}
    assert coord._mqtt_state[THING]["robotConfig"]["rrConfig"] == {**full_rr, "enable": False}


def test_on_mqtt_state_caches_path_data_and_reinjects_when_absent() -> None:
    coord, _, _ = _make_coordinator()
    path = {"segments": [[{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]]}
    coord.on_mqtt_state(THING, {"pathData": path})
    assert coord._last_path_data[THING] == path
    # A later patch without pathData re-injects the cached track so the sensor
    # attribute stays populated after the robot docks.
    coord.on_mqtt_state(THING, {"battery": 80})
    assert coord._mqtt_state[THING]["pathData"] == path


def test_on_mqtt_state_mowing_start_clears_cache_and_schedules_poll() -> None:
    from lymow.const import WORK_STATUS_MOWING_GROUP

    coord, _, _ = _make_coordinator()
    mow = next(iter(WORK_STATUS_MOWING_GROUP))
    coord._async_poll_path = MagicMock()  # avoid creating a real coroutine here
    coord._last_path_data[THING] = {"segments": [[{"x": 1.0, "y": 2.0}]]}
    coord.on_mqtt_state(THING, {"workStatus": mow})  # prev (-1) → mowing
    assert THING not in coord._last_path_data
    assert coord._path_poll_pending[THING] is True
    coord._async_poll_path.assert_called_once_with(THING)


@pytest.mark.asyncio
async def test_async_poll_path_reschedules_while_mowing() -> None:
    import asyncio
    from unittest.mock import patch as _patch

    from lymow.const import WORK_STATUS_MOWING_GROUP
    from lymow.protocol import encode_query_path

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": next(iter(WORK_STATUS_MOWING_GROUP))}}
    coord._path_poll_pending[THING] = True
    created: list = []

    def _create(coro):
        created.append(coro)
        if asyncio.iscoroutine(coro):
            coro.close()

    coord.hass.async_create_task = MagicMock(side_effect=_create)
    with _patch("asyncio.sleep", AsyncMock()):
        await coord._async_poll_path(THING)
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_query_path())
    assert len(created) == 1  # re-scheduled itself for the next 30 s cycle


@pytest.mark.asyncio
async def test_async_poll_path_stops_when_no_longer_mowing() -> None:
    from unittest.mock import patch as _patch

    from lymow.protocol import encode_query_path

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}  # docked → no re-schedule
    coord._path_poll_pending[THING] = True
    with _patch("asyncio.sleep", AsyncMock()):
        await coord._async_poll_path(THING)
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_query_path())
    assert coord._path_poll_pending[THING] is False
    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_async_query_schedules_clears_stale_and_publishes() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord._mqtt_state[THING] = {"schedules": [{"hour": 6}], "battery": 50}
    coord.data = {THING: {"schedules": [{"hour": 6}], "battery": 50}}
    await coord.async_query_schedules(THING)
    # published schedules cleared (no stale entries), other fields kept
    assert "schedules" not in coord._mqtt_state[THING]
    assert "schedules" not in coord.data[THING]
    assert coord.data[THING]["battery"] == 50
    mqtt.async_publish_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_query_schedules_no_published_value_is_safe() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"battery": 50}}  # no schedules key yet
    await coord.async_query_schedules(THING)
    mqtt.async_publish_command.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_query_all_schedules_covers_every_device() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_query_all_schedules()
    assert mqtt.async_publish_command.await_count == len(coord.devices)


@pytest.mark.asyncio
async def test_async_set_task_config_publishes_encoded_command() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_task_config(THING, pathSpacing=250)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == 49  # USER_CTRL_GLOBAL_SETTING_N (Keep Custom) — LIVE-CONFIRMED 2026-05-30
    pb_map = _decode_fields(_first(f, 12))
    cfg = _decode_fields(_first(pb_map, 11))  # PbMap.globalZoneConfig
    assert _first(cfg, 9) == 250  # pathSpacing — confirmed PbZoneConfig field f9 (2026-05-30)


@pytest.mark.asyncio
async def test_async_set_run_time_config_publishes_encoded_command() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_run_time_config(THING, cutHeight=55)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == 50  # USER_CTRL_SET_RUN_TIME_CONFIG
    pb_map = _decode_fields(_first(f, 12))
    cfg = _decode_fields(_first(pb_map, 13))
    assert _first(cfg, 1) == 55  # cutHeight


@pytest.mark.asyncio
async def test_async_set_device_settings_round_trip() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_device_settings(THING, charging_mode=1, rainy_mowing=False)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == 36  # USER_CTRL_SET_TASK_CONFIG
    cfg = _decode_fields(_first(f, 26))
    assert _first(cfg, 1) == 1  # chargingMode
    assert _first(cfg, 3) == 0  # rainy_mowing False


@pytest.mark.asyncio
async def test_async_set_device_settings_optimistic_update() -> None:
    """Setting device settings immediately reflects in coordinator data (optimistic)."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": {}}}
    await coord.async_set_device_settings(
        THING,
        rainy_mowing=True,
        charging_handbrake=True,
        zone_order=1,
        charging_mode=0,
    )
    tc = coord.data[THING]["mapData"]["taskConfig"]
    assert tc["rainCleaning"] is True
    assert tc["disableChargingPark"] is False  # inverted: UI-True → wire-False
    assert tc["zoneOrder"] == 1
    assert tc["chargingMode"] == 0


@pytest.mark.asyncio
async def test_async_set_device_settings_optimistic_skipped_when_no_data() -> None:
    coord, _, _ = _make_coordinator()
    # Should not raise even with no coordinator data
    await coord.async_set_device_settings(THING, rainy_mowing=True)


@pytest.mark.asyncio
async def test_async_set_zone_config_publishes_userctrl_9_and_queries_map() -> None:
    """async_set_zone_config publishes a userCtrl=9 frame then re-queries the map."""
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_zone_config(THING, [{"hashId": "wsmjco1T", "cutHeight": 40}])
    # Two publishes: the set_zone_config frame + the follow-up query_map.
    assert mqtt.async_publish_command.await_count == 2
    first_call = mqtt.async_publish_command.await_args_list[0]
    second_call = mqtt.async_publish_command.await_args_list[1]
    assert first_call.args[0] == THING
    assert _first(_decode_fields(first_call.args[1]), 5) == 9  # MODIFY_ZONE_INFO
    assert _first(_decode_fields(second_call.args[1]), 5) == 19  # QUERY_MAP


@pytest.mark.asyncio
async def test_async_set_zone_config_empty_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    with pytest.raises(HomeAssistantError):
        await coord.async_set_zone_config(THING, [])


@pytest.mark.asyncio
async def test_async_update_channel_settings_mutates_cache_then_syncs() -> None:
    """Verify channel cut_height + channel_lift are written through sync_map."""
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    coord.data = {
        THING: {
            "mapData": {
                "goZones": [],
                "nogoZones": [],
                "channels": [
                    {"hashId": "ch000001", "cutHeight": 40, "channelLift": 0},
                ],
            }
        }
    }
    await coord.async_update_channel_settings(THING, "ch000001", cut_height_mm=55, channel_lift=2)
    # First call is the sync_map publish, second is the follow-up query_map.
    assert mqtt.async_publish_command.await_count >= 1
    first_call = mqtt.async_publish_command.await_args_list[0]
    pb = first_call.args[1]
    assert _first(_decode_fields(pb), 5) == 25  # USER_CTRL_SYNC_MAP


@pytest.mark.asyncio
async def test_async_update_channel_settings_unknown_channel_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": {"channels": [{"hashId": "other"}]}}}
    with pytest.raises(HomeAssistantError, match="not found"):
        await coord.async_update_channel_settings(THING, "ch000001", cut_height_mm=50)


@pytest.mark.asyncio
async def test_async_update_channel_settings_no_map_data_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="not yet loaded"):
        await coord.async_update_channel_settings(THING, "ch000001", cut_height_mm=50)


@pytest.mark.asyncio
async def test_async_get_clean_history_returns_list_of_entries() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history = AsyncMock(
        return_value={
            "clean_history": [
                {"clean_area": 100.0, "clean_time": 10, "date": 1779020649},
                {"clean_area": 200.0, "clean_time": 20, "date": 1779017649},
            ]
        }
    )
    out = await coord.async_get_clean_history(THING)
    assert len(out) == 2
    assert out[0]["clean_area"] == 100.0
    api.get_clean_history.assert_awaited_once_with(THING, page=0, page_size=15)


@pytest.mark.asyncio
async def test_async_get_clean_history_filters_non_dict_entries() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history = AsyncMock(
        return_value={"clean_history": [{"clean_area": 1.0}, "garbage", None, {"clean_area": 2.0}]}
    )
    out = await coord.async_get_clean_history(THING)
    assert [e["clean_area"] for e in out] == [1.0, 2.0]


@pytest.mark.asyncio
async def test_async_get_clean_history_returns_empty_for_bad_envelope() -> None:
    coord, _, api = _make_coordinator()
    api.get_clean_history = AsyncMock(return_value=None)
    assert await coord.async_get_clean_history(THING) == []
    api.get_clean_history = AsyncMock(return_value={"clean_history": "not-a-list"})
    assert await coord.async_get_clean_history(THING) == []


@pytest.mark.asyncio
async def test_async_get_clean_history_wraps_underlying_errors() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, api = _make_coordinator()
    api.get_clean_history = AsyncMock(side_effect=RuntimeError("network down"))
    with pytest.raises(HomeAssistantError, match="get_clean_history failed"):
        await coord.async_get_clean_history(THING)


@pytest.mark.asyncio
async def test_async_bind_rtk_publishes_encoded_command() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_bind_rtk(THING, "LK000PLACEHOLD00")  # placeholder base id
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    assert _first(_decode_fields(_first(cfg, 17)), 1) == b"LK000PLACEHOLD00"


@pytest.mark.asyncio
async def test_async_set_wifi_writes_over_ble_not_mqtt(monkeypatch) -> None:
    """Wi-Fi provisioning is BLE-only (live-confirmed 2026-05-30; issue #200):
    it must write to a BLE controller, never publish over MQTT."""
    coord, mqtt, _ = _make_coordinator()
    created: list = []

    def ctor(address):
        c = MagicMock()
        c.address = address
        c.async_write_once = AsyncMock()
        created.append(c)
        return c

    monkeypatch.setattr(sys.modules["lymow.coordinator"], "LymowBleController", ctor)
    await coord.async_set_wifi("AA:BB:CC:DD:EE:FF", "TestNet", "testpass12")  # placeholder creds
    assert len(created) == 1 and created[0].address == "AA:BB:CC:DD:EE:FF"
    created[0].async_write_once.assert_awaited_once()
    # The encoded payload must carry real bytes (the SSID/password), not be empty.
    (payload,), _ = created[0].async_write_once.call_args
    assert isinstance(payload, bytes) and len(payload) > 0
    mqtt.async_publish_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_set_pin_publishes_encoded_command() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_pin(THING, "1234")  # placeholder PIN
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    assert pb.hex() == "10316a084a060a0401020304"


@pytest.mark.asyncio
async def test_async_set_headlight_schedule_publishes_encoded_command() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_headlight_schedule(THING, enable=True, start=(3, 17), end=(4, 23))
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    assert pb.hex() == "10314a0250016a0c7204080310117a0408041017"


@pytest.mark.asyncio
async def test_async_set_recharge_resume_round_trip() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_recharge_resume(THING, enable=True, period_start=(8, 0), resume_bat=75)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    cfg = _decode_fields(_first(_decode_fields(pb), 13))
    rr = _decode_fields(_first(cfg, 18))
    assert _first(rr, 1) == 1  # enableRr
    start = _decode_fields(_first(rr, 2))
    assert _first(start, 1) == 8
    assert _first(rr, 5) == 75  # resumeBat


@pytest.mark.asyncio
async def test_async_set_run_time_config_mirrors_write_into_runtime_config_dict() -> None:
    """Optimistic state for the Live cut-height/move-speed/cut-speed Numbers:
    a successful write lands in self.data[thing]["runTimeConfig"] so those
    entities reflect the value the user just set."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"runTimeConfig": {"cutHeight": 30}}}
    await coord.async_set_run_time_config(THING, moveSpeed=0.4, cutSpeed=200)
    assert coord.data[THING]["runTimeConfig"] == {
        "cutHeight": 30,  # preserved
        "moveSpeed": 0.4,
        "cutSpeed": 200,
    }


@pytest.mark.asyncio
async def test_async_set_run_time_config_skips_mirror_when_thing_not_in_data() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {}
    await coord.async_set_run_time_config(THING, cutHeight=55)
    assert THING not in coord.data


@pytest.mark.asyncio
async def test_async_set_run_time_config_coerces_non_dict_cache_to_baseline() -> None:
    """Untrusted cache: if a future decode path puts a non-dict at
    ``runTimeConfig``, the optimistic merge must not TypeError on the
    dict-union — coerce to an empty baseline so the publish still mirrors."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"runTimeConfig": "not a dict"}}
    await coord.async_set_run_time_config(THING, cutHeight=55)
    assert coord.data[THING]["runTimeConfig"] == {"cutHeight": 55}


@pytest.mark.asyncio
async def test_async_set_robot_config_publishes_metric_4g_without_userctrl() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_set_robot_config(THING, metric_4g=True)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) is None  # no userCtrl on robotConfig writes
    cfg = _decode_fields(_first(f, 13))  # PbInput.robotConfig
    assert _first(cfg, 11) == 1  # metric_4g


@pytest.mark.asyncio
async def test_async_find_my_robot_play_sound_publishes_captured_wire() -> None:
    """Wire frame captured live from the app — fires the find-my-robot beacon
    via PbInput {f13.audioVolume=100, f16=1} with no userCtrl."""
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_find_my_robot_play_sound(THING)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    assert pb.hex() == "10316a023064800101"
    f = _decode_fields(pb)
    assert _first(f, 5) is None  # no userCtrl
    assert _first(f, 16) == 1  # play-sound trigger
    cfg = _decode_fields(_first(f, 13))
    assert _first(cfg, 6) == 100  # audioVolume max (default)


@pytest.mark.asyncio
async def test_async_sync_timezone_publishes_offset_on_field_21() -> None:
    """Mirrors what the app's setTimezone (#9036) writes — seconds east of UTC
    on PbRobotConfig.f21, via the no-userCtrl robotConfig path."""
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_sync_timezone(THING, 9 * 3600)
    mqtt.async_publish_command.assert_awaited_once()
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) is None  # no userCtrl
    cfg = _decode_fields(_first(f, 13))
    assert _first(cfg, 21) == 9 * 3600


# ---------------------------------------------------------------------------
# MQTT online callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_data_seeds_proto3_config_defaults() -> None:
    """Absent (proto3-default) settings fields are backfilled so settings
    entities read a default instead of unknown — without clobbering real values."""
    coord, _, _ = _make_coordinator(rest_data={"deviceState": "offline", "battery": 50})
    # Robot reported only audioVolume; everything else is at its omitted default.
    coord._mqtt_state[THING] = {"robotConfig": {"audioVolume": 100}}
    coord.hass.async_create_task = _make_task_closer()
    result = await coord._async_update_data()
    rc = result[THING]["robotConfig"]
    assert rc["audioVolume"] == 100  # real value preserved
    assert rc["isOpenLed"] is False and rc["metric_4g"] is False and rc["dockOnError"] is False
    assert rc["rrConfig"] == {"enable": False, "rechargeBat": 0, "resumeBat": 0}
    tc = result[THING]["mapData"]["taskConfig"]
    assert tc == {"chargingMode": 0, "zoneOrder": 0, "rainCleaning": False, "disableChargingPark": False}


@pytest.mark.asyncio
async def test_async_update_data_seed_does_not_overwrite_real_config() -> None:
    coord, _, _ = _make_coordinator(rest_data={"deviceState": "offline"})
    coord._mqtt_state[THING] = {
        "robotConfig": {"isOpenLed": True, "rrConfig": {"enable": True, "rechargeBat": 15}},
        "mapData": {"taskConfig": {"zoneOrder": 1}, "goZones": [{"hashId": "z1"}]},
    }
    coord.hass.async_create_task = _make_task_closer()
    result = await coord._async_update_data()
    rc = result[THING]["robotConfig"]
    assert rc["isOpenLed"] is True
    assert rc["rrConfig"]["enable"] is True and rc["rrConfig"]["rechargeBat"] == 15
    assert rc["rrConfig"]["resumeBat"] == 0  # the one absent sub-field is defaulted
    md = result[THING]["mapData"]
    assert md["taskConfig"]["zoneOrder"] == 1 and md["taskConfig"]["chargingMode"] == 0
    assert md["goZones"] == [{"hashId": "z1"}]  # untouched


@pytest.mark.asyncio
async def test_startup_query_fires_when_rest_reports_online_without_is_online() -> None:
    """A robot already online at HA start sends no notify-app transition, so the
    config query must be gated on the REST deviceState, not just isOnline."""
    coord, _, _ = _make_coordinator(rest_data={"deviceState": "online", "battery": 90})
    created: list = []
    coord.hass.async_create_task = _make_task_closer(created)
    await coord._async_update_data()
    assert THING in coord._startup_queried
    assert len(created) == 2  # query_robot_config + query_map


@pytest.mark.asyncio
async def test_startup_query_skipped_when_rest_reports_offline() -> None:
    coord, _, _ = _make_coordinator(rest_data={"deviceState": "offline"})
    created: list = []
    coord.hass.async_create_task = _make_task_closer(created)
    await coord._async_update_data()
    assert THING not in coord._startup_queried
    assert created == []


@pytest.mark.asyncio
async def test_startup_query_skipped_when_mqtt_not_connected() -> None:
    """The first poll runs during setup before MQTT connects; a query published
    then would be dropped, so the gate must hold until the transport is up."""
    coord, mqtt, _ = _make_coordinator(rest_data={"deviceState": "online"})
    mqtt.is_connected = False
    created: list = []
    coord.hass.async_create_task = _make_task_closer(created)
    await coord._async_update_data()
    assert THING not in coord._startup_queried
    assert created == []


def test_rtk_polling_auto_enables_presence_and_runs_one_timer() -> None:
    coord, _, _ = _make_coordinator()
    assert coord.set_rtk_polling(THING, True) is True  # presence newly enabled
    assert coord.is_rtk_polling(THING) and coord.is_presence_on(THING)
    timer = coord._rtk_poll_unsub
    assert timer is not None  # one shared timer started
    assert coord.set_rtk_polling("thing-2", True) is True
    assert coord._rtk_poll_unsub is timer  # not recreated
    # Disabling RTK keeps presence (and the timer keeps sending heartbeats).
    coord.set_rtk_polling(THING, False)
    assert not coord.is_rtk_polling(THING) and coord.is_presence_on(THING)
    assert coord._rtk_poll_unsub is timer


def test_presence_off_cascades_rtk_off_and_stops_timer() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_polling(THING, True)  # presence + rtk on
    coord.set_presence(THING, False)  # turning presence off stops RTK too
    assert not coord.is_presence_on(THING) and not coord.is_rtk_polling(THING)
    assert coord._rtk_poll_unsub is None  # timer stopped once nothing is active


def test_set_rtk_polling_returns_false_when_presence_already_on() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_presence(THING, True)
    assert coord.set_rtk_polling(THING, True) is False  # presence not newly added


@pytest.mark.asyncio
async def test_rtk_poll_once_heartbeat_only_vs_with_queries() -> None:
    from lymow.protocol import encode_app_connect_heartbeat

    coord, mqtt, _ = _make_coordinator()
    coord.async_query_rtk_diagnostic_l1 = AsyncMock()
    coord.async_query_rtk_diagnostic_l2 = AsyncMock()
    await coord._rtk_poll_once(THING, query_rtk=False)  # presence only
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_app_connect_heartbeat(coord._rtk_session_id))
    coord.async_query_rtk_diagnostic_l1.assert_not_awaited()
    await coord._rtk_poll_once(THING, query_rtk=True)  # presence + queries
    coord.async_query_rtk_diagnostic_l1.assert_awaited_once_with(THING)
    coord.async_query_rtk_diagnostic_l2.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_rtk_poll_tick_schedules_presence_things_when_online() -> None:
    coord, _, _ = _make_coordinator()
    coord._presence_things.add(THING)
    created: list = []
    coord.hass.async_create_task = _make_task_closer(created)
    coord.data = {THING: {"deviceState": "offline"}}
    coord._rtk_poll_tick(None)
    assert created == []
    coord.data = {THING: {"deviceState": "online"}}
    coord._rtk_poll_tick(None)
    assert {c.cr_code.co_name for c in created} == {"_rtk_poll_once"}


@pytest.mark.asyncio
async def test_async_query_all_robot_configs_queries_and_marks_each_device() -> None:
    devices = [{"deviceThingName": "mower-001"}, {"deviceThingName": "mower-002"}]
    coord, mqtt, _ = _make_coordinator(devices=devices)
    await coord.async_query_all_robot_configs()
    assert mqtt.async_publish_command.await_count == 2
    assert coord._startup_queried == {"mower-001", "mower-002"}


def test_on_mqtt_online_sets_is_online_true() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    coord.on_mqtt_online(THING, True)
    assert coord.data[THING]["isOnline"] is True
    assert coord.data[THING]["deviceState"] == "online"


def test_on_mqtt_online_schedules_robot_config_and_map_queries() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    create_task = _capture_create_task(coord)
    coord.on_mqtt_online(THING, True)
    assert create_task.call_count == 2  # query_robot_config + query_map


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
async def test_async_shutdown_stops_rtk_poll_timer() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_presence(THING, True)  # starts the poll timer
    unsub = coord._rtk_poll_unsub = MagicMock()
    await coord.async_shutdown()
    unsub.assert_called_once()  # timer unsubscribed, no leaked callback
    assert coord._rtk_poll_unsub is None


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

    assert mqtt.async_publish_command.await_count == 2  # sync-map + query-map
    thing, _ = mqtt.async_publish_command.await_args_list[0].args
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
# Zone polygon edit / new-zone services
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_zone_polygon_publishes_modify_zone_info_and_requeries() -> None:
    from lymow.protocol import encode_set_zone_polygon

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    coord.async_query_map = AsyncMock()
    new_polygon = [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}, {"x": 5.0, "y": 6.0}]
    await coord.async_update_zone_polygon(THING, "zone0001", new_polygon)
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_set_zone_polygon("zone0001", new_polygon))
    coord.async_query_map.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_async_update_nogo_polygon_skips_hash_check_when_no_nogo_zones() -> None:
    from lymow.protocol import encode_set_nogo_polygon

    coord, mqtt, _ = _make_coordinator()
    # _SAMPLE_MAP_DATA has no nogoZones → id set empty → hash-existence check skipped.
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    coord.async_query_map = AsyncMock()
    new_polygon = [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}, {"x": 5.0, "y": 6.0}]
    await coord.async_update_nogo_polygon(THING, "nogo0001", new_polygon)
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_set_nogo_polygon("nogo0001", new_polygon))
    coord.async_query_map.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_async_update_zone_polygon_raises_when_zone_missing() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    with pytest.raises(HomeAssistantError, match="not found"):
        await coord.async_update_zone_polygon(THING, "no-such-zone", [{"x": 0.0, "y": 0.0}] * 3)


@pytest.mark.asyncio
async def test_async_update_zone_polygon_rejects_too_few_vertices() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    with pytest.raises(HomeAssistantError, match="3 vertices"):
        await coord.async_update_zone_polygon(THING, "zone0001", [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}])


@pytest.mark.asyncio
async def test_async_update_zone_polygon_rejects_malformed_point() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    with pytest.raises(HomeAssistantError, match="'x' and 'y'"):
        await coord.async_update_zone_polygon(
            THING, "zone0001", [{"x": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 2.0}]
        )


@pytest.mark.asyncio
async def test_async_update_zone_polygon_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_update_zone_polygon(THING, "zone0001", [{"x": 0.0, "y": 0.0}] * 3)


@pytest.mark.asyncio
async def test_async_move_charging_station_updates_x_y_theta() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_DATA)}}
    captured = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    await coord.async_move_charging_station(THING, 3.5, -1.2, theta=1.57)
    cs = captured["map"]["chargingStation"]
    assert cs["x"] == pytest.approx(3.5)
    assert cs["y"] == pytest.approx(-1.2)
    assert cs["theta"] == pytest.approx(1.57)


@pytest.mark.asyncio
async def test_async_move_charging_station_defaults_theta_from_existing() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    map_data = copy.deepcopy(_SAMPLE_MAP_DATA)
    map_data["chargingStation"] = {"x": 0.0, "y": 0.0, "theta": 2.0}
    coord.data = {THING: {"mapData": map_data}}
    captured = {}

    async def _capture(thing, md):
        captured["map"] = md

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    await coord.async_move_charging_station(THING, 1.0, 2.0)
    assert captured["map"]["chargingStation"]["theta"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_async_move_charging_station_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_move_charging_station(THING, 0.0, 0.0)


@pytest.mark.asyncio
async def test_async_add_zone_appends_new_zone_with_fresh_hash() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_DATA)}}
    captured = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    poly = [{"x": 10.0, "y": 10.0}, {"x": 20.0, "y": 10.0}, {"x": 15.0, "y": 20.0}]
    new_id = await coord.async_add_zone(THING, poly, name="patio", cut_height_mm=35)
    # New hashId is unique and present in goZones.
    assert new_id not in {"zone0001", "zone0002"}
    assert any(z["hashId"] == new_id for z in captured["map"]["goZones"])
    added = next(z for z in captured["map"]["goZones"] if z["hashId"] == new_id)
    assert added["name"] == "patio"
    assert added["cutHeight"] == 35
    assert added["isEnabled"] is True
    assert added["polygon"] == poly
    assert new_id in captured["map"]["modifyHashs"]


@pytest.mark.asyncio
async def test_async_add_zone_avoids_hash_collision() -> None:
    """Defensive: if secrets.token_hex were to collide with an existing id, the
    method must keep retrying. We force a collision once, then succeed."""
    import copy

    coord, _, _ = _make_coordinator()
    # Pre-existing hash that the first secrets call will pretend to produce.
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_DATA)}}

    async def _noop(thing, map_data):
        pass

    coord.async_sync_map = _noop  # type: ignore[method-assign]

    from unittest.mock import patch as _patch

    with _patch("secrets.token_hex", side_effect=["zone0001", "fresh1234"]):
        new_id = await coord.async_add_zone(THING, [{"x": 0.0, "y": 0.0}] * 3)
    assert new_id == "fresh1234"


@pytest.mark.asyncio
async def test_async_add_zone_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_add_zone(THING, [{"x": 0.0, "y": 0.0}] * 3)


@pytest.mark.asyncio
async def test_async_add_zone_rejects_too_few_vertices() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    with pytest.raises(HomeAssistantError, match="3 vertices"):
        await coord.async_add_zone(THING, [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}])


@pytest.mark.asyncio
async def test_async_add_zone_rejects_malformed_point() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_DATA}}
    with pytest.raises(HomeAssistantError, match="'x' and 'y'"):
        await coord.async_add_zone(THING, [{"x": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 2.0}])


# ---------------------------------------------------------------------------
# async_update_nogo_polygon / async_add_nogo_zone / async_add_channel
# ---------------------------------------------------------------------------


_SAMPLE_MAP_WITH_NOGO = {
    "goZones": [{"hashId": "zone0001", "polygon": [], "isEnabled": True}],
    "nogoZones": [
        {
            "hashId": "nogo0001",
            "polygon": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.5, "y": 1.0}],
            "isEnabled": True,
            "parentZoneHashId": "zone0001",
        }
    ],
    "channels": [],
}


@pytest.mark.asyncio
async def test_async_update_nogo_polygon_publishes_modify_for_known_zone() -> None:
    from lymow.protocol import encode_set_nogo_polygon

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    coord.async_query_map = AsyncMock()
    new_poly = [{"x": 10.0, "y": 10.0}, {"x": 20.0, "y": 10.0}, {"x": 15.0, "y": 20.0}]
    await coord.async_update_nogo_polygon(THING, "nogo0001", new_poly)
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_set_nogo_polygon("nogo0001", new_poly))
    coord.async_query_map.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_async_update_nogo_polygon_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_update_nogo_polygon(THING, "nogo0001", [{"x": 0.0, "y": 0.0}] * 3)


@pytest.mark.asyncio
async def test_async_update_nogo_polygon_rejects_too_few_vertices() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="3 vertices"):
        await coord.async_update_nogo_polygon(THING, "nogo0001", [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}])


@pytest.mark.asyncio
async def test_async_update_nogo_polygon_rejects_malformed_point() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="'x' and 'y'"):
        await coord.async_update_nogo_polygon(
            THING, "nogo0001", [{"x": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 2.0}]
        )


@pytest.mark.asyncio
async def test_async_update_nogo_polygon_raises_for_unknown_hash() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="not found"):
        await coord.async_update_nogo_polygon(THING, "missingxx", [{"x": 0.0, "y": 0.0}] * 3)


@pytest.mark.asyncio
async def test_async_add_nogo_zone_appends_with_parent_and_fresh_hash() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_WITH_NOGO)}}
    captured: dict = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    poly = [{"x": 5.0, "y": 5.0}, {"x": 6.0, "y": 5.0}, {"x": 5.5, "y": 6.0}]
    new_id = await coord.async_add_nogo_zone(THING, poly, parent_zone_hash_id="zone0001")

    assert new_id not in {"nogo0001"}
    added = next(z for z in captured["map"]["nogoZones"] if z["hashId"] == new_id)
    assert added["parentZoneHashId"] == "zone0001"
    assert added["isEnabled"] is True
    assert added["polygon"] == poly
    assert new_id in captured["map"]["modifyHashs"]


@pytest.mark.asyncio
async def test_async_add_nogo_zone_avoids_hash_collision() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_WITH_NOGO)}}

    async def _noop(thing, map_data):
        pass

    coord.async_sync_map = _noop  # type: ignore[method-assign]

    from unittest.mock import patch as _patch

    with _patch("secrets.token_hex", side_effect=["nogo0001", "freshn"]):
        new_id = await coord.async_add_nogo_zone(THING, [{"x": 0.0, "y": 0.0}] * 3)
    assert new_id == "freshn"


@pytest.mark.asyncio
async def test_async_add_nogo_zone_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_add_nogo_zone(THING, [{"x": 0.0, "y": 0.0}] * 3)


@pytest.mark.asyncio
async def test_async_add_nogo_zone_rejects_too_few_vertices() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="3 vertices"):
        await coord.async_add_nogo_zone(THING, [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}])


@pytest.mark.asyncio
async def test_async_add_nogo_zone_rejects_malformed_point() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="'x' and 'y'"):
        await coord.async_add_nogo_zone(THING, [{"x": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 2.0}])


@pytest.mark.asyncio
async def test_async_add_channel_appends_with_zone_links_and_fresh_hash() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_WITH_NOGO)}}
    captured: dict = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    poly = [{"x": 1.0, "y": 1.0}, {"x": 2.0, "y": 2.0}]
    new_id = await coord.async_add_channel(
        THING, poly, zone1_hash_id="zone0001", zone2_hash_id="zone0002", cut_height_mm=55
    )

    added = next(c for c in captured["map"]["channels"] if c["hashId"] == new_id)
    assert added["zone1"] == "zone0001"
    assert added["zone2"] == "zone0002"
    assert added["cutHeight"] == 55
    assert added["isValid"] is True
    assert added["polygon"] == poly
    assert new_id in captured["map"]["modifyHashs"]


@pytest.mark.asyncio
async def test_async_add_channel_avoids_hash_collision() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_SAMPLE_MAP_WITH_NOGO)}}

    async def _noop(thing, map_data):
        pass

    coord.async_sync_map = _noop  # type: ignore[method-assign]

    from unittest.mock import patch as _patch

    # First call returns an existing nogo hash, retry returns a fresh one
    with _patch("secrets.token_hex", side_effect=["nogo0001", "freshch"]):
        new_id = await coord.async_add_channel(THING, [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}])
    assert new_id == "freshch"


@pytest.mark.asyncio
async def test_async_add_channel_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_add_channel(THING, [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 1.0}])


@pytest.mark.asyncio
async def test_async_add_channel_rejects_too_few_points() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="2 points"):
        await coord.async_add_channel(THING, [{"x": 0.0, "y": 0.0}])


@pytest.mark.asyncio
async def test_async_add_channel_rejects_malformed_point() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _SAMPLE_MAP_WITH_NOGO}}
    with pytest.raises(HomeAssistantError, match="'x' and 'y'"):
        await coord.async_add_channel(THING, [{"x": 0.0}, {"x": 1.0, "y": 1.0}])


# ---------------------------------------------------------------------------
# Zone update commands — async_update_zone_enabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_zone_enabled_publishes_sync_map() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "mapData": _SAMPLE_MAP_DATA}}

    await coord.async_update_zone_enabled(THING, "zone0001", False)

    assert mqtt.async_publish_command.await_count == 2  # sync-map + query-map
    thing, _ = mqtt.async_publish_command.await_args_list[0].args
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
async def test_async_delete_zone_sends_command_then_queries_map() -> None:
    from lymow.const import USER_CTRL_CLEAR_ZONE, USER_CTRL_QUERY_MAP
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_delete_zone(THING, "zone0001")

    # 1 = delete (USER_CTRL_CLEAR_ZONE), 2 = query-map refresh so the card stops showing the deleted zone.
    assert mqtt.async_publish_command.await_count == 2
    thing_del, pb_del = mqtt.async_publish_command.await_args_list[0].args
    thing_q, pb_q = mqtt.async_publish_command.await_args_list[1].args
    assert thing_del == THING
    assert thing_q == THING
    assert _first(_decode_fields(pb_del), 5) == USER_CTRL_CLEAR_ZONE
    assert _first(_decode_fields(pb_q), 5) == USER_CTRL_QUERY_MAP


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
    assert result[THING]["lastCleanDurationSec"] == 60
    assert result[THING]["lastCleanAt"] == datetime.fromtimestamp(1779184292, tz=UTC)
    assert result[THING]["lastCleanPercent"] == 100.0
    assert result[THING]["lastCleanBatteryUsed"] == 49
    assert result[THING]["cleanHistoryCount"] == 14  # cumulative, from total_records
    assert result[THING]["totalCleanTimeSec"] == 829
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
    assert result[THING]["totalCleanTimeSec"] == 100
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
# Backup map list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_map_fields_populated_from_list() -> None:
    from datetime import UTC, datetime

    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = [
        {"map_file": "a.pb", "name": "", "backup_time": 1778768592},
        {"map_file": "b.pb", "name": "", "backup_time": 1778756506},
    ]
    result = await coord._async_update_data()
    assert result[THING]["backupMapCount"] == 2
    assert result[THING]["backupMapLatestAt"] == datetime.fromtimestamp(1778768592, tz=UTC)
    backups = result[THING]["backupMapList"]
    assert backups[0] == {"file": "a.pb", "name": "", "backupTime": 1778768592}


@pytest.mark.asyncio
async def test_backup_map_count_zero_when_empty() -> None:
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = []
    result = await coord._async_update_data()
    assert result[THING]["backupMapCount"] == 0
    assert result[THING]["backupMapList"] == []
    assert "backupMapLatestAt" not in result[THING]


@pytest.mark.asyncio
async def test_backup_map_swallows_errors() -> None:
    coord, _, api = _make_coordinator(rest_data={"workStatus": 5})
    api.get_backup_map_list.side_effect = RuntimeError("403")
    result = await coord._async_update_data()
    assert result[THING]["workStatus"] == 5
    assert "backupMapCount" not in result[THING]


@pytest.mark.asyncio
async def test_backup_map_handles_invalid_timestamp() -> None:
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = [{"map_file": "a.pb", "backup_time": "not-int"}]
    result = await coord._async_update_data()
    assert result[THING]["backupMapCount"] == 1
    assert "backupMapLatestAt" not in result[THING]


@pytest.mark.asyncio
async def test_backup_map_fetch_throttled_across_refreshes() -> None:
    """Two refreshes within the throttle window should issue only one HTTP call."""
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = [{"map_file": "a.pb", "backup_time": 100}]
    await coord._async_update_data()
    await coord._async_update_data()
    assert api.get_backup_map_list.await_count == 1


@pytest.mark.asyncio
async def test_backup_map_cache_replayed_between_refreshes() -> None:
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = [{"map_file": "a.pb", "backup_time": 100}]
    first = await coord._async_update_data()
    second = await coord._async_update_data()
    assert second[THING]["backupMapCount"] == first[THING]["backupMapCount"] == 1
    assert second[THING]["backupMapList"] == first[THING]["backupMapList"]


@pytest.mark.asyncio
async def test_backup_map_error_replays_stale_cache() -> None:
    """A transient backend error must not drop the previously cached snapshot."""
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = [{"map_file": "a.pb", "backup_time": 100}]
    await coord._async_update_data()
    # Bust the throttle so the next refresh tries to hit the API again.
    fetched_at, fields = coord._backup_map_cache[THING]
    coord._backup_map_cache[THING] = (fetched_at.replace(year=2020), fields)
    api.get_backup_map_list.side_effect = RuntimeError("503")
    result = await coord._async_update_data()
    assert result[THING]["backupMapCount"] == 1


@pytest.mark.asyncio
async def test_backup_map_legacy_field_used_as_file_fallback() -> None:
    """Older payload shapes (`key`, `backupMapUrl`) must surface as `file`."""
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = [
        {"backupMapUrl": "legacy/a.pb", "backup_time": 100},
        {"key": "older/b.pb", "backup_time": 90},
    ]
    result = await coord._async_update_data()
    assert result[THING]["backupMapList"][0]["file"] == "legacy/a.pb"
    assert result[THING]["backupMapList"][1]["file"] == "older/b.pb"


@pytest.mark.asyncio
async def test_backup_map_handles_non_list_response() -> None:
    """If the API ever returns something other than a list, treat it like an error."""
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = "garbage"  # type: ignore[assignment]
    result = await coord._async_update_data()
    assert "backupMapCount" not in result[THING]


@pytest.mark.asyncio
async def test_backup_map_skips_non_dict_entries() -> None:
    coord, _, api = _make_coordinator()
    api.get_backup_map_list.return_value = ["garbage", {"map_file": "a.pb", "backup_time": 100}]
    result = await coord._async_update_data()
    assert result[THING]["backupMapCount"] == 1
    assert result[THING]["backupMapList"] == [{"file": "a.pb", "name": "", "backupTime": 100}]


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
                {"name": "Home", "latitude": 12.0, "longitude": 65.0, "radius": 150},
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
    assert sent[0]["latitude"] == 12.0
    assert sent[0]["longitude"] == 65.0
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


@pytest.mark.asyncio
async def test_async_set_geofence_writes_all_provided_fields() -> None:
    """The full setter accepts lat/lon/radius/name in one PATCH."""
    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Old", "latitude": 10.0, "longitude": 20.0, "radius": 100},
            ]
        }
    }
    coord.async_set_updated_data = MagicMock()

    await coord.async_set_geofence(THING, latitude=59.68, longitude=16.76, radius_m=200, name="Home")
    api.update_device_feature.assert_awaited_once()
    _, kwargs = api.update_device_feature.call_args
    sent = kwargs["geoFence"][0]
    assert sent["latitude"] == 59.68
    assert sent["longitude"] == 16.76
    assert sent["radius"] == 200
    assert sent["name"] == "Home"


@pytest.mark.asyncio
async def test_async_set_geofence_seeds_default_when_no_existing_record() -> None:
    """Allow configuring a fresh device without first opening the Lymow app."""
    coord, _, api = _make_coordinator()
    coord.data = {THING: {}}
    coord.async_set_updated_data = MagicMock()

    await coord.async_set_geofence(THING, latitude=59.68, longitude=16.76, radius_m=150)
    _, kwargs = api.update_device_feature.call_args
    sent = kwargs["geoFence"][0]
    assert sent["latitude"] == 59.68
    assert sent["longitude"] == 16.76
    assert sent["radius"] == 150
    assert sent["name"] == ""


@pytest.mark.asyncio
async def test_async_set_geofence_preserves_unspecified_fields() -> None:
    """A radius-only update keeps the existing centre coords + name."""
    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Yard", "latitude": 10.0, "longitude": 20.0, "radius": 100},
            ]
        }
    }
    coord.async_set_updated_data = MagicMock()

    await coord.async_set_geofence(THING, radius_m=250)
    _, kwargs = api.update_device_feature.call_args
    sent = kwargs["geoFence"][0]
    assert sent["latitude"] == 10.0
    assert sent["longitude"] == 20.0
    assert sent["radius"] == 250
    assert sent["name"] == "Yard"


@pytest.mark.asyncio
async def test_async_set_geofence_index_mutates_only_that_region() -> None:
    """`index=1` updates the second region; other regions are kept intact."""
    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Front", "latitude": 1.0, "longitude": 2.0, "radius": 100},
                {"name": "Back", "latitude": 3.0, "longitude": 4.0, "radius": 150},
                {"name": "Side", "latitude": 5.0, "longitude": 6.0, "radius": 200},
            ]
        }
    }
    coord.async_set_updated_data = MagicMock()

    await coord.async_set_geofence(THING, radius_m=250, name="Renamed Back", index=1)

    _, kwargs = api.update_device_feature.call_args
    sent = kwargs["geoFence"]
    assert len(sent) == 3
    assert sent[0] == {"name": "Front", "latitude": 1.0, "longitude": 2.0, "radius": 100}
    assert sent[1] == {"name": "Renamed Back", "latitude": 3.0, "longitude": 4.0, "radius": 250}
    assert sent[2] == {"name": "Side", "latitude": 5.0, "longitude": 6.0, "radius": 200}


@pytest.mark.asyncio
async def test_async_set_geofence_index_at_len_appends_new_region() -> None:
    """`index == len(current)` appends a new region with defaults + provided fields."""
    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Front", "latitude": 1.0, "longitude": 2.0, "radius": 100},
            ]
        }
    }
    coord.async_set_updated_data = MagicMock()

    await coord.async_set_geofence(THING, latitude=10.0, longitude=20.0, radius_m=180, name="Back", index=1)

    _, kwargs = api.update_device_feature.call_args
    sent = kwargs["geoFence"]
    assert len(sent) == 2
    assert sent[0] == {"name": "Front", "latitude": 1.0, "longitude": 2.0, "radius": 100}
    assert sent[1] == {"name": "Back", "latitude": 10.0, "longitude": 20.0, "radius": 180}


@pytest.mark.asyncio
async def test_async_set_geofence_index_out_of_range_raises() -> None:
    """Skipping past the end of the list is an error, not a silent extend."""
    from homeassistant.exceptions import HomeAssistantError

    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Front", "latitude": 1.0, "longitude": 2.0, "radius": 100},
            ]
        }
    }
    with pytest.raises(HomeAssistantError, match="index 5 is out of range"):
        await coord.async_set_geofence(THING, radius_m=250, index=5)
    api.update_device_feature.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_set_geofence_negative_index_raises() -> None:
    """Negative indexes would silently mutate the last entry — reject them."""
    from homeassistant.exceptions import HomeAssistantError

    coord, _, api = _make_coordinator()
    coord.data = {
        THING: {
            "geoFence": [
                {"name": "Front", "latitude": 1.0, "longitude": 2.0, "radius": 100},
            ]
        }
    }
    with pytest.raises(HomeAssistantError, match="index -1 is out of range"):
        await coord.async_set_geofence(THING, radius_m=250, index=-1)
    api.update_device_feature.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_send_user_ctrl_publishes_command() -> None:
    from lymow.const import USER_CTRL_LOCK
    from lymow.protocol import _decode_fields

    coord, mqtt, _ = _make_coordinator()
    await coord.async_send_user_ctrl(THING, USER_CTRL_LOCK)

    assert mqtt.async_publish_command.await_count == 1
    thing, pb_bytes = mqtt.async_publish_command.call_args[0]
    assert thing == THING
    by_field = {fn: val for fn, _wt, val in _decode_fields(pb_bytes)}
    assert by_field[5] == USER_CTRL_LOCK


# ---------------------------------------------------------------------------
# QUERY_* service helpers — each publishes a bare userCtrl pbinput
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method_name", "expected_code"),
    [
        ("async_query_cleaning_info", 24),
        ("async_query_cleaning_summary", 34),
        # async_query_robot_config uses encode_query_robot_config() — f9 sub-message,
        # not plain userCtrl=35 — tested separately below.
        # ("async_query_robot_config", 35),  # removed: wrong encoding, see test below
        ("async_query_path", 23),
        ("async_query_channels", 39),
        ("async_query_run_time_config", 51),
        ("async_query_wifi_4g", 52),
        ("async_query_net_detail", 53),
        ("async_query_rtk_diagnostic_l1", 57),
        ("async_query_rtk_diagnostic_l2", 58),
    ],
)
@pytest.mark.asyncio
async def test_query_helpers_publish_correct_userctrl(method_name: str, expected_code: int) -> None:
    from lymow.protocol import _decode_fields

    coord, mqtt, _ = _make_coordinator()
    await getattr(coord, method_name)(THING)

    assert mqtt.async_publish_command.await_count == 1
    thing, pb_bytes = mqtt.async_publish_command.call_args[0]
    assert thing == THING
    by_field = {fn: val for fn, _wt, val in _decode_fields(pb_bytes)}
    # userCtrl lives at field 5 — same convention as every other userCtrl-only command.
    assert by_field[5] == expected_code


@pytest.mark.asyncio
async def test_query_robot_config_uses_f9_submessage() -> None:
    """async_query_robot_config must use PbInput.f9={f10=1}, NOT plain userCtrl=35.

    The robot silently ignores a plain userCtrl=35; confirmed from app capture
    that getRobotConfig requires the f9 sub-command discriminator.
    """
    from lymow.protocol import _decode_fields

    coord, mqtt, _ = _make_coordinator()
    await coord.async_query_robot_config(THING)

    assert mqtt.async_publish_command.await_count == 1
    _thing, pb_bytes = mqtt.async_publish_command.call_args[0]
    by_field = {fn: val for fn, _wt, val in _decode_fields(pb_bytes)}
    # Must NOT use f5 (userCtrl); must use f9 sub-message with f10=1 inside.
    assert 5 not in by_field, "must not send plain userCtrl=35"
    assert isinstance(by_field.get(9), bytes)
    inner = {fn: val for fn, _wt, val in _decode_fields(by_field[9])}
    assert inner.get(10) == 1


# ---------------------------------------------------------------------------
# RTK auto-pause guard
# ---------------------------------------------------------------------------


def test_rtk_guard_defaults_disabled() -> None:
    coord, _, _ = _make_coordinator()
    assert coord.is_rtk_guard_enabled(THING) is False
    assert coord.get_rtk_guard_threshold(THING) == 1


def test_set_rtk_guard_enabled_toggles_state() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    assert coord.is_rtk_guard_enabled(THING) is True
    coord.set_rtk_guard_enabled(THING, False)
    assert coord.is_rtk_guard_enabled(THING) is False


def test_disable_clears_guard_paused_flag() -> None:
    """Disabling the feature mid-flight must not leave a stale guard-paused state
    around, otherwise a later natural pause→resume cycle would be mis-attributed."""
    coord, _, _ = _make_coordinator()
    coord._rtk_guard_active_pause[THING] = True
    coord.set_rtk_guard_enabled(THING, False)
    assert coord._rtk_guard_active_pause[THING] is False


def test_set_rtk_guard_threshold_persists() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_threshold(THING, 2)
    assert coord.get_rtk_guard_threshold(THING) == 2


def _capture_create_task(coord) -> MagicMock:
    """Replace coord.hass.async_create_task with a mock that closes the coroutine
    so we don't see "coroutine was never awaited" RuntimeWarnings."""
    mock = MagicMock(side_effect=lambda coro: coro.close())
    coord.hass.async_create_task = mock
    return mock


def test_rtk_guard_disabled_does_not_schedule_task() -> None:
    """When the switch is off the MQTT-side check is a complete no-op."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2}}  # mowing
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 0})
    create_task.assert_not_called()


def test_rtk_guard_no_action_without_rtk_in_patch() -> None:
    """A patch that doesn't carry rtkStatus must not trigger anything."""
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": 2}}
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"battery": 50})
    create_task.assert_not_called()


def test_rtk_guard_pauses_when_below_threshold_while_mowing() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.set_rtk_guard_threshold(THING, 1)
    coord.data = {THING: {"workStatus": 2}}  # mowing
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 0})
    assert create_task.call_count == 1


def test_rtk_guard_does_not_pause_when_above_threshold() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.set_rtk_guard_threshold(THING, 1)
    coord.data = {THING: {"workStatus": 2}}
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 2})  # comfortably above
    create_task.assert_not_called()


def test_rtk_guard_does_not_pause_when_not_mowing() -> None:
    """Only mowing → pause makes sense; docked/error states are ignored."""
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": 5}}  # docked
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 0})
    create_task.assert_not_called()


def test_rtk_guard_resumes_only_when_we_paused() -> None:
    """If the user paused manually, RTK recovery must not auto-resume."""
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": 3}}  # paused
    coord._rtk_guard_active_pause[THING] = False  # we did NOT pause
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 3})  # great fix
    create_task.assert_not_called()


def test_rtk_guard_resumes_when_we_paused_and_rtk_recovers() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": 3}}  # paused
    coord._rtk_guard_active_pause[THING] = True  # we paused earlier
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 3})
    assert create_task.call_count == 1


def test_rtk_guard_ignores_non_numeric_rtk() -> None:
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": 2}}
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": "bad"})
    create_task.assert_not_called()


def test_rtk_guard_no_action_when_work_status_unknown() -> None:
    """If the cached merge doesn't yet contain workStatus, the guard has no
    context to decide on — it must early-return without scheduling."""
    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {}}  # no workStatus
    create_task = _capture_create_task(coord)
    coord.on_mqtt_state(THING, {"rtkStatus": 0})
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_pause_helper_publishes_pause_and_sets_flag() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2}}  # mowing
    await coord._async_rtk_guard_pause(THING, rtk_val=0, threshold=1)
    assert coord._rtk_guard_active_pause[THING] is True
    assert mqtt.async_publish_command.await_count == 1


@pytest.mark.asyncio
async def test_rtk_guard_resume_helper_publishes_resume_and_clears_flag() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 3}}  # paused
    coord._rtk_guard_active_pause[THING] = True
    await coord._async_rtk_guard_resume(THING, rtk_val=3)
    assert coord._rtk_guard_active_pause[THING] is False
    assert mqtt.async_publish_command.await_count == 1


# ---------------------------------------------------------------------------
# async_merge_zones
# ---------------------------------------------------------------------------


_TWO_SQUARES = {
    "goZones": [
        {
            "hashId": "alpha",
            "cutHeight": 40,
            "isEnabled": True,
            "polygon": [{"x": 0.0, "y": 0.0}, {"x": 2.0, "y": 0.0}, {"x": 2.0, "y": 2.0}, {"x": 0.0, "y": 2.0}],
        },
        {
            "hashId": "beta",
            "cutHeight": 50,
            "isEnabled": True,
            "polygon": [{"x": 5.0, "y": 0.0}, {"x": 7.0, "y": 0.0}, {"x": 7.0, "y": 2.0}, {"x": 5.0, "y": 2.0}],
        },
    ],
    "nogoZones": [
        {"hashId": "nogo-a", "parentZoneHashId": "alpha"},
    ],
}


@pytest.mark.asyncio
async def test_async_merge_zones_reshapes_keeper_and_deletes_others() -> None:
    """Client-side merge: reshape the first zone to the union hull (uc=9), delete the rest (uc=8)."""
    from lymow.geometry import merge_zone_polygons
    from lymow.protocol import encode_delete_zone, encode_set_zone_polygon

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _TWO_SQUARES}}
    coord.async_query_map = AsyncMock()
    keeper = await coord.async_merge_zones(THING, ["alpha", "beta"])
    assert keeper == "alpha"
    hull = merge_zone_polygons(_TWO_SQUARES["goZones"][0]["polygon"], _TWO_SQUARES["goZones"][1]["polygon"])
    sent = [c.args for c in mqtt.async_publish_command.await_args_list]
    assert (THING, encode_set_zone_polygon("alpha", hull)) in sent
    assert (THING, encode_delete_zone("beta")) in sent
    coord.async_query_map.assert_awaited_once_with(THING)


@pytest.mark.asyncio
async def test_async_merge_zones_applies_optional_name() -> None:
    from lymow.protocol import encode_rename_zone

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _TWO_SQUARES}}
    coord.async_query_map = AsyncMock()
    await coord.async_merge_zones(THING, ["alpha", "beta"], name="combined")
    sent = [c.args for c in mqtt.async_publish_command.await_args_list]
    assert (THING, encode_rename_zone("alpha", "combined")) in sent


@pytest.mark.asyncio
async def test_async_merge_zones_applies_explicit_cut_height() -> None:
    from lymow.protocol import encode_set_zone_config

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _TWO_SQUARES}}
    coord.async_query_map = AsyncMock()
    await coord.async_merge_zones(THING, ["alpha", "beta"], cut_height_mm=30)
    sent = [c.args for c in mqtt.async_publish_command.await_args_list]
    assert (THING, encode_set_zone_config([{"hashId": "alpha", "cutHeight": 30}])) in sent


@pytest.mark.asyncio
async def test_async_merge_zones_raises_with_fewer_than_two_inputs() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _TWO_SQUARES}}
    with pytest.raises(HomeAssistantError, match="at least 2"):
        await coord.async_merge_zones(THING, ["alpha"])


@pytest.mark.asyncio
async def test_async_merge_zones_raises_when_zone_missing() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _TWO_SQUARES}}
    with pytest.raises(HomeAssistantError, match="not found"):
        await coord.async_merge_zones(THING, ["alpha", "missing"])


@pytest.mark.asyncio
async def test_async_merge_zones_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_merge_zones(THING, ["alpha", "beta"])


@pytest.mark.asyncio
async def test_async_merge_zones_raises_when_no_polygons() -> None:
    """If every input zone has an empty polygon there's nothing to merge."""
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {
        THING: {
            "mapData": {
                "goZones": [
                    {"hashId": "a", "polygon": []},
                    {"hashId": "b", "polygon": []},
                ],
                "nogoZones": [],
            }
        }
    }
    with pytest.raises(HomeAssistantError, match="None of the requested zones have a polygon"):
        await coord.async_merge_zones(THING, ["a", "b"])


@pytest.mark.asyncio
async def test_async_merge_zones_raises_when_geometry_fails() -> None:
    """If the combined polygon vertices can't form a hull, surface the error
    as HomeAssistantError instead of letting ValueError propagate."""
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {
        THING: {
            "mapData": {
                "goZones": [
                    {"hashId": "a", "polygon": [{"x": 0.0, "y": 0.0}]},
                    {"hashId": "b", "polygon": [{"x": 0.0, "y": 0.0}]},
                ],
                "nogoZones": [],
            }
        }
    }
    with pytest.raises(HomeAssistantError, match="Could not merge zones"):
        await coord.async_merge_zones(THING, ["a", "b"])


# ---------------------------------------------------------------------------
# async_pin_and_go (#43)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_pin_and_go_builds_square_and_starts_zone() -> None:
    coord, _, _ = _make_coordinator()
    coord.async_add_zone = AsyncMock(return_value="newhash99")
    coord.async_start_zones = AsyncMock()
    new_id = await coord.async_pin_and_go(THING, 5.0, 3.0, radius_m=2.0, cut_height_mm=35, name="pin")
    assert new_id == "newhash99"
    expected_polygon = [
        {"x": 3.0, "y": 1.0},
        {"x": 7.0, "y": 1.0},
        {"x": 7.0, "y": 5.0},
        {"x": 3.0, "y": 5.0},
    ]
    coord.async_add_zone.assert_awaited_once_with(THING, expected_polygon, name="pin", cut_height_mm=35)
    coord.async_start_zones.assert_awaited_once_with(THING, ["newhash99"])


@pytest.mark.asyncio
async def test_async_pin_and_go_default_radius_is_one_meter() -> None:
    coord, _, _ = _make_coordinator()
    coord.async_add_zone = AsyncMock(return_value="hh")
    coord.async_start_zones = AsyncMock()
    await coord.async_pin_and_go(THING, 0.0, 0.0)
    polygon = coord.async_add_zone.await_args.args[1]
    xs = sorted({p["x"] for p in polygon})
    ys = sorted({p["y"] for p in polygon})
    assert xs == [-1.0, 1.0]
    assert ys == [-1.0, 1.0]


@pytest.mark.asyncio
async def test_async_pin_and_go_rejects_non_positive_radius() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.async_add_zone = AsyncMock()
    with pytest.raises(HomeAssistantError, match="positive"):
        await coord.async_pin_and_go(THING, 0.0, 0.0, radius_m=0)
    coord.async_add_zone.assert_not_awaited()


# ---------------------------------------------------------------------------
# async_split_zone
# ---------------------------------------------------------------------------


_ONE_SQUARE = {
    "goZones": [
        {
            "hashId": "alpha",
            "cutHeight": 45,
            "isEnabled": True,
            "polygon": [{"x": 0.0, "y": 0.0}, {"x": 4.0, "y": 0.0}, {"x": 4.0, "y": 4.0}, {"x": 0.0, "y": 4.0}],
        }
    ],
    "nogoZones": [
        {"hashId": "nogo-a", "parentZoneHashId": "alpha"},
    ],
}


@pytest.mark.asyncio
async def test_async_split_zone_replaces_source_with_two_new_zones() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_ONE_SQUARE)}}
    captured: dict = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    left_id, right_id = await coord.async_split_zone(
        THING, "alpha", {"x": 2.0, "y": -1.0}, {"x": 2.0, "y": 5.0}, names=("west", "east")
    )
    ids_after = {z["hashId"] for z in captured["map"]["goZones"]}
    assert "alpha" not in ids_after
    assert left_id in ids_after and right_id in ids_after
    for zid in (left_id, right_id):
        z = next(zone for zone in captured["map"]["goZones"] if zone["hashId"] == zid)
        assert z["cutHeight"] == 45
        assert z["isEnabled"] is True


@pytest.mark.asyncio
async def test_async_split_zone_cascade_deletes_child_nogo_zones() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_ONE_SQUARE)}}
    captured: dict = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    await coord.async_split_zone(THING, "alpha", {"x": 2.0, "y": -1.0}, {"x": 2.0, "y": 5.0})
    assert captured["map"]["nogoZones"] == []


@pytest.mark.asyncio
async def test_async_split_zone_marks_modified_hashes() -> None:
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_ONE_SQUARE)}}
    captured: dict = {}

    async def _capture(thing, map_data):
        captured["map"] = map_data

    coord.async_sync_map = _capture  # type: ignore[method-assign]
    left_id, right_id = await coord.async_split_zone(THING, "alpha", {"x": 2.0, "y": -1.0}, {"x": 2.0, "y": 5.0})
    modify = captured["map"]["modifyHashs"]
    assert "alpha" in modify and left_id in modify and right_id in modify


@pytest.mark.asyncio
async def test_async_split_zone_raises_when_zone_missing() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _ONE_SQUARE}}
    with pytest.raises(HomeAssistantError, match="not found"):
        await coord.async_split_zone(THING, "missing", {"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0})


@pytest.mark.asyncio
async def test_async_split_zone_raises_when_no_map_data() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    with pytest.raises(HomeAssistantError, match="Map data not yet loaded"):
        await coord.async_split_zone(THING, "alpha", {"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0})


@pytest.mark.asyncio
async def test_async_split_zone_raises_when_geometry_fails() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": _ONE_SQUARE}}
    with pytest.raises(HomeAssistantError, match="Could not split zone"):
        await coord.async_split_zone(THING, "alpha", {"x": -1.0, "y": 10.0}, {"x": 5.0, "y": 10.0})


@pytest.mark.asyncio
async def test_async_split_zone_raises_when_source_polygon_too_small() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": {"goZones": [{"hashId": "alpha", "polygon": [{"x": 0.0, "y": 0.0}]}]}}}
    with pytest.raises(HomeAssistantError, match="no polygon"):
        await coord.async_split_zone(THING, "alpha", {"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0})


@pytest.mark.asyncio
async def test_async_split_zone_retries_on_hash_collision() -> None:
    """The inner _fresh_hash loop retries when token_hex collides with an
    existing zone id. Exercised by forcing one collision on the first call."""
    import copy

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"mapData": copy.deepcopy(_ONE_SQUARE)}}

    async def _noop(thing, map_data):
        pass

    coord.async_sync_map = _noop  # type: ignore[method-assign]

    from unittest.mock import patch as _patch

    # First call hits the existing "alpha", retries → fresh; second call is fresh.
    with _patch("secrets.token_hex", side_effect=["alpha", "leftFresh", "rightFresh"]):
        left_id, right_id = await coord.async_split_zone(THING, "alpha", {"x": 2.0, "y": -1.0}, {"x": 2.0, "y": 5.0})
    assert left_id == "leftFresh"
    assert right_id == "rightFresh"


# ---------------------------------------------------------------------------
# OTA firmware update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_update_data_merges_ota_state() -> None:
    """check_update fields land in coordinator.data via _ota_state."""
    coord, _, api = _make_coordinator(rest_data={"workStatus": 1, "softwareVersion": "v2.1.40"})
    api.check_update = AsyncMock(return_value={"latestVersion": "v2.1.48", "prefix": "", "releaseNote": "Fixes"})
    result = await coord._async_update_data()
    assert result[THING]["latestVersion"] == "v2.1.48"
    assert result[THING]["otaPrefix"] == ""
    assert result[THING]["otaReleaseNote"] == "Fixes"


@pytest.mark.asyncio
async def test_maybe_refresh_ota_throttles_within_interval() -> None:
    """A second refresh within 6 h does not re-hit the endpoint."""
    coord, _, api = _make_coordinator()
    api.check_update = AsyncMock(return_value={"latestVersion": "v2.1.48"})
    await coord._async_update_data()
    await coord._async_update_data()
    assert api.check_update.await_count == 1


@pytest.mark.asyncio
async def test_maybe_refresh_ota_swallows_endpoint_error() -> None:
    """A failing check_update doesn't break the data refresh."""
    coord, _, api = _make_coordinator()
    api.check_update = AsyncMock(side_effect=RuntimeError("boom"))
    result = await coord._async_update_data()
    assert THING in result
    assert "latestVersion" not in result[THING]


@pytest.mark.asyncio
async def test_async_check_firmware_update_publishes_patch() -> None:
    coord, _, api = _make_coordinator()
    api.check_update = AsyncMock(return_value={"latestVersion": "v2.1.48", "prefix": "fw/", "releaseNote": "Note"})
    coord.data = {THING: {"softwareVersion": "v2.1.40"}}
    data = await coord.async_check_firmware_update(THING)
    assert data["latestVersion"] == "v2.1.48"
    assert coord.data[THING]["latestVersion"] == "v2.1.48"
    assert coord.data[THING]["otaPrefix"] == "fw/"


@pytest.mark.asyncio
async def test_async_install_firmware_update_passes_object_key_and_caches_job_id() -> None:
    coord, _, api = _make_coordinator()
    api.create_ota_job = AsyncMock(return_value={"jobId": "JOB-42"})
    coord.data = {THING: {"softwareVersion": "v2.1.40"}}
    job_id = await coord.async_install_firmware_update(THING, "v2.1.48")
    assert job_id == "JOB-42"
    api.create_ota_job.assert_awaited_once_with(THING, "v2.1.48")
    assert coord._ota_state[THING]["otaJobId"] == "JOB-42"
    assert coord.data[THING]["otaJobId"] == "JOB-42"


@pytest.mark.asyncio
async def test_async_install_firmware_update_handles_missing_job_id() -> None:
    coord, _, api = _make_coordinator()
    api.create_ota_job = AsyncMock(return_value={})
    job_id = await coord.async_install_firmware_update(THING, "v2.1.48")
    assert job_id is None
    assert coord._ota_state[THING]["otaJobId"] is None


@pytest.mark.asyncio
async def test_async_get_ota_progress_clears_on_terminal_status() -> None:
    coord, _, api = _make_coordinator()
    coord._ota_state[THING] = {"otaJobId": "JOB-42"}
    coord.data = {THING: {"otaJobId": "JOB-42"}}
    api.get_ota_job_summary = AsyncMock(return_value={"status": "OTA_SUCCESS"})
    result = await coord.async_get_ota_progress(THING, "JOB-42")
    assert result == {"status": "OTA_SUCCESS"}
    assert "otaJobId" not in coord._ota_state[THING]
    assert coord.data[THING]["otaJobId"] is None


@pytest.mark.asyncio
async def test_async_get_ota_progress_keeps_job_id_on_in_progress() -> None:
    coord, _, api = _make_coordinator()
    coord._ota_state[THING] = {"otaJobId": "JOB-42"}
    api.get_ota_job_summary = AsyncMock(return_value={"status": "OTA_IN_PROGRESS"})
    await coord.async_get_ota_progress(THING, "JOB-42")
    assert coord._ota_state[THING]["otaJobId"] == "JOB-42"


@pytest.mark.asyncio
async def test_async_get_ota_progress_clears_on_robot_not_in_wait() -> None:
    """Robot rejected the install — the dead jobId must be cleared so the
    entity returns to "not in progress" without a separate signal."""
    coord, _, api = _make_coordinator()
    coord._ota_state[THING] = {"otaJobId": "JOB-42"}
    coord.data = {THING: {"otaJobId": "JOB-42"}}
    api.get_ota_job_summary = AsyncMock(return_value={"status": "OTA_ROBOT_NOT_IN_WAIT"})
    await coord.async_get_ota_progress(THING, "JOB-42")
    assert "otaJobId" not in coord._ota_state[THING]


@pytest.mark.asyncio
async def test_maybe_poll_ota_progress_swallows_error() -> None:
    coord, _, api = _make_coordinator()
    coord._ota_state[THING] = {"otaJobId": "JOB-42"}
    api.get_ota_job_summary = AsyncMock(side_effect=RuntimeError("network"))
    # Force the OTA refresh to be inside its throttle window so this run
    # only exercises the job-progress poll path.
    from datetime import UTC
    from datetime import datetime as _dt

    coord._last_ota_check[THING] = _dt.now(UTC)
    result = await coord._async_update_data()
    assert THING in result


@pytest.mark.asyncio
async def test_maybe_poll_ota_progress_skips_when_no_job_id() -> None:
    from datetime import UTC
    from datetime import datetime as _dt

    coord, _, api = _make_coordinator()
    coord._last_ota_check[THING] = _dt.now(UTC)
    await coord._async_update_data()
    api.get_ota_job_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_device_patch_noop_when_data_missing() -> None:
    """The publish helper is safe to call before the first coordinator tick."""
    coord, _, _ = _make_coordinator()
    coord.data = None
    coord._publish_device_patch(THING, {"otaJobId": "JOB-42"})
    assert coord.data is None


@pytest.mark.asyncio
async def test_ota_patch_from_check_handles_non_dict() -> None:
    """A non-dict check_update response (e.g. error string) yields no patch."""
    assert LymowCoordinator._ota_patch_from_check("oops") == {}


@pytest.mark.asyncio
async def test_async_check_firmware_update_no_patch_when_response_empty() -> None:
    """An empty check_update response doesn't publish a patch but still
    updates the last-check timestamp."""
    coord, _, api = _make_coordinator()
    api.check_update = AsyncMock(return_value={})
    coord.data = {THING: {"softwareVersion": "v2.1.40"}}
    result = await coord.async_check_firmware_update(THING)
    assert result == {}
    assert THING in coord._last_ota_check
    assert "latestVersion" not in coord.data[THING]


# ---------------------------------------------------------------------------
# BLE manual drive
# ---------------------------------------------------------------------------


def _fake_ble_ctor(created: list):
    def ctor(address):
        c = MagicMock()
        c.address = address
        c.async_drive_for = AsyncMock()
        c.async_disconnect = AsyncMock()
        created.append(c)
        return c

    return ctor


async def test_async_ble_drive_creates_reuses_and_replaces(monkeypatch) -> None:
    coord, _, _ = _make_coordinator()
    created: list = []
    monkeypatch.setattr(sys.modules["lymow.coordinator"], "LymowBleController", _fake_ble_ctor(created))

    await coord.async_ble_drive("AA:BB", 0.3, -0.2, 1.0)
    assert len(created) == 1
    created[0].async_drive_for.assert_awaited_once_with(0.3, -0.2, 1.0)

    # same address reuses the controller
    await coord.async_ble_drive("AA:BB", 0.1, 0.0, 0.5)
    assert len(created) == 1
    assert created[0].async_drive_for.await_count == 2

    # different address drops the old connection and builds a new controller
    await coord.async_ble_drive("CC:DD", 0.0, 0.0, 0.5)
    assert len(created) == 2
    created[0].async_disconnect.assert_awaited_once()


async def test_async_shutdown_disconnects_ble(monkeypatch) -> None:
    coord, _, _ = _make_coordinator()
    created: list = []
    monkeypatch.setattr(sys.modules["lymow.coordinator"], "LymowBleController", _fake_ble_ctor(created))
    await coord.async_ble_drive("AA:BB", 0.1, 0.0, 0.2)
    await coord.async_shutdown()
    created[0].async_disconnect.assert_awaited_once()


async def test_async_shutdown_disconnects_ble_even_if_mqtt_raises(monkeypatch) -> None:
    coord, mqtt, _ = _make_coordinator()
    created: list = []
    monkeypatch.setattr(sys.modules["lymow.coordinator"], "LymowBleController", _fake_ble_ctor(created))
    await coord.async_ble_drive("AA:BB", 0.1, 0.0, 0.2)
    mqtt.disconnect.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError):
        await coord.async_shutdown()
    created[0].async_disconnect.assert_awaited_once()


async def test_async_restore_backup_map_requeries() -> None:
    coord, _, api = _make_coordinator()
    api.restore_backup_map = AsyncMock()
    coord.async_query_map = AsyncMock()
    await coord.async_restore_backup_map(THING, "dev/map/m1.pb")
    api.restore_backup_map.assert_awaited_once_with(THING, "dev/map/m1.pb")
    coord.async_query_map.assert_awaited_once_with(THING)


async def test_async_delete_backup_map_drops_cache() -> None:
    coord, _, api = _make_coordinator()
    api.delete_backup_map = AsyncMock()
    coord._backup_map_cache[THING] = ("t", {})
    await coord.async_delete_backup_map(THING, "k")
    api.delete_backup_map.assert_awaited_once_with("k")
    assert THING not in coord._backup_map_cache


async def test_async_backup_map_publishes_and_drops_cache() -> None:
    from lymow.const import USER_CTRL_FLOOR_BACKUP
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()  # get_backup_map_list returns [] → not full
    coord.hass.async_create_task = _make_task_closer()
    coord._backup_map_cache[THING] = ("t", {})
    await coord.async_backup_map(THING)
    _thing, pb = mqtt.async_publish_command.await_args.args
    assert _first(_decode_fields(pb), 5) == USER_CTRL_FLOOR_BACKUP
    assert THING not in coord._backup_map_cache  # cache invalidated so sensor refreshes
    coord.hass.async_create_task.assert_called_once()  # scheduled the prompt re-poll


async def test_async_backup_map_rejects_when_storage_full() -> None:
    """At the backend's 5-backup cap, fail fast instead of a silent no-op (which
    would make users click again and pile up duplicates)."""
    from homeassistant.exceptions import HomeAssistantError

    coord, mqtt, api = _make_coordinator()
    api.get_backup_map_list = AsyncMock(return_value=[{"map_file": f"m{i}"} for i in range(5)])
    coord.hass.async_create_task = _make_task_closer()
    with pytest.raises(HomeAssistantError, match="full"):
        await coord.async_backup_map(THING)
    mqtt.async_publish_command.assert_not_awaited()


async def test_async_refresh_backups_soon_repolls_and_pushes_updates(monkeypatch) -> None:
    """After a backup, re-poll the list a few times and push each result so the new
    entry surfaces without waiting out the 5-min cache."""
    coord, _, api = _make_coordinator()
    coord.data = {THING: {"battery": 50}}
    coord.async_set_updated_data = MagicMock()
    api.get_backup_map_list = AsyncMock(return_value=[{"map_file": "m1", "backup_time": 1_700_000_000}])

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    await coord._async_refresh_backups_soon(THING)

    from lymow.coordinator import _BACKUP_REFRESH_OFFSETS_S

    assert coord.async_set_updated_data.call_count == len(_BACKUP_REFRESH_OFFSETS_S)
    pushed = coord.async_set_updated_data.call_args[0][0]
    assert pushed[THING]["backupMapCount"] == 1  # new backup surfaced
    assert pushed[THING]["battery"] == 50  # existing fields preserved


async def test_async_rename_backup_map_drops_cache() -> None:
    coord, _, api = _make_coordinator()
    api.rename_backup_map = AsyncMock()
    coord._backup_map_cache[THING] = ("t", {})
    await coord.async_rename_backup_map(THING, "k", "Spring")
    api.rename_backup_map.assert_awaited_once_with("k", "Spring")
    assert THING not in coord._backup_map_cache


async def test_async_rename_device_merges_name() -> None:
    coord, _, api = _make_coordinator()
    api.rename_device = AsyncMock()
    coord.data = {THING: {"deviceName": "old"}}
    coord.async_set_updated_data = MagicMock()
    await coord.async_rename_device(THING, "New Name")
    api.rename_device.assert_awaited_once_with(THING, "New Name")
    sent = coord.async_set_updated_data.call_args.args[0]
    assert sent[THING]["deviceName"] == "New Name"


async def test_async_rename_device_no_data_noop_merge() -> None:
    coord, _, api = _make_coordinator()
    api.rename_device = AsyncMock()
    coord.data = None
    await coord.async_rename_device(THING, "New Name")
    api.rename_device.assert_awaited_once_with(THING, "New Name")


async def test_async_start_video_session_chains_endpoints() -> None:
    coord, _, api = _make_coordinator()
    creds = {"accessKeyId": "AK", "secretAccessKey": "SK", "sessionToken": "ST"}
    api.start_video_session = AsyncMock(
        return_value={"channelARN": "arn:test", "region": "eu-west-1", "credentials": creds}
    )
    api.get_signaling_channel_endpoint = AsyncMock(return_value={"WSS": "wss://v", "HTTPS": "https://r"})
    api.get_ice_server_config = AsyncMock(
        return_value=[{"Uris": ["turn:x"], "Username": "u", "Password": "p"}, "junk-non-dict"]
    )
    api.viewer_client_id = MagicMock(return_value="ha-lymow_abcd_userId_S")
    api.presign_signaling_url = MagicMock(return_value="wss://v/?X-Amz-Signature=sig")
    result = await coord.async_start_video_session(THING)
    api.get_signaling_channel_endpoint.assert_awaited_once_with("arn:test", creds, region="eu-west-1")
    api.get_ice_server_config.assert_awaited_once_with("arn:test", "https://r", creds, region="eu-west-1")
    assert result["signalingEndpoints"] == {"WSS": "wss://v", "HTTPS": "https://r"}
    assert result["iceServers"] == [{"Uris": ["turn:x"], "Username": "u", "Password": "p"}, "junk-non-dict"]
    # Turnkey browser-viewer config (non-dict ICE entries are filtered out)
    api.presign_signaling_url.assert_called_once_with(
        "wss://v", "arn:test", "ha-lymow_abcd_userId_S", creds, region="eu-west-1"
    )
    assert result["viewerClientId"] == "ha-lymow_abcd_userId_S"
    assert result["viewerWssUrl"] == "wss://v/?X-Amz-Signature=sig"
    assert result["webrtcIceServers"] == [{"urls": ["turn:x"], "username": "u", "credential": "p"}]


async def test_async_start_video_session_no_creds_returns_base() -> None:
    coord, _, api = _make_coordinator()
    api.start_video_session = AsyncMock(return_value={"channelARN": "arn:test"})
    api.get_signaling_channel_endpoint = AsyncMock()
    result = await coord.async_start_video_session(THING)
    api.get_signaling_channel_endpoint.assert_not_awaited()
    assert result == {"channelARN": "arn:test"}


async def test_async_start_video_session_endpoint_failure_is_nonfatal() -> None:
    coord, _, api = _make_coordinator()
    creds = {"accessKeyId": "AK", "secretAccessKey": "SK", "sessionToken": "ST"}
    api.start_video_session = AsyncMock(
        return_value={"channelARN": "arn:test", "region": "eu-west-1", "credentials": creds}
    )
    api.get_signaling_channel_endpoint = AsyncMock(side_effect=RuntimeError("boom"))
    result = await coord.async_start_video_session(THING)
    assert result["channelARN"] == "arn:test"  # base session still returned
    assert "signalingEndpoints" not in result


async def test_async_start_video_session_non_dict_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, _, api = _make_coordinator()
    api.start_video_session = AsyncMock(return_value="unexpected")
    with pytest.raises(HomeAssistantError):
        await coord.async_start_video_session(THING)


@pytest.mark.asyncio
async def test_async_rename_zone_publishes_modify_zone_info() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_rename_zone(THING, "wsmjco1T", "Front lawn")
    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == 9  # USER_CTRL_MODIFY_ZONE_INFO
    bi = _decode_fields(_first(_decode_fields(_first(_decode_fields(_first(f, 12)), 1)), 1))
    assert _first(bi, 2).decode() == "Front lawn"


@pytest.mark.asyncio
async def test_async_rename_zone_updates_optimistic_cache_when_present() -> None:
    """When coordinator already holds the map, rename should update the cached name immediately."""
    coord, _mqtt, _ = _make_coordinator()
    coord.data = {
        THING: {
            "mapData": {
                "goZones": [{"hashId": "wsmjco1T", "name": "Front garden"}],
                "nogoZones": [],
            },
        }
    }
    await coord.async_rename_zone(THING, "wsmjco1T", "Front lawn")
    assert coord.data[THING]["mapData"]["goZones"][0]["name"] == "Front lawn"


@pytest.mark.asyncio
async def test_async_rename_nogo_zone_targets_nogo_field_and_updates_cache() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    # Seed coordinator cache with one nogo zone so we can verify the optimistic rename.
    coord.data = {
        THING: {
            "mapData": {"goZones": [], "nogoZones": [{"hashId": "ngabcdef", "name": "Old"}]},
        }
    }
    await coord.async_rename_nogo_zone(THING, "ngabcdef", "Flower bed")

    thing, pb = mqtt.async_publish_command.await_args.args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == 9  # USER_CTRL_MODIFY_ZONE_INFO
    pb_map = _decode_fields(_first(f, 12))
    # The nogo rename must land in PbMap.nogoZones (field 2), not goZones (field 1)
    assert _first(pb_map, 1) is None
    bi = _decode_fields(_first(_decode_fields(_first(pb_map, 2)), 1))
    assert _first(bi, 2).decode() == "Flower bed"
    assert _first(bi, 3).decode() == "ngabcdef"
    # Coordinator cache updated optimistically
    assert coord.data[THING]["mapData"]["nogoZones"][0]["name"] == "Flower bed"


@pytest.mark.asyncio
async def test_async_rename_channel_stores_override_and_updates_cache() -> None:
    coord, _, _ = _make_coordinator()
    coord.data = {
        THING: {
            "mapData": {
                "goZones": [],
                "nogoZones": [],
                "channels": [{"hashId": "a1b2c3d4", "isDockingChannel": False}],
            },
        }
    }
    await coord.async_rename_channel(THING, "a1b2c3d4", "Back passage")
    assert coord._channel_name_overrides[THING]["a1b2c3d4"] == "Back passage"
    assert coord.data[THING]["mapData"]["channels"][0]["name"] == "Back passage"


@pytest.mark.asyncio
async def test_on_mqtt_state_applies_channel_name_overrides_on_map_update() -> None:
    coord, _, _ = _make_coordinator()
    coord._channel_name_overrides[THING] = {"a1b2c3d4": "Back passage"}
    coord.data = {THING: {}}
    patch = {"mapData": {"channels": [{"hashId": "a1b2c3d4", "isDockingChannel": False}]}}
    coord.on_mqtt_state(THING, patch)
    stored = coord.data[THING]["mapData"]["channels"][0]
    assert stored["name"] == "Back passage"


@pytest.mark.asyncio
async def test_on_mqtt_state_skips_channel_override_when_none_registered() -> None:
    coord, _, _ = _make_coordinator()
    # No overrides registered — patch must pass through unchanged.
    coord.data = {THING: {}}
    patch = {"mapData": {"channels": [{"hashId": "a1b2c3d4", "isDockingChannel": False}]}}
    coord.on_mqtt_state(THING, patch)
    stored = coord.data[THING]["mapData"]["channels"][0]
    assert "name" not in stored


@pytest.mark.asyncio
async def test_async_clear_schedules_sends_empty_then_queries() -> None:
    coord, mqtt, _ = _make_coordinator()
    await coord.async_clear_schedules(THING)
    # first publish = clear (empty schedule field), then a query-schedules
    first_pb = mqtt.async_publish_command.await_args_list[0].args[1]
    assert first_pb.hex() == "10315a00"
    assert mqtt.async_publish_command.await_count == 2  # clear + query


def test_wire_entries_from_cached_preserves_utc_and_refills_point() -> None:
    from lymow.coordinator import _wire_entries_from_cached

    cached = [
        {
            "dayOfWeek": [6],
            "hour": 13,
            "minute": 16,
            "isRepeated": True,
            "isDisabled": False,
            "zones": ["z1"],
            "id": 42,
            "timeZone": 2,
        }
    ]
    map_data = {"goZones": [{"hashId": "z1", "name": "Lawn", "innerPoint": {"x": 1.5, "y": 2.5}}]}
    [entry] = _wire_entries_from_cached(cached, map_data)
    assert entry["hour"] == 13 and entry["minute"] == 16  # UTC preserved, no re-conversion
    assert entry["id"] == 42 and entry["timeZone"] == 2
    assert entry["zones"][0] == {"hashId": "z1", "name": "Lawn", "point": {"x": 1.5, "y": 2.5}}


@pytest.mark.asyncio
async def test_async_add_schedule_appends_and_preserves_existing() -> None:
    from lymow.protocol import _all, _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    coord.hass.config.time_zone = "UTC"
    coord.data = {
        THING: {
            "schedules": [
                {
                    "dayOfWeek": [6],
                    "hour": 13,
                    "minute": 16,
                    "isDisabled": False,
                    "zones": ["z1"],
                    "id": 42,
                    "timeZone": 0,
                }
            ],
            "mapData": {"goZones": [{"hashId": "z1", "name": "L", "innerPoint": {"x": 0.0, "y": 0.0}}]},
        }
    }
    await coord.async_add_schedule(
        THING, hour=8, minute=5, day_of_week=[1], zones=["z1"], is_repeated=True, is_disabled=False
    )
    pb = mqtt.async_publish_command.await_args_list[0].args[1]
    tasks = _all(_decode_fields(_first(_decode_fields(pb), 11)), 1)
    assert len(tasks) == 2  # existing + new
    # new entry (second) carries the new time; UTC tz => hour unchanged
    new = _decode_fields(tasks[1])
    assert _first(new, 2) == 8 and _first(new, 3) == 5


@pytest.mark.asyncio
async def test_async_delete_schedule_drops_one_keeps_rest() -> None:
    from lymow.protocol import _all, _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    coord.data = {
        THING: {
            "schedules": [
                {"hour": 7, "minute": 0, "zones": [], "id": 1},
                {"hour": 8, "minute": 0, "zones": [], "id": 2},
            ],
            "mapData": {},
        }
    }
    await coord.async_delete_schedule(THING, 1)
    pb = mqtt.async_publish_command.await_args_list[0].args[1]
    tasks = _all(_decode_fields(_first(_decode_fields(pb), 11)), 1)
    assert len(tasks) == 1
    assert _first(_decode_fields(tasks[0]), 6) == 2  # id 2 survives


@pytest.mark.asyncio
async def test_async_delete_last_schedule_sends_clear() -> None:
    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"schedules": [{"hour": 7, "minute": 0, "zones": [], "id": 1}], "mapData": {}}}
    await coord.async_delete_schedule(THING, 1)
    assert mqtt.async_publish_command.await_args_list[0].args[1].hex() == "10315a00"  # clear


@pytest.mark.asyncio
async def test_async_delete_schedule_unknown_id_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"schedules": [{"hour": 7, "minute": 0, "zones": [], "id": 1}], "mapData": {}}}
    with pytest.raises(HomeAssistantError, match="No schedule with id 9"):
        await coord.async_delete_schedule(THING, 9)
    mqtt.async_publish_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_toggle_schedule_sets_disabled_flag() -> None:
    from lymow.protocol import _all, _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    coord.data = {
        THING: {
            "schedules": [
                {"hour": 7, "minute": 0, "zones": [], "id": 1, "isDisabled": False},
                {"hour": 8, "minute": 0, "zones": [], "id": 2, "isDisabled": False},
            ],
            "mapData": {},
        }
    }
    await coord.async_toggle_schedule(THING, 2, disabled=True)
    pb = mqtt.async_publish_command.await_args_list[0].args[1]
    tasks = _all(_decode_fields(_first(_decode_fields(pb), 11)), 1)
    by_id = {_first(_decode_fields(t), 6): _decode_fields(t) for t in tasks}
    assert _first(by_id[2], 8) == 1  # id 2 isDisabled set
    assert _first(by_id[1], 8) is None  # id 1 still enabled (f8 omitted)


@pytest.mark.asyncio
async def test_async_toggle_schedule_unknown_id_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError

    coord, mqtt, _ = _make_coordinator()
    coord.data = {THING: {"schedules": [{"hour": 7, "minute": 0, "zones": [], "id": 1}], "mapData": {}}}
    with pytest.raises(HomeAssistantError, match="No schedule with id 5"):
        await coord.async_toggle_schedule(THING, 5, disabled=True)
    mqtt.async_publish_command.assert_not_awaited()


def test_build_schedule_entries_converts_utc_and_fills_zone() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from lymow.coordinator import build_schedule_entries

    now = datetime(2026, 5, 22, 12, 0, tzinfo=ZoneInfo("Europe/Stockholm"))  # CEST = UTC+2
    map_data = {"goZones": [{"hashId": "z1", "name": "Lawn", "innerPoint": {"x": 1.0, "y": 2.0}, "cutHeight": 55}]}
    specs = [{"hour": 9, "minute": 30, "dayOfWeek": [5], "zones": ["z1"], "isRepeated": True}]
    [entry] = build_schedule_entries(specs, map_data, now)
    assert entry["hour"] == 7  # 09:30 CEST -> 07:30 UTC
    assert entry["minute"] == 30
    assert entry["timeZone"] == 2  # UTC+2 offset hours
    assert entry["isRepeated"] is True
    assert entry["zones"][0] == {"hashId": "z1", "name": "Lawn", "point": {"x": 1.0, "y": 2.0}}
    assert entry["config"]["cutHeight"] == 55
    assert entry["config"]["hashId"] == "z1"
    assert "id" in entry


def test_build_schedule_entries_shifts_day_when_utc_crosses_midnight() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from lymow.coordinator import build_schedule_entries

    # 00:30 Monday CEST(UTC+2) -> 22:30 Sunday UTC: day must shift Mon(1) -> Sun(0).
    now = datetime(2026, 5, 22, 12, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
    specs = [{"hour": 0, "minute": 30, "dayOfWeek": [1], "zones": []}]
    [entry] = build_schedule_entries(specs, {}, now)
    assert entry["hour"] == 22
    assert entry["minute"] == 30
    assert entry["dayOfWeek"] == [0]  # Monday shifted back to Sunday in UTC


def test_build_schedule_entries_negative_half_hour_offset_truncates() -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from lymow.coordinator import build_schedule_entries

    # America/St_Johns is UTC-03:30 (UTC-02:30 in DST) — must truncate toward zero.
    now = datetime(2026, 1, 15, 12, 0, tzinfo=ZoneInfo("America/St_Johns"))  # -03:30 in winter
    [entry] = build_schedule_entries([{"hour": 9, "minute": 0, "zones": []}], {}, now)
    assert entry["timeZone"] == -3  # not -4


def test_build_schedule_entries_unknown_zone_defaults() -> None:
    from datetime import datetime, timezone

    from lymow.coordinator import build_schedule_entries

    now = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)
    [entry] = build_schedule_entries([{"hour": 8, "minute": 0, "zones": ["missing"]}], {}, now)
    assert entry["zones"][0] == {"hashId": "missing", "name": "", "point": {"x": 0.0, "y": 0.0}}
    assert entry["config"]["cutHeight"] == 40  # default when zone/cut unknown
    assert entry["timeZone"] == 0


def test_build_schedule_entries_no_zones_has_no_config() -> None:
    from datetime import datetime, timezone

    from lymow.coordinator import build_schedule_entries

    now = datetime(2026, 5, 22, 8, 0, tzinfo=timezone.utc)
    [entry] = build_schedule_entries([{"hour": 8, "minute": 0, "zones": []}], {}, now)
    assert "config" not in entry
    assert entry["zones"] == []


async def test_async_set_schedules_publishes_then_queries() -> None:
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    coord.hass.config.time_zone = "UTC"
    await coord.async_set_schedules(THING, [{"hour": 9, "minute": 30, "zones": []}])
    assert mqtt.async_publish_command.await_count == 2  # set + query
    thing, pb = mqtt.async_publish_command.await_args_list[0].args
    assert thing == THING
    f = _decode_fields(pb)
    assert isinstance(_first(f, 11), bytes)  # PbSchedules in field 11
    task = _decode_fields(_first(_decode_fields(_first(f, 11)), 1))
    assert _first(task, 2) == 9  # hour (UTC == local under UTC tz)


async def test_async_delete_channel_sends_command_then_queries_map() -> None:
    from lymow.const import USER_CTRL_DELETE_CHANNEL
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_delete_channel(THING, "ch000001")
    assert mqtt.async_publish_command.await_count == 2  # delete + query-map
    thing, pb = mqtt.async_publish_command.await_args_list[0].args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == USER_CTRL_DELETE_CHANNEL
    channel = _decode_fields(_first(_decode_fields(_first(f, 12)), 3))
    assert _first(channel, 1) == b"ch000001"


async def test_async_delete_nogo_zone_sends_command_then_queries_map() -> None:
    from lymow.const import USER_CTRL_CLEAR_ZONE
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    await coord.async_delete_nogo_zone(THING, "ng1")
    assert mqtt.async_publish_command.await_count == 2  # delete + query-map
    _thing, pb = mqtt.async_publish_command.await_args_list[0].args
    f = _decode_fields(pb)
    assert _first(f, 5) == USER_CTRL_CLEAR_ZONE
    zone = _decode_fields(_first(_decode_fields(_first(f, 12)), 2))  # nogoZones (f2) -> PbZone
    assert _first(_decode_fields(_first(zone, 1)), 3) == b"ng1"  # basicInfo.hashId


@pytest.mark.asyncio
async def test_async_sync_map_publishes_sync_then_queries_map() -> None:
    from lymow.const import USER_CTRL_SYNC_MAP
    from lymow.protocol import _decode_fields, _first

    coord, mqtt, _ = _make_coordinator()
    map_data: dict[str, Any] = {"goZones": [], "nogoZones": [], "channels": []}
    await coord.async_sync_map(THING, map_data)
    # sync command + query-map — robot does not re-broadcast map after SYNC_MAP on its own
    assert mqtt.async_publish_command.await_count == 2
    thing, pb = mqtt.async_publish_command.await_args_list[0].args
    assert thing == THING
    f = _decode_fields(pb)
    assert _first(f, 5) == USER_CTRL_SYNC_MAP


# ---------------------------------------------------------------------------
# Backup map preview (thumbnail geometry)
# ---------------------------------------------------------------------------


def test_downsample_polygon_thins_and_passes_through() -> None:
    coord, _, _ = _make_coordinator()
    pts = [{"x": i, "y": i} for i in range(200)]
    out = coord._downsample_polygon(pts, max_points=50)
    assert len(out) == 50
    short = [{"x": 1, "y": 1}, {"x": 2, "y": 2}]
    assert coord._downsample_polygon(short) == short
    assert coord._downsample_polygon("not-a-list") == []


_FAKE_DECODE = {
    "goZones": [{"hashId": "z1", "isEnabled": True, "polygon": [{"x": i, "y": i} for i in range(120)]}],
    "nogoZones": [{"polygon": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 0}]}],
    "channels": [{"polygon": [{"x": 5, "y": 5}, {"x": 6, "y": 6}]}],
}


@pytest.mark.asyncio
async def test_fetch_backup_preview_decodes_downsamples_and_caches(monkeypatch) -> None:
    coord, _, api = _make_coordinator()
    api.download_backup_map = AsyncMock(return_value=b"rawbytes")
    monkeypatch.setattr(sys.modules["lymow.coordinator"], "decode_backup_map", lambda raw: _FAKE_DECODE)
    preview = await coord._fetch_backup_preview(THING, "k1")
    assert preview["goZones"][0]["hashId"] == "z1"
    assert len(preview["goZones"][0]["polygon"]) <= 60  # downsampled
    assert preview["nogoZones"] and preview["channels"]
    # Cached by key — a second call must not re-download.
    api.download_backup_map.reset_mock()
    again = await coord._fetch_backup_preview(THING, "k1")
    assert again is preview
    api.download_backup_map.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_backup_preview_none_on_download_error() -> None:
    coord, _, api = _make_coordinator()
    api.download_backup_map = AsyncMock(side_effect=RuntimeError("boom"))
    assert await coord._fetch_backup_preview(THING, "k2") is None


@pytest.mark.asyncio
async def test_fetch_backup_preview_none_when_no_bytes() -> None:
    coord, _, api = _make_coordinator()
    api.download_backup_map = AsyncMock(return_value=None)
    assert await coord._fetch_backup_preview(THING, "k3") is None


@pytest.mark.asyncio
async def test_fetch_backup_preview_none_on_decode_error(monkeypatch) -> None:
    coord, _, api = _make_coordinator()
    api.download_backup_map = AsyncMock(return_value=b"x")

    def _boom(_raw):
        raise ValueError("bad pb")

    monkeypatch.setattr(sys.modules["lymow.coordinator"], "decode_backup_map", _boom)
    assert await coord._fetch_backup_preview(THING, "k4") is None


@pytest.mark.asyncio
async def test_fetch_backup_map_fields_attaches_preview(monkeypatch) -> None:
    coord, _, api = _make_coordinator()
    api.get_backup_map_list = AsyncMock(
        return_value=[
            {"map_file": "dev/map/a.pb", "name": "", "backup_time": 100},
            {"map_file": "dev/map/b.pb", "name": "", "backup_time": 90},
        ]
    )
    # First backup decodes; second download fails → no preview key on it.
    calls = {"n": 0}

    async def _dl(_thing, key):
        calls["n"] += 1
        return b"raw" if key.endswith("a.pb") else None

    api.download_backup_map = _dl
    monkeypatch.setattr(sys.modules["lymow.coordinator"], "decode_backup_map", lambda raw: _FAKE_DECODE)
    fields = await coord._fetch_backup_map_fields(THING)
    lst = fields["backupMapList"]
    assert lst[0]["preview"]["goZones"][0]["hashId"] == "z1"
    assert "preview" not in lst[1]  # download returned None → skipped


# ---------------------------------------------------------------------------
# Edit Boundary (drive-record): start (userCtrl=10) + complete (userCtrl=29)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_start_edit_boundary_publishes_modify_zone_start() -> None:
    from lymow.protocol import encode_modify_zone_start

    coord, mqtt, _ = _make_coordinator()
    await coord.async_start_edit_boundary(THING, "KX1kGyat")
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_modify_zone_start("KX1kGyat"))


@pytest.mark.asyncio
async def test_async_complete_edit_boundary_publishes_and_requeries_map() -> None:
    from lymow.protocol import encode_complete_zone_partition

    coord, mqtt, _ = _make_coordinator()
    coord.async_query_map = AsyncMock()
    await coord.async_complete_edit_boundary(THING)
    mqtt.async_publish_command.assert_awaited_once_with(THING, encode_complete_zone_partition())
    coord.async_query_map.assert_awaited_once_with(THING)
