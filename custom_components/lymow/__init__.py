"""Lymow integration."""

from __future__ import annotations

import logging

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

PLATFORMS = [Platform.LAWN_MOWER, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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

    # Proactively request map data so zone entities populate without waiting
    # for the user to trigger a map query manually.
    await coordinator.async_query_all_maps()

    _LOGGER.debug("Lymow setup complete: %d device(s) in region %s", len(devices), region)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator: LymowCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
