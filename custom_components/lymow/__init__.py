"""Lymow integration."""

from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.components import persistent_notification
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import websocket_api
from .api import LymowApiClient
from .auth import LymowAuth, LymowAuthConnectionError, LymowAuthError
from .const import (
    AUTH_METHOD_GOOGLE,
    AUTH_METHOD_PASSWORD,
    CONF_AUTH_METHOD,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_USERNAME,
    DOMAIN,
    REGION_AUTO,
    REGION_CONFIG,
)
from .coordinator import LymowCoordinator
from .mqtt import LymowMqttClient

_LOGGER = logging.getLogger(__name__)
_WWW_REGISTERED_KEY = f"{DOMAIN}_www_registered"
_WWW_SERVED_KEY = f"{DOMAIN}_www_served"
_PANEL_REGISTERED_KEY = f"{DOMAIN}_panel_registered"
_PANEL_URL_PATH = "lymow"
# Dashboard that versions before the panel auto-created; see _remove_legacy_lovelace.
_LEGACY_DASHBOARD = "lymow-mower"


def _read_version() -> str:
    try:
        manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
        return manifest.get("version", "0")
    except Exception:
        return "0"


# Read once at import (HA imports custom integrations in an executor) — reading it
# per call from async_setup_entry is blocking I/O inside the event loop.
_VERSION = _read_version()


def _card_url(name: str = "lymow-panel.js") -> str:
    """Return a www/ asset URL with the integration version as cache buster."""
    return f"/custom_components/{DOMAIN}/{name}?v={_VERSION}"


PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.UPDATE,
]


def _lovelace_attr(lovelace: Any, name: str) -> Any:
    """Read a field from hass.data["lovelace"] (a dataclass on current HA, a dict on older)."""
    if isinstance(lovelace, dict):
        return lovelace.get(name)
    return getattr(lovelace, name, None)


async def _remove_legacy_lovelace(hass: HomeAssistant) -> None:
    """Clean up after the old Lovelace cards, which the React panel replaced.

    Their resources are deleted (otherwise every dashboard 404s on them). The
    dashboard older versions auto-created at /lymow-mower can't be removed from
    here (and may have been customised), so the user gets a one-off notice."""
    try:
        lovelace = hass.data.get("lovelace")
        if lovelace is None:
            return
        resources = _lovelace_attr(lovelace, "resources")
        if resources is not None:
            await resources.async_load()
            for item in list(resources.async_items()):
                if f"/custom_components/{DOMAIN}/" in item.get("url", ""):
                    await resources.async_delete_item(item["id"])
        if _LEGACY_DASHBOARD in (_lovelace_attr(lovelace, "dashboards") or {}):
            persistent_notification.async_create(
                hass,
                "Lymow now has its own **Lymow** page in the sidebar, so the old auto-created "
                "dashboard is no longer needed and its cards were removed. You can delete it under "
                "Settings → Dashboards (it's the one at `/lymow-mower`).",
                title="Lymow: old dashboard can be removed",
                notification_id=f"{DOMAIN}_legacy_dashboard",
            )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not clean up legacy Lymow Lovelace items (non-fatal)", exc_info=True)


