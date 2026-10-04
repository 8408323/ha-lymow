"""Websocket commands backing the Lymow panel.

``lymow/devices`` lists every mower with its entities keyed by unique-id suffix,
so the panel never guesses entity_id slugs. ``lymow/subscribe`` streams one
mower's non-entity data (map geometry, live pose, schedules, backups), pushing a
fresh snapshot whenever the coordinator updates and the snapshot changed.
Actions go through the regular ``lymow.*`` services (also over the websocket).
"""

from __future__ import annotations

import math
import time
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN
from .sensor import _schedule_to_local, map_payload

# homeassistant.auth.permissions.const.POLICY_READ
POLICY_READ = "read"
POLICY_CONTROL = "control"


_REBIND_KEY = f"{DOMAIN}_ws_rebinders"
# Upper bound on snapshot pushes per subscription (seconds between sends).
_MIN_PUSH_INTERVAL_S = 1.0


@callback
def notify_coordinators_changed(hass: HomeAssistant) -> None:
    """Called after an entry (re)loads so open subscriptions attach to its new coordinator."""
    for rebind in list(hass.data.get(_REBIND_KEY, ())):
        rebind()


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_devices)
    websocket_api.async_register_command(hass, ws_subscribe)


def _coordinators(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.get(DOMAIN, {})


def _can_read(connection: websocket_api.ActiveConnection, entity_id: str | None) -> bool:
    """Admins see everything; other users need read access to the mower entity."""
    user = connection.user
    if user.is_admin:
        return True
    return entity_id is not None and user.permissions.check_entity(entity_id, POLICY_READ)


def _mower_entity(hass: HomeAssistant, thing: str) -> str | None:
    return er.async_get(hass).async_get_entity_id("lawn_mower", DOMAIN, thing)


def _find(hass: HomeAssistant, thing: str) -> Any | None:
    for coordinator in _coordinators(hass).values():
        if any(d.get("deviceThingName") == thing for d in coordinator.devices):
            return coordinator
    return None


def _coord(v: Any) -> bool:
    """A usable map coordinate: finite and within a few km of the dock (ENU metres)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and abs(v) < 1e5


_DROP = object()


def _finite(obj: Any) -> Any:
    """Drop non-finite/absurd floats (and points left without x/y) from decoded data.

    Map geometry comes straight from protobuf float32s; a corrupt value (NaN, inf)
    would break JSON serialisation or the panel's rendering."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) and abs(obj) < 1e7 else _DROP
    if isinstance(obj, dict):
        out = {k: c for k, v in obj.items() if (c := _finite(v)) is not _DROP}
        if ("x" in obj or "y" in obj) and not ("x" in out and "y" in out):
            return _DROP
        return out
    if isinstance(obj, list):
        return [c for v in obj if (c := _finite(v)) is not _DROP]
    return obj


def _polygon(points: Any) -> list[dict[str, float]]:
    if not isinstance(points, list):
        return []
    return [
        {"x": float(p["x"]), "y": float(p["y"])}
        for p in points
        if isinstance(p, dict) and all(_coord(p.get(k)) for k in ("x", "y"))
    ]


def _preview(preview: Any) -> dict[str, list[dict[str, Any]]] | None:
    """Backup thumbnail geometry, reduced to well-formed polygons."""
    if not isinstance(preview, dict):
        return None
    return {
        kind: [
            {"polygon": _polygon(z.get("polygon")), "isEnabled": z.get("isEnabled") is not False}
            for z in preview.get(kind) or []
            if isinstance(z, dict)
        ]
        if isinstance(preview.get(kind), list)
        else []
        for kind in ("goZones", "nogoZones", "channels")
    }


def _backup(entry: Any) -> dict[str, Any] | None:
    """A backup row in a fixed shape, or None if it can't be acted on (no object key).

    The list comes from the Lymow cloud as-is; normalise it here so the panel can
    rely on the types instead of trusting the REST payload."""
    if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or not entry["file"]:
        return None
    name, when, preview = entry.get("name"), entry.get("backupTime"), entry.get("preview")
    return {
        "file": entry["file"],
        "name": name if isinstance(name, str) else "",
        # Epoch seconds; anything non-finite or outside 1970–2100 is dropped.
        "backupTime": when
        if isinstance(when, (int, float)) and not isinstance(when, bool) and 0 <= when < 4_102_444_800
        else None,
        "preview": _preview(preview),
    }


def _int_in(value: Any, lo: int, hi: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi else None


def _schedule(sched: Any) -> dict[str, Any] | None:
    """A schedule in local time, or None when the decoded entry is malformed."""
    if not isinstance(sched, dict):
        return None
    hour, minute = _int_in(sched.get("hour"), 0, 23), _int_in(sched.get("minute"), 0, 59)
    days = sched.get("dayOfWeek") or []
    tz = sched.get("timeZone") or 0
    if hour is None or minute is None or _int_in(tz, -24, 24) is None:
        return None
    if not isinstance(days, list) or any(_int_in(d, 0, 6) is None for d in days):
        return None
    zones = sched.get("zones")
    return _schedule_to_local(
        {
            **sched,
            "dayOfWeek": days,
            "zones": [z for z in zones if isinstance(z, str)] if isinstance(zones, list) else [],
        }
    )


# Snapshot parts and the sensor whose read permission guards each (unique-id suffix),
# so the stream never shows a non-admin more than their entity access allows.
_GUARDED = (
    ("map", "map", {}),
    ("run_time_config", "map", {}),  # also a map-sensor attribute
    ("map_received_at", "map", None),
    ("schedules", "schedules", None),
    ("backups", "backup_maps", []),
)


def _redact(hass: HomeAssistant, connection: websocket_api.ActiveConnection, thing: str, snap: dict) -> dict:
    if connection.user.is_admin:
        return snap
    reg = er.async_get(hass)
    out = dict(snap)
    for key, suffix, empty in _GUARDED:
        if not _can_read(connection, reg.async_get_entity_id("sensor", DOMAIN, f"{thing}_{suffix}")):
            out[key] = empty
    return out


def snapshot(coordinator: Any, thing: str) -> dict[str, Any]:
    """Everything the panel needs for one mower that isn't an entity state."""
    data = (coordinator.data or {}).get(thing) or {}
    schedules = data.get("schedules")
    return {
        "thing": thing,
        # gps_origin pins the lawn to real-world coordinates; the panel works in
        # dock-relative metres and never needs it.
        "map": _finite({k: v for k, v in map_payload(data).items() if k != "gps_origin"}),
        # Live run-time overrides: the map reply's copy, overlaid by the values the
        # coordinator mirrors after a successful set_run_time_config.
        "run_time_config": _finite(
            {**((data.get("mapData") or {}).get("runTimeConfig") or {}), **(data.get("runTimeConfig") or {})}
        ),
        # None = not received yet (a query is in flight). The panel must not edit
        # schedules then: add_schedule writes the full list and would drop the rest.
        "schedules": None if schedules is None else [row for s in schedules if (row := _schedule(s))],
        "backups": [row for b in data.get("backupMapList") or [] if (row := _backup(b))],
        "online": data.get("deviceState") != "offline",
        # When the mower last sent a map reply (epoch s), for confirming edits.
        "map_received_at": data.get("mapReceivedAt"),
    }


def _text(value: Any) -> str | None:
    """A non-empty string, or None for anything else the cloud might send."""
    return value if isinstance(value, str) and value else None


@websocket_api.websocket_command({vol.Required("type"): "lymow/devices"})
@callback
def ws_devices(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    registry = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    devices = []
    for entry_id, coordinator in _coordinators(hass).items():
        reg_entries = er.async_entries_for_config_entry(registry, entry_id)
        entry_devices = dr.async_entries_for_config_entry(dev_reg, entry_id)
        for device in coordinator.devices:
            thing = device["deviceThingName"]
            entities: dict[str, str] = {}
            for ent in reg_entries:
                uid = ent.unique_id or ""
                if uid == thing:
                    entities["mower"] = ent.entity_id
                elif uid.startswith(f"{thing}_"):
                    entities[uid[len(thing) + 1 :]] = ent.entity_id
            if not _can_read(connection, entities.get("mower")):
                continue
            entities = {k: v for k, v in entities.items() if _can_read(connection, v)}
            dev = next((d for d in entry_devices if (DOMAIN, thing) in d.identifiers), None)
            devices.append(
                {
                    "entry_id": entry_id,
                    "thing": thing,
                    "device_id": dev.id if dev else None,
                    # A user's registry rename wins; otherwise the live name (set_device_name
                    # updates it immediately), then what the device was registered as.
                    "name": _text(dev and dev.name_by_user)
                    or _text(((coordinator.data or {}).get(thing) or {}).get("deviceName"))
                    or _text(dev and dev.name)
                    or _text(device.get("deviceName"))
                    or thing,
                    "entities": entities,
                    # Read-only users get the panel without its actions.
                    "can_control": connection.user.is_admin
                    or connection.user.permissions.check_entity(entities["mower"], POLICY_CONTROL),
                }
            )
    connection.send_result(msg["id"], devices)


@websocket_api.websocket_command({vol.Required("type"): "lymow/subscribe", vol.Required("thing"): str})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    thing = msg["thing"]
    coordinator = _find(hass, thing)
    if coordinator is not None and not _can_read(connection, _mower_entity(hass, thing)):
        connection.send_error(msg["id"], "unauthorized", "Not allowed to read this mower")
        return
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", f"Unknown mower {thing}")
        return

    state: dict[str, Any] = {"coordinator": coordinator, "sent_at": 0.0, "timer": None}

    @callback
    def _send(_now: Any = None) -> None:
        state["timer"] = None
        # Permissions can change while subscribed (HA's own forwarders re-check too).
        if not _can_read(connection, _mower_entity(hass, thing)):
            _end("unauthorized")
            return
        snap = _redact(hass, connection, thing, snapshot(state["coordinator"], thing))
        # Only forward real changes.
        if snap != state.get("snap"):
            state["snap"] = snap
            state["sent_at"] = time.monotonic()
            connection.send_message(websocket_api.event_message(msg["id"], snap))

    @callback
    def _push() -> None:
        # While mowing, MQTT pose updates arrive several times a second and each
        # snapshot carries the whole map: send at most one per interval, trailing.
        if state["timer"] is not None:
            return
        wait = state["sent_at"] + _MIN_PUSH_INTERVAL_S - time.monotonic()
        if wait <= 0:
            _send()
        else:
            state["timer"] = async_call_later(hass, wait, _send)

    @callback
    def _end(reason: str) -> None:
        """Stop the stream for good (nothing may follow the final event)."""
        if state["timer"] is not None:
            state["timer"]()
            state["timer"] = None
        state["unlisten"]()
        state["unlisten"] = lambda: None
        rebinders.discard(_rebind)
        connection.send_message(websocket_api.event_message(msg["id"], {"thing": thing, reason: True}))

    @callback
    def _rebind() -> None:
        # A config-entry reload replaces the coordinator; follow it so an open
        # panel keeps getting live data instead of freezing on the old one.
        new = _find(hass, thing)
        if new is state["coordinator"]:
            return
        if new is None:
            # The mower's entry was unloaded: stop streaming a frozen snapshot and
            # tell the panel, which then reloads its device list.
            _end("gone")
            return
        state["unlisten"]()
        state["coordinator"] = new
        state["unlisten"] = new.async_add_listener(_push)
        _send()

    state["unlisten"] = coordinator.async_add_listener(_push)
    rebinders: set = hass.data.setdefault(_REBIND_KEY, set())
    rebinders.add(_rebind)

    @callback
    def _unsubscribe() -> None:
        if state["timer"] is not None:
            state["timer"]()
        state["unlisten"]()
        rebinders.discard(_rebind)

    connection.subscriptions[msg["id"]] = _unsubscribe
    connection.send_result(msg["id"])
    _push()
