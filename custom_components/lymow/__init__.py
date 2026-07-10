"""Lymow integration."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import LymowApiClient
from .auth import LymowAuth
from .const import CONF_PASSWORD, CONF_REGION, CONF_USERNAME, DOMAIN, REGION_CONFIG
from .coordinator import LymowCoordinator
from .mqtt import LymowMqttClient

_LOGGER = logging.getLogger(__name__)
_WWW_REGISTERED_KEY = f"{DOMAIN}_www_registered"
_DASHBOARD_CREATED_KEY = f"{DOMAIN}_dashboard_created"


def _card_url(name: str = "lymow-map-card.js") -> str:
    """Return a card URL with the integration version as cache buster."""
    try:
        manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
        version = manifest.get("version", "0")
    except Exception:
        version = "0"
    return f"/custom_components/{DOMAIN}/{name}?v={version}"


_DASHBOARD_URL_PATH = "lymow-mower"

# Dashboard entities, keyed by a logical name → (HA platform domain, unique_id
# suffix appended to the device thing-name). Resolved to real entity_ids via the
# entity registry at dashboard-build time, since HA slugifies entity_ids from the
# device name (not the thing-name) and several keys differ from their slug.
_DASHBOARD_ENTITY_KEYS: dict[str, tuple[str, str]] = {
    "map": ("sensor", "_map"),
    "mower": ("lawn_mower", ""),
    "battery": ("sensor", "_battery"),
    "mow_progress": ("sensor", "_mow_progress"),
    "connectivity": ("sensor", "_connectivity"),
    "firmware": ("sensor", "_firmware"),
    "last_mow": ("sensor", "_last_clean_at"),
    "last_mow_area": ("sensor", "_last_clean_area"),
    "last_mow_duration": ("sensor", "_last_clean_duration"),
    "total_mow_sessions": ("sensor", "_clean_history_count"),
    "total_mowed_area": ("sensor", "_total_area_m2"),
    "camera": ("camera", "_camera"),
}


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
    coordinator.set_auth_context(auth, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD], region, tokens, creds)
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Create the Lymow dashboard on first setup so the map card is immediately
    # visible without any manual Lovelace configuration.
    if not hass.data.get(_DASHBOARD_CREATED_KEY):
        hass.async_create_task(
            _async_create_dashboard(hass, devices),
            eager_start=False,
        )

    return True


def _resolve_dashboard_entities(hass: HomeAssistant, thing_name: str) -> dict[str, str]:
    """Map each logical dashboard entity to its real, enabled entity_id.

    Resolves via the entity registry (entity_ids are slugified from the device
    name, not the thing-name, and some keys differ from their slug). Disabled or
    unregistered entities are omitted so the dashboard never points at them.
    """
    ent_reg = er.async_get(hass)
    resolved: dict[str, str] = {}
    for logical, (domain, suffix) in _DASHBOARD_ENTITY_KEYS.items():
        entity_id = ent_reg.async_get_entity_id(domain, DOMAIN, f"{thing_name}{suffix}")
        if entity_id is None:
            continue
        registry_entry = ent_reg.async_get(entity_id)
        if registry_entry is None or registry_entry.disabled_by is not None:
            continue
        resolved[logical] = entity_id
    return resolved


async def _async_create_dashboard(hass: HomeAssistant, devices: list[dict]) -> None:
    """Create a Lymow sidebar dashboard with the map card if it doesn't exist yet."""
    try:
        if not devices:
            return
        lovelace = hass.data.get("lovelace")
        if lovelace is None:
            return
        if _DASHBOARD_URL_PATH in lovelace.get("dashboards", {}):
            return  # Already exists (e.g. after a reload)
        collection = lovelace.get("dashboards_collection")
        if collection is None:
            return

        entities = _resolve_dashboard_entities(hass, devices[0]["deviceThingName"])
        if "map" not in entities and "mower" not in entities:
            return  # Nothing meaningful to show yet (entities not registered).
        config = _build_dashboard_config(entities)

        await collection.async_create_item(
            {
                "url_path": _DASHBOARD_URL_PATH,
                "mode": "storage",
                "title": "Lymow",
                "icon": "mdi:robot-mower",
                "show_in_sidebar": True,
                "require_admin": False,
            }
        )
        dashboard_store = lovelace.get("dashboards", {}).get(_DASHBOARD_URL_PATH)
        if dashboard_store and hasattr(dashboard_store, "async_save"):
            await dashboard_store.async_save(config)
        # Mark created only after success, so a transient failure (e.g. Lovelace
        # not loaded yet) doesn't permanently block a later retry.
        hass.data[_DASHBOARD_CREATED_KEY] = True
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Could not auto-create Lymow dashboard (non-fatal)", exc_info=True)