async def _async_init_panel(hass: HomeAssistant) -> None:
    websocket_api.async_register(hass)
    www_path = Path(__file__).parent / "www"
    if www_path.is_dir():
        await hass.http.async_register_static_paths(
            [StaticPathConfig(url_path=f"/custom_components/{DOMAIN}", path=str(www_path), cache_headers=False)]
        )
        await _remove_legacy_lovelace(hass)
        # Remember that the panel's JS is actually being served this run, so we
        # only ever register the panel when its module_url resolves.
        hass.data[_WWW_SERVED_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Once per HA run: serve www/ (the panel bundle), register the panel's
    # websocket commands, and remove resources left by the old Lovelace cards.
    # Entries set up concurrently at startup share one future: the first runs the
    # init, the others wait for (and fail with) its result. A failure releases the
    # claim so a later setup retries.
    if (init := hass.data.get(_WWW_REGISTERED_KEY)) is not None:
        await init
    else:
        init = hass.data[_WWW_REGISTERED_KEY] = asyncio.get_running_loop().create_future()
        try:
            await _async_init_panel(hass)
        except BaseException as err:
            hass.data.pop(_WWW_REGISTERED_KEY, None)
            init.set_exception(err)
            init.exception()  # retrieved here; waiters still receive it
            raise
        init.set_result(None)

    session = async_get_clientsession(hass)
    auth = LymowAuth(session)
    auth_method = entry.data.get(CONF_AUTH_METHOD, AUTH_METHOD_PASSWORD)
    tokens, region = await _async_authenticate_entry(auth, entry, auth_method)

    refresh_token = tokens.get("RefreshToken")
    if isinstance(refresh_token, str):
        _update_refresh_token(hass, entry, refresh_token)

    creds = await auth.get_aws_credentials(tokens["IdToken"], region)
    aws = creds["credentials"]

    client = LymowApiClient(
        session=session,
        access_token=tokens["AccessToken"],
        region=region,
        identity_id=creds["identity_id"],
    )
    # Seed the temporary AWS credentials so S3-signed REST calls (backup maps,
    # KVS) work from the first poll; the coordinator refreshes them before expiry.
    client.update_aws_credentials(aws["AccessKeyId"], aws["SecretKey"], aws.get("SessionToken"))

    devices = await client.get_devices()
    things = [d["deviceThingName"] for d in devices]

    cfg = REGION_CONFIG[region]
    iot_host = cfg.get("iot_host")
    if not iot_host:
        raise ValueError(f"No IoT endpoint configured for region {region}")

    mqtt_client = LymowMqttClient(
        host=iot_host,
        region=region,
        on_state=lambda thing, patch: coordinator.on_mqtt_state(thing, patch),
        on_online=lambda thing, online: coordinator.on_mqtt_online(thing, online),
    )

    coordinator = LymowCoordinator(hass, client, mqtt_client, devices)
    # Give the coordinator what it needs to refresh tokens + AWS creds before they
    # expire — otherwise the access token lapses (~24 h) and every poll 401s.
    coordinator.set_auth_context(
        auth,
        auth_method,
        entry.data.get(CONF_USERNAME),
        entry.data.get(CONF_PASSWORD),
        region,
        tokens,
        creds,
        lambda token: _update_refresh_token(hass, entry, token),
    )
    await coordinator.async_config_entry_first_refresh()

    await mqtt_client.connect(
        things=things,
        access_key=aws["AccessKeyId"],
        secret_key=aws["SecretKey"],
        session_token=aws.get("SessionToken"),
    )

    # Proactively request map + schedule + config data so zone, schedule and
    # settings entities populate without waiting for the user to trigger a query.
    # This runs after connect() so the publishes aren't dropped — the per-poll
    # startup gate can't query reliably because the first poll precedes connect.
    await coordinator.async_query_all_maps()
    await coordinator.async_query_all_schedules()
    await coordinator.async_query_all_robot_configs()

    _LOGGER.debug("Lymow setup complete: %d device(s) in region %s", len(devices), region)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    websocket_api.notify_coordinators_changed(hass)

    # Reload the entry when options change so edits (e.g. the camera RTSP
    # path/port) take effect without a manual reload.
    entry.async_on_unload(entry.add_update_listener(partial(_async_reload_entry, options=dict(entry.options))))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the sidebar panel here — only once setup has succeeded (so a failed
    # setup leaves no orphan panel) and only when the JS is served. Running on every
    # successful setup means a reload re-registers the panel unload removed.
    if hass.data.get(_WWW_SERVED_KEY):
        await _async_register_panel(hass)

    return True


async def _async_authenticate_entry(
    auth: LymowAuth,
    entry: ConfigEntry,
    auth_method: str,
) -> tuple[dict[str, Any], str]:
    """Restore a config entry's Cognito session using its configured method."""
    stored_region = entry.data.get(CONF_REGION)
    refresh_token = entry.data.get("refresh_token")

    if auth_method == AUTH_METHOD_GOOGLE:
        if not isinstance(stored_region, str) or stored_region == REGION_AUTO:
            raise ConfigEntryAuthFailed("Google OAuth region is missing")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ConfigEntryAuthFailed("Google OAuth refresh token is missing")
        try:
            tokens = await auth.refresh_oauth_tokens(refresh_token=refresh_token, region=stored_region)
        except LymowAuthConnectionError as exc:
            raise ConfigEntryNotReady(f"Google OAuth token refresh failed: {exc}") from exc
        except LymowAuthError as exc:
            raise ConfigEntryAuthFailed("Google OAuth credentials require reauthentication") from exc
        return tokens, stored_region

    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    if not isinstance(username, str) or not isinstance(password, str):
        raise ConfigEntryAuthFailed("Lymow password credentials are missing")

    try:
        if isinstance(stored_region, str) and stored_region != REGION_AUTO:
            if isinstance(refresh_token, str) and refresh_token:
                try:
                    tokens = await auth.refresh_tokens(refresh_token, stored_region)
                except Exception:  # noqa: BLE001
                    tokens = await auth.login_region(username, password, stored_region)
                else:
                    tokens["RefreshToken"] = tokens.get("RefreshToken") or refresh_token
                    tokens["region"] = stored_region
            else:
                tokens = await auth.login_region(username, password, stored_region)
        else:
            tokens = await auth.login(username, password)
    except (LymowAuthConnectionError, aiohttp.ClientError, TimeoutError) as exc:
        raise ConfigEntryNotReady(f"Lymow login failed: {exc}") from exc
    except LymowAuthError as exc:
        raise ConfigEntryAuthFailed("Lymow password was rejected") from exc
    return tokens, tokens["region"]


def _update_refresh_token(hass: HomeAssistant, entry: ConfigEntry, refresh_token: str) -> None:
    """Persist a rotated refresh token without changing other entry data."""
    if refresh_token != entry.data.get("refresh_token"):
        hass.config_entries.async_update_entry(entry, data={**entry.data, "refresh_token": refresh_token})


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the full-page Lymow custom panel in the sidebar if not already registered."""
    if hass.data.get(_PANEL_REGISTERED_KEY):
        return
    try:
        from homeassistant.components import panel_custom

        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=_PANEL_URL_PATH,
            webcomponent_name="lymow-panel",
            module_url=_card_url("lymow-panel.js"),
            sidebar_title="Lymow",
            sidebar_icon="mdi:robot-mower",
            require_admin=False,
            embed_iframe=False,
        )
        hass.data[_PANEL_REGISTERED_KEY] = True
    except ValueError:
        # The url_path is already taken by a panel we didn't register (e.g. user
        # YAML). Don't claim ownership — otherwise unload would remove it.
        _LOGGER.debug("Lymow panel url_path %s already in use; not registering", _PANEL_URL_PATH)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not register Lymow panel (non-fatal)", exc_info=True)


def _remove_panel(hass: HomeAssistant) -> None:
    """Remove the Lymow sidebar panel when the last config entry unloads."""
    if not hass.data.get(_PANEL_REGISTERED_KEY):
        return
    try:
        from homeassistant.components import frontend

        frontend.async_remove_panel(hass, _PANEL_URL_PATH)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not remove Lymow panel (non-fatal)", exc_info=True)
    finally:
        hass.data.pop(_PANEL_REGISTERED_KEY, None)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry, options: dict[str, Any] | None = None) -> None:
    """Reload the config entry when its options change.

    Data-only updates (a rotated refresh token being persisted) also fire this
    listener; those must not tear down MQTT and every platform."""
    if entry.options == options:
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LymowCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        websocket_api.notify_coordinators_changed(hass)
        # Drop the sidebar panel only when the last Lymow entry is gone.
        if not hass.data.get(DOMAIN):
            _remove_panel(hass)
    return unload_ok
