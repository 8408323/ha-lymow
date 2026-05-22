"""Tests for lymow __init__.py — async_setup_entry and async_unload_entry.

All external dependencies (HA runtime, auth, API, MQTT, coordinator) are
replaced with lightweight mocks so no real network or HA stack is needed.

This file follows the same pattern as test_coordinator.py: HA modules are
stubbed into sys.modules before the integration package is imported, so the
tests run under the uv Python 3.13 environment where HA is not installed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Minimal HA stubs so __init__.py can import without the HA stack
# ---------------------------------------------------------------------------
import importlib.util
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")


def _make_ha_stubs() -> None:
    """Create just enough HA module stubs to import lymow/__init__.py."""
    ha = sys.modules.get("homeassistant") or types.ModuleType("homeassistant")
    sys.modules.setdefault("homeassistant", ha)

    # homeassistant.components (namespace)
    ha_comp = types.ModuleType("homeassistant.components")
    sys.modules.setdefault("homeassistant.components", ha_comp)

    # homeassistant.components.frontend — only needs add_extra_js_url
    ha_frontend = types.ModuleType("homeassistant.components.frontend")
    ha_frontend.add_extra_js_url = MagicMock()
    sys.modules.setdefault("homeassistant.components.frontend", ha_frontend)

    # homeassistant.components.http — only needs StaticPathConfig
    ha_http = types.ModuleType("homeassistant.components.http")

    class _StaticPathConfig:
        def __init__(self, url_path, path, cache_headers=True):
            self.url_path = url_path
            self.path = path
            self.cache_headers = cache_headers

    ha_http.StaticPathConfig = _StaticPathConfig
    sys.modules.setdefault("homeassistant.components.http", ha_http)

    # homeassistant.config_entries
    ha_ce = types.ModuleType("homeassistant.config_entries")
    ha_ce.ConfigEntry = object
    sys.modules.setdefault("homeassistant.config_entries", ha_ce)

    # homeassistant.const — needs Platform enum-like
    ha_const = sys.modules.get("homeassistant.const") or types.ModuleType("homeassistant.const")
    if not hasattr(ha_const, "Platform"):

        class _Platform:
            BINARY_SENSOR = "binary_sensor"
            BUTTON = "button"
            CAMERA = "camera"
            DEVICE_TRACKER = "device_tracker"
            LAWN_MOWER = "lawn_mower"
            NUMBER = "number"
            SENSOR = "sensor"
            SWITCH = "switch"
            UPDATE = "update"

        ha_const.Platform = _Platform
    sys.modules.setdefault("homeassistant.const", ha_const)

    # homeassistant.core
    ha_core = sys.modules.get("homeassistant.core") or types.ModuleType("homeassistant.core")
    if not hasattr(ha_core, "HomeAssistant"):
        ha_core.HomeAssistant = object
    sys.modules.setdefault("homeassistant.core", ha_core)

    # homeassistant.helpers
    ha_helpers = sys.modules.get("homeassistant.helpers") or types.ModuleType("homeassistant.helpers")
    sys.modules.setdefault("homeassistant.helpers", ha_helpers)

    # homeassistant.helpers.aiohttp_client
    ha_ac = types.ModuleType("homeassistant.helpers.aiohttp_client")
    ha_ac.async_get_clientsession = MagicMock()
    sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_ac)


def _stub_lymow_coordinator() -> None:
    """Register a stub for lymow.coordinator (it imports HA, conftest skips it)."""
    if "lymow.coordinator" in sys.modules:
        return
    mod = types.ModuleType("lymow.coordinator")
    mod.LymowCoordinator = MagicMock  # type: ignore[attr-defined]
    sys.modules["lymow.coordinator"] = mod


def _load_lymow_init():
    """Load custom_components/lymow/__init__.py as the 'lymow' package module."""
    if "lymow" in sys.modules:
        return sys.modules["lymow"]
    path = os.path.join(_BASE, "__init__.py")
    spec = importlib.util.spec_from_file_location(
        "lymow",
        path,
        submodule_search_locations=[_BASE],
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec to handle any circular references
    sys.modules["lymow"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_make_ha_stubs()
_stub_lymow_coordinator()
_lymow = _load_lymow_init()

async_setup_entry = _lymow.async_setup_entry  # noqa: E402
async_unload_entry = _lymow.async_unload_entry  # noqa: E402
_WWW_REGISTERED_KEY = _lymow._WWW_REGISTERED_KEY  # noqa: E402

# Const values loaded by the conftest  # noqa: E402
from lymow.const import CONF_PASSWORD, CONF_REGION, CONF_USERNAME, DOMAIN  # noqa: E402

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_entry(
    username: str = "user@example.com",
    password: str = "secret",
    region: str | None = "eu-west-1",
    entry_id: str = "eid-1",
) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    data = {CONF_USERNAME: username, CONF_PASSWORD: password}
    if region is not None:
        data[CONF_REGION] = region
    entry.data = data
    return entry


def _make_tokens(region: str = "eu-west-1") -> dict:
    return {"region": region, "IdToken": "id-tok", "AccessToken": "access-tok"}


def _make_creds() -> dict:
    return {
        "identity_id": "eu-west-1:abc123",
        "credentials": {
            "AccessKeyId": "AKIAIOSFODNN7EXAMPLE",
            "SecretKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "session-tok",
        },
    }


def _make_hass(www_registered: bool = False) -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    if www_registered:
        hass.data[_WWW_REGISTERED_KEY] = True
    hass.http.async_register_static_paths = AsyncMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    # Close the scheduled coroutine instead of leaking it (avoids
    # "coroutine was never awaited" warnings); we don't run it here.
    hass.async_create_task = MagicMock(side_effect=lambda coro, *a, **k: coro.close())
    return hass


def _make_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.async_config_entry_first_refresh = AsyncMock()
    coord.async_query_all_maps = AsyncMock()
    coord.async_query_all_schedules = AsyncMock()
    coord.async_shutdown = AsyncMock()
    return coord


def _make_auth(tokens: dict, creds: dict) -> MagicMock:
    auth = MagicMock()
    auth.login_region = AsyncMock(return_value=tokens)
    auth.login = AsyncMock(return_value=tokens)
    auth.get_aws_credentials = AsyncMock(return_value=creds)
    return auth


def _make_client(devices: list | None = None) -> MagicMock:
    client = MagicMock()
    client.get_devices = AsyncMock(return_value=devices or [{"deviceThingName": "thing-1"}])
    return client


def _make_mqtt() -> MagicMock:
    mqtt = MagicMock()
    mqtt.connect = AsyncMock()
    return mqtt


# ── async_setup_entry ──────────────────────────────────────────────────────────


async def test_async_setup_entry_returns_true_with_stored_region() -> None:
    hass = _make_hass()
    entry = _make_entry(region="eu-west-1")
    tokens = _make_tokens()
    creds = _make_creds()
    auth = _make_auth(tokens, creds)
    client = _make_client()
    mqtt = _make_mqtt()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient", return_value=mqtt),
        patch("lymow.LymowCoordinator", return_value=coord),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    auth.login_region.assert_awaited_once()
    auth.login.assert_not_awaited()
    coord.async_config_entry_first_refresh.assert_awaited_once()
    coord.async_query_all_maps.assert_awaited_once()
    coord.async_query_all_schedules.assert_awaited_once()
    mqtt.connect.assert_awaited_once()


async def test_async_setup_entry_uses_login_when_no_stored_region() -> None:
    """When CONF_REGION is not in entry.data, auth.login() (auto-detect) is called."""
    hass = _make_hass()
    entry = _make_entry(region=None)
    tokens = _make_tokens()
    creds = _make_creds()
    auth = _make_auth(tokens, creds)
    client = _make_client()
    mqtt = _make_mqtt()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient", return_value=mqtt),
        patch("lymow.LymowCoordinator", return_value=coord),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    auth.login.assert_awaited_once()
    auth.login_region.assert_not_awaited()


async def test_async_setup_entry_stores_coordinator_in_hass_data() -> None:
    hass = _make_hass()
    entry = _make_entry(entry_id="eid-1")
    auth = _make_auth(_make_tokens(), _make_creds())
    client = _make_client()
    mqtt = _make_mqtt()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient", return_value=mqtt),
        patch("lymow.LymowCoordinator", return_value=coord),
    ):
        await async_setup_entry(hass, entry)

    assert hass.data[DOMAIN]["eid-1"] is coord


async def test_async_setup_entry_registers_www_once() -> None:
    """Static paths are registered the first time and skipped on subsequent calls."""
    hass = _make_hass(www_registered=False)
    entry = _make_entry()
    auth = _make_auth(_make_tokens(), _make_creds())
    client = _make_client()
    mqtt = _make_mqtt()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient", return_value=mqtt),
        patch("lymow.LymowCoordinator", return_value=coord),
    ):
        await async_setup_entry(hass, entry)

    # www was not yet registered, so async_register_static_paths should have been called
    hass.http.async_register_static_paths.assert_awaited_once()
    assert hass.data[_WWW_REGISTERED_KEY] is True


async def test_async_setup_entry_skips_www_when_already_registered() -> None:
    """If the www key is already set, static paths are not registered again."""
    hass = _make_hass(www_registered=True)
    entry = _make_entry()
    auth = _make_auth(_make_tokens(), _make_creds())
    client = _make_client()
    mqtt = _make_mqtt()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient", return_value=mqtt),
        patch("lymow.LymowCoordinator", return_value=coord),
    ):
        await async_setup_entry(hass, entry)

    hass.http.async_register_static_paths.assert_not_awaited()


async def test_async_setup_entry_raises_on_missing_iot_host() -> None:
    """ValueError is raised when no IoT endpoint is configured for the region."""
    hass = _make_hass()
    entry = _make_entry(region="eu-west-1")
    tokens = _make_tokens(region="eu-west-1")
    creds = _make_creds()
    auth = _make_auth(tokens, creds)
    client = _make_client()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient"),
        patch("lymow.LymowCoordinator", return_value=coord),
        # Patch REGION_CONFIG to return a config without iot_host
        patch(
            "lymow.REGION_CONFIG",
            {"eu-west-1": {}},  # no iot_host key
        ),
        pytest.raises(ValueError, match="No IoT endpoint configured"),
    ):
        await async_setup_entry(hass, entry)


async def test_async_setup_entry_passes_session_token_to_mqtt() -> None:
    """SessionToken from AWS creds is forwarded to mqtt_client.connect."""
    hass = _make_hass()
    entry = _make_entry()
    creds = _make_creds()
    auth = _make_auth(_make_tokens(), creds)
    client = _make_client()
    mqtt = _make_mqtt()
    coord = _make_coordinator()

    with (
        patch("lymow.async_get_clientsession", return_value=MagicMock()),
        patch("lymow.LymowAuth", return_value=auth),
        patch("lymow.LymowApiClient", return_value=client),
        patch("lymow.LymowMqttClient", return_value=mqtt),
        patch("lymow.LymowCoordinator", return_value=coord),
    ):
        await async_setup_entry(hass, entry)

    connect_kwargs = mqtt.connect.call_args.kwargs
    assert connect_kwargs["session_token"] == "session-tok"


# ── async_unload_entry ─────────────────────────────────────────────────────────


async def test_async_unload_entry_shuts_down_coordinator() -> None:
    coord = _make_coordinator()
    hass = _make_hass()
    hass.data = {DOMAIN: {"eid-1": coord}}
    entry = _make_entry(entry_id="eid-1")

    result = await async_unload_entry(hass, entry)

    assert result is True
    coord.async_shutdown.assert_awaited_once()
    assert "eid-1" not in hass.data[DOMAIN]


async def test_async_unload_entry_does_not_shutdown_on_failure() -> None:
    coord = _make_coordinator()
    hass = _make_hass()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    hass.data = {DOMAIN: {"eid-1": coord}}
    entry = _make_entry(entry_id="eid-1")

    result = await async_unload_entry(hass, entry)

    assert result is False
    coord.async_shutdown.assert_not_awaited()
    assert "eid-1" in hass.data[DOMAIN]


# ── dashboard auto-creation ──────────────────────────────────────────────────

_build_dashboard_config = _lymow._build_dashboard_config
_resolve_dashboard_entities = _lymow._resolve_dashboard_entities
_async_create_dashboard = _lymow._async_create_dashboard
_DASHBOARD_URL_PATH = _lymow._DASHBOARD_URL_PATH
_DASHBOARD_CREATED_KEY = _lymow._DASHBOARD_CREATED_KEY


class _FakeRegistry:
    def __init__(self, ids: dict, disabled: set | None = None) -> None:
        self._ids = ids  # unique_id -> entity_id
        self._disabled = disabled or set()  # disabled entity_ids

    def async_get_entity_id(self, domain, platform, unique_id):
        return self._ids.get(unique_id)

    def async_get(self, entity_id):
        if entity_id not in self._ids.values():
            return None
        e = MagicMock()
        e.disabled_by = "user" if entity_id in self._disabled else None
        return e


def test_build_dashboard_config_full() -> None:
    ents = {k: f"x.{k}" for k in _lymow._DASHBOARD_ENTITY_KEYS}
    cfg = _build_dashboard_config(ents)
    titles = [v["title"] for v in cfg["views"]]
    assert titles == ["Map", "Sensors"]
    map_card = cfg["views"][0]["cards"][0]
    assert map_card["type"] == "custom:lymow-map-card"
    assert map_card["entity"] == "x.map"
    assert map_card["mower_entity"] == "x.mower"


def test_build_dashboard_config_map_only_no_mower_entity() -> None:
    cfg = _build_dashboard_config({"map": "sensor.m"})
    assert [v["title"] for v in cfg["views"]] == ["Map"]
    card = cfg["views"][0]["cards"][0]
    assert "mower_entity" not in card  # mower unresolved → omitted


def test_build_dashboard_config_drops_empty_views() -> None:
    # Only history sensors → no Map view, only Sensors view with one card.
    cfg = _build_dashboard_config({"last_mow": "sensor.lm"})
    assert [v["title"] for v in cfg["views"]] == ["Sensors"]
    assert len(cfg["views"][0]["cards"]) == 1


def test_build_dashboard_config_empty() -> None:
    assert _build_dashboard_config({}) == {"views": []}


def test_resolve_dashboard_entities_filters_disabled_and_missing() -> None:
    hass = MagicMock()
    # map + mower registered; mower disabled; battery missing entirely.
    hass._entity_registry = _FakeRegistry(
        ids={"thing_map": "sensor.dev_map", "thing": "lawn_mower.dev"},
        disabled={"lawn_mower.dev"},
    )
    resolved = _resolve_dashboard_entities(hass, "thing")
    assert resolved == {"map": "sensor.dev_map"}  # mower disabled, rest missing


def _lovelace_hass(reg, devices=None, existing=False):
    hass = MagicMock()
    hass.data = {}
    hass._entity_registry = reg
    store = MagicMock()
    store.async_save = AsyncMock()
    dashboards = {_DASHBOARD_URL_PATH: store} if existing else {}
    collection = MagicMock()

    async def _create(item):
        dashboards[_DASHBOARD_URL_PATH] = store

    collection.async_create_item = AsyncMock(side_effect=_create)
    hass.data["lovelace"] = {"dashboards": dashboards, "dashboards_collection": collection}
    hass._store = store
    hass._collection = collection
    return hass


async def test_async_create_dashboard_happy_path() -> None:
    reg = _FakeRegistry({"t_map": "sensor.t_map", "t": "lawn_mower.t"})
    hass = _lovelace_hass(reg)
    await _async_create_dashboard(hass, [{"deviceThingName": "t"}])
    hass._collection.async_create_item.assert_awaited_once()
    hass._store.async_save.assert_awaited_once()
    assert hass.data[_DASHBOARD_CREATED_KEY] is True


async def test_async_create_dashboard_skips_empty_devices() -> None:
    hass = _lovelace_hass(_FakeRegistry({}))
    await _async_create_dashboard(hass, [])
    hass._collection.async_create_item.assert_not_awaited()


async def test_async_create_dashboard_skips_without_lovelace() -> None:
    hass = MagicMock()
    hass.data = {}
    await _async_create_dashboard(hass, [{"deviceThingName": "t"}])
    assert _DASHBOARD_CREATED_KEY not in hass.data


async def test_async_create_dashboard_skips_when_exists() -> None:
    reg = _FakeRegistry({"t_map": "sensor.t_map"})
    hass = _lovelace_hass(reg, existing=True)
    await _async_create_dashboard(hass, [{"deviceThingName": "t"}])
    hass._collection.async_create_item.assert_not_awaited()


async def test_async_create_dashboard_skips_when_no_map_or_mower() -> None:
    # Only a disabled-by-default sensor resolves → nothing meaningful to show.
    reg = _FakeRegistry({"t_battery": "sensor.t_battery"})
    hass = _lovelace_hass(reg)
    await _async_create_dashboard(hass, [{"deviceThingName": "t"}])
    hass._collection.async_create_item.assert_not_awaited()
    assert _DASHBOARD_CREATED_KEY not in hass.data


def test_card_url_falls_back_when_manifest_unreadable() -> None:
    with patch.object(_lymow.json, "loads", side_effect=ValueError("bad")):
        url = _lymow._card_url()
    assert url.endswith("v=0")


async def test_async_create_dashboard_skips_when_no_collection() -> None:
    hass = MagicMock()
    hass.data = {"lovelace": {"dashboards": {}, "dashboards_collection": None}}
    hass._entity_registry = _FakeRegistry({"t_map": "sensor.t_map"})
    await _async_create_dashboard(hass, [{"deviceThingName": "t"}])
    assert _DASHBOARD_CREATED_KEY not in hass.data


async def test_async_create_dashboard_swallows_errors() -> None:
    reg = _FakeRegistry({"t_map": "sensor.t_map", "t": "lawn_mower.t"})
    hass = _lovelace_hass(reg)
    hass._collection.async_create_item = AsyncMock(side_effect=RuntimeError("boom"))
    # Must not raise; flag stays unset so a later setup can retry.
    await _async_create_dashboard(hass, [{"deviceThingName": "t"}])
    assert _DASHBOARD_CREATED_KEY not in hass.data