def _build_dashboard_config(entities: dict[str, str]) -> ConfigType:
    """Return a Lovelace config for the Lymow dashboard from resolved entity_ids.

    ``entities`` maps logical names (see ``_DASHBOARD_ENTITY_KEYS``) to real
    entity_ids; missing/disabled ones are simply absent. Cards with no available
    entities are dropped, and so are views left with no cards.
    """

    def pick(*names: str) -> list[str]:
        return [entities[n] for n in names if n in entities]

    map_cards: list[ConfigType] = []
    if "map" in entities:
        card: ConfigType = {"type": "custom:lymow-map-card", "entity": entities["map"], "title": "Lymow Map"}
        if "mower" in entities:
            card["mower_entity"] = entities["mower"]
        map_cards.append(card)
    if status := pick("mower", "battery", "mow_progress", "connectivity"):
        map_cards.append({"type": "entities", "title": "Mower status", "entities": status})

    sensor_cards: list[ConfigType] = []
    if history := pick(
        "last_mow", "last_mow_area", "last_mow_duration", "mow_progress", "total_mow_sessions", "total_mowed_area"
    ):
        sensor_cards.append({"type": "entities", "title": "Mow history", "entities": history})
    if conn := pick("connectivity", "firmware", "battery"):
        sensor_cards.append({"type": "entities", "title": "Connectivity", "entities": conn})

    drive_cards: list[ConfigType] = []
    if "mower" in entities:
        drive_card: ConfigType = {
            "type": "custom:lymow-drive-card",
            "mower_entity": entities["mower"],
            "title": "Drive",
        }
        if "camera" in entities:
            drive_card["camera_entity"] = entities["camera"]
        drive_cards.append(drive_card)

    schedule_cards: list[ConfigType] = []
    if "mower" in entities:
        schedule_cards.append({"type": "custom:lymow-schedule-card", "mower_entity": entities["mower"]})

    backup_cards: list[ConfigType] = []
    if "mower" in entities:
        backup_cards.append({"type": "custom:lymow-backup-card", "mower_entity": entities["mower"]})

    advanced_cards: list[ConfigType] = []
    if "mower" in entities:
        advanced_cards.append({"type": "custom:lymow-settings-card", "mower_entity": entities["mower"]})

    views: list[ConfigType] = []
    if map_cards:
        views.append({"title": "Map", "path": "map", "icon": "mdi:map", "cards": map_cards})
    if drive_cards:
        views.append({"title": "Drive", "path": "drive", "icon": "mdi:gamepad-variant", "cards": drive_cards})
    if schedule_cards:
        views.append({"title": "Schedules", "path": "schedules", "icon": "mdi:clock-outline", "cards": schedule_cards})
    if backup_cards:
        views.append({"title": "Backups", "path": "backups", "icon": "mdi:database-arrow-up", "cards": backup_cards})
    if advanced_cards:
        views.append({"title": "Advanced", "path": "advanced", "icon": "mdi:cog-outline", "cards": advanced_cards})
    if sensor_cards:
        views.append({"title": "Sensors", "path": "sensors", "icon": "mdi:gauge", "cards": sensor_cards})
    return {"views": views}


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LymowCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
