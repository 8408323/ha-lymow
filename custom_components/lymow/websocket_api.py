"""Websocket commands backing the Lymow panel.

``lymow/devices`` lists every mower with its entities keyed by unique-id suffix,
so the panel never guesses entity_id slugs. ``lymow/subscribe`` streams one
mower's non-entity data (map geometry, live pose, schedules, backups), pushing a
fresh snapshot whenever the coordinator updates and the snapshot changed.
Actions go through the regular ``lymow.*`` services (also over the websocket).
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .sensor import _schedule_to_local, map_payload


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_devices)
    websocket_api.async_register_command(hass, ws_subscribe)


def _coordinators(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.get(DOMAIN, {})


def _find(hass: HomeAssistant, thing: str) -> Any | None:
    for coordinator in _coordinators(hass).values():
        if any(d.get("deviceThingName") == thing for d in coordinator.devices):
            return coordinator
    return None


def snapshot(coordinator: Any, thing: str) -> dict[str, Any]:
    """Everything the panel needs for one mower that isn't an entity state."""
    data = (coordinator.data or {}).get(thing) or {}
    return {
        "thing": thing,
        "map": map_payload(data),
        "schedules": [_schedule_to_local(s) for s in data.get("schedules") or []],
        "backups": data.get("backupMapList") or [],
        "online": data.get("deviceState") != "offline",
    }


@websocket_api.websocket_command({vol.Required("type"): "lymow/devices"})
@callback
def ws_devices(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    devices = []
    for entry_id, coordinator in _coordinators(hass).items():
        reg_entries = er.async_entries_for_config_entry(registry, entry_id)
        for device in coordinator.devices:
            thing = device["deviceThingName"]
            entities: dict[str, str] = {}
            for ent in reg_entries:
                uid = ent.unique_id or ""
                if uid == thing:
                    entities["mower"] = ent.entity_id
                elif uid.startswith(f"{thing}_"):
                    entities[uid[len(thing) + 1 :]] = ent.entity_id
            dev = dev_reg.async_get_device(identifiers={(DOMAIN, thing)})
            devices.append(
                {
                    "entry_id": entry_id,
                    "thing": thing,
                    "device_id": dev.id if dev else None,
                    "name": (dev and (dev.name_by_user or dev.name)) or device.get("deviceName") or thing,
                    "entities": entities,
                }
            )
    connection.send_result(msg["id"], devices)


@websocket_api.websocket_command({vol.Required("type"): "lymow/subscribe", vol.Required("thing"): str})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    thing = msg["thing"]
    coordinator = _find(hass, thing)
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", f"Unknown mower {thing}")
        return

    last: dict[str, Any] = {}

    @callback
    def _push() -> None:
        snap = snapshot(coordinator, thing)
        # MQTT pushes arrive several times a second; only forward real changes.
        if snap != last.get("snap"):
            last["snap"] = snap
            connection.send_message(websocket_api.event_message(msg["id"], snap))

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(_push)
    connection.send_result(msg["id"])
    _push()
