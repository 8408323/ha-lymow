"""Lymow integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LymowApiClient
from .auth import LymowAuth, LymowAuthError
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


def _card_url(name: str = "lymow-map-card.js") -> str:
    """Return a card URL with the integration version as cache buster."""
    try:
        manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
        version = manifest.get("version", "0")
    except Exception:
        version = "0"
    return f"/custom_components/{DOMAIN}/{name}?v={version}"


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


async def _ensure_lovelace_resources(hass: HomeAssistant) -> None:
    """Register card JS files as Lovelace resources, updating stale version URLs.

    Checks each expected JS file by base name. If an entry already exists
    with a different ?v= query string (old version), it is updated in-place
    so only one copy is registered per card. This prevents double-loading
    which causes 'custom element already defined' config errors.
    """
    try:
        from homeassistant.components.lovelace.resources import ResourceStorageCollection

        lovelace = hass.data.get("lovelace")
        if lovelace is None:
            return
        resources: ResourceStorageCollection = lovelace.get("resources")
        if resources is None:
            return
        await resources.async_load()
        # Build a map of base JS filename → (resource_id, current_url)
        base_to_item: dict[str, tuple[str, str]] = {}
        for item in resources.async_items():
            url: str = item.get("url", "")
            # Strip query string to get base path
            base = url.split("?")[0]
            if f"/custom_components/{DOMAIN}/" in base:
                base_to_item[base] = (item["id"], url)

        for js in (
            "lymow-map-card.js",
            "lymow-camera-card.js",
            "lymow-control-card.js",
            "lymow-drive-card.js",
            "lymow-schedule-card.js",
            "lymow-backup-card.js",
            "lymow-settings-card.js",
        ):
            wanted_url = _card_url(js)
            base_path = wanted_url.split("?")[0]
            if base_path in base_to_item:
                res_id, current_url = base_to_item[base_path]
                if current_url != wanted_url:
                    # Version changed — update the existing entry
                    await resources.async_update_item(res_id, {"res_type": "module", "url": wanted_url})
            else:
                await resources.async_create_item({"res_type": "module", "url": wanted_url})
    except Exception:  # noqa: BLE001
        pass  # Non-fatal; add_extra_js_url is the fallback


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Register www/ static path and inject the Lovelace card once per HA run.
    # add_extra_js_url() makes HA load the module on every Lovelace page so
    # users never need to add the resource manually in the UI.
    if not hass.data.get(_WWW_REGISTERED_KEY):
        www_path = Path(__file__).parent / "www"
        if www_path.is_dir():
            await hass.http.async_register_static_paths(
                [StaticPathConfig(url_path=f"/custom_components/{DOMAIN}", path=str(www_path), cache_headers=False)]
            )
            # Use Lovelace resources (not add_extra_js_url) as the sole loader.
            # add_extra_js_url + Lovelace resources both fire on every page load,
            # causing duplicate customElements.define() calls → config errors.
            await _ensure_lovelace_resources(hass)
            # Remember that the panel's JS is actually being served this run, so we
            # only ever register the panel when its module_url resolves.
            hass.data[_WWW_SERVED_KEY] = True
        hass.data[_WWW_REGISTERED_KEY] = True

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

    # Reload the entry when options change so edits (e.g. the camera RTSP
    # path/port) take effect without a manual reload.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

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
        except LymowAuthError as exc:
            raise ConfigEntryAuthFailed("Google OAuth credentials require reauthentication") from exc
        return tokens, stored_region

    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)
    if not isinstance(username, str) or not isinstance(password, str):
        raise ConfigEntryAuthFailed("Lymow password credentials are missing")

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


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LymowCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
        # Drop the sidebar panel only when the last Lymow entry is gone.
        if not hass.data.get(DOMAIN):
            _remove_panel(hass)
    return unload_ok
