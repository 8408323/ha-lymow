"""Lymow integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import LymowApiClient
from .auth import LymowAuth
from .const import CONF_PASSWORD, CONF_REGION, CONF_USERNAME, DOMAIN, REGION_CONFIG
from .coordinator import LymowCoordinator
from .mqtt import LymowMqttClient

_LOGGER = logging.getLogger(__name__)
_WWW_REGISTERED_KEY = f"{DOMAIN}_www_registered"
_DASHBOARD_CREATED_KEY = f"{DOMAIN}_dashboard_created"

def _card_url() -> str:
    """Return card URL with integration version as cache buster."""
    try:
        manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
        version = manifest.get("version", "0")
    except Exception:
        version = "0"
    return f"/custom_components/{DOMAIN}/lymow-map-card.js?v={version}"

_DASHBOARD_URL_PATH = "lymow-mower"
_DASHBOARD_URL_PATH = "lymow-mower"


PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.LAWN_MOWER,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


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
            add_extra_js_url(hass, _card_url())
        hass.data[_WWW_REGISTERED_KEY] = True

    session = async_get_clientsession(hass)
    auth = LymowAuth(session)

    # Use stored region if available (set during config flow), else auto-detect.
    stored_region: str | None = entry.data.get(CONF_REGION)
    if stored_region:
        tokens = await auth.login_region(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD], stored_region)
    else:
        tokens = await auth.login(entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])

    region = tokens["region"]

    creds = await auth.get_aws_credentials(tokens["IdToken"], region)
    aws = creds["credentials"]

    client = LymowApiClient(
        session=session,
        access_token=tokens["AccessToken"],
        region=region,
        identity_id=creds["identity_id"],
    )

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
    await coordinator.async_config_entry_first_refresh()

    await mqtt_client.connect(
        things=things,
        access_key=aws["AccessKeyId"],
        secret_key=aws["SecretKey"],
        session_token=aws.get("SessionToken"),
    )

    # Proactively request map + schedule data so zone and schedule entities
    # populate without waiting for the user to trigger a query manually.
    await coordinator.async_query_all_maps()
    await coordinator.async_query_all_schedules()

    _LOGGER.debug("Lymow setup complete: %d device(s) in region %s", len(devices), region)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Create the Lymow dashboard on first setup so the map card is immediately
    # visible without any manual Lovelace configuration.
    if not hass.data.get(_DASHBOARD_CREATED_KEY):
        hass.data[_DASHBOARD_CREATED_KEY] = True
        hass.async_create_task(
            _async_create_dashboard(hass, devices),
            eager_start=False,
        )

    return True


async def _async_create_dashboard(hass: HomeAssistant, devices: list[dict]) -> None:
    """Create a Lymow sidebar dashboard with the map card if it doesn't exist yet."""
    try:
        lovelace = hass.data.get("lovelace")
        if lovelace is None:
            return

        dashboards = lovelace.get("dashboards", {})
        if _DASHBOARD_URL_PATH in dashboards:
            return  # Already exists (e.g. after a reload)

        # Create the dashboard entry via the Lovelace storage collection.
        collection = lovelace.get("dashboards_collection")
        if collection is None:
            return

        await collection.async_create_item({
            "url_path": _DASHBOARD_URL_PATH,
            "mode": "storage",
            "title": "Lymow",
            "icon": "mdi:robot-mower",
            "show_in_sidebar": True,
            "require_admin": False,
        })

        # Build a view config using the entity IDs derived from the first device.
        if not devices:
            return
        thing = devices[0]["deviceThingName"].lower()
        config = _build_dashboard_config(thing)

        dashboard_store = lovelace.get("dashboards", {}).get(_DASHBOARD_URL_PATH)
        if dashboard_store and hasattr(dashboard_store, "async_save"):
            await dashboard_store.async_save(config)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not auto-create Lymow dashboard (non-fatal)", exc_info=True)


def _build_dashboard_config(thing: str) -> dict:
    """Return a Lovelace config dict for the Lymow dashboard."""
    return {
        "views": [
            {
                "title": "Map",
                "path": "map",
                "icon": "mdi:map",
                "cards": [
                    {
                        "type": "custom:lymow-map-card",
                        "entity": f"sensor.{thing}_map",
                        "mower_entity": f"lawn_mower.{thing}",
                        "title": "Lymow Map",
                    },
                    {
                        "type": "entities",
                        "title": "Mower status",
                        "entities": [
                            f"lawn_mower.{thing}",
                            f"sensor.{thing}_battery",
                            f"sensor.{thing}_mow_progress",
                            f"sensor.{thing}_connectivity",
                        ],
                    },
                ],
            },
            {
                "title": "Sensors",
                "path": "sensors",
                "icon": "mdi:gauge",
                "cards": [
                    {
                        "type": "entities",
                        "title": "Mow history",
                        "entities": [
                            f"sensor.{thing}_last_mow",
                            f"sensor.{thing}_last_mow_area",
                            f"sensor.{thing}_last_mow_duration",
                            f"sensor.{thing}_mow_progress",
                            f"sensor.{thing}_total_mow_sessions",
                            f"sensor.{thing}_total_mowed_area",
                        ],
                    },
                    {
                        "type": "entities",
                        "title": "Connectivity",
                        "entities": [
                            f"sensor.{thing}_connectivity",
                            f"sensor.{thing}_firmware_version",
                            f"sensor.{thing}_battery",
                        ],
                    },
                ],
            },
        ]
    }


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LymowCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
