"""Tests for the panel's websocket commands."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ws = sys.modules["lymow.websocket_api"]

THING = "device_aabbcc"


def _coordinator(data: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.devices = [{"deviceThingName": THING, "deviceName": "Lawn"}]
    coord.data = {THING: data or {}}
    coord.listeners = []

    def _add(cb):
        coord.listeners.append(cb)
        return lambda: coord.listeners.remove(cb)

    coord.async_add_listener = _add
    return coord


def _connection(admin: bool = True, readable: set[str] | None = None) -> MagicMock:
    conn = MagicMock()
    conn.subscriptions = {}
    conn.user.is_admin = admin
    conn.user.permissions.check_entity = lambda eid, policy: eid in (readable or set())
    return conn


def test_register_adds_both_commands() -> None:
    with patch.object(ws.websocket_api, "async_register_command") as reg:
        ws.async_register(MagicMock())
    assert [c.args[1] for c in reg.call_args_list] == [ws.ws_devices, ws.ws_subscribe]


def test_snapshot_contents() -> None:
    sched = {"hour": 6, "minute": 0, "timeZone": 2, "dayOfWeek": [1]}
    coord = _coordinator(
        {
            "schedules": [sched],
            "backupMapList": [
                {"file": "a"},
                {"file": None, "name": "broken"},
                "junk",
                {"file": "b", "name": 5, "backupTime": "x"},
            ],
            "deviceState": "offline",
            "mapData": {},
        }
    )
    snap = ws.snapshot(coord, THING)
    assert snap["thing"] == THING
    assert snap["schedules"][0]["hour"] == 8  # converted to local time
    assert snap["backups"] == [
        {"file": "a", "name": "", "backupTime": None, "preview": None},
        {"file": "b", "name": "", "backupTime": None, "preview": None},
    ]
    assert snap["online"] is False
    assert snap["map"] == {}


def test_devices_maps_entities_by_unique_id_suffix() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator()}}
    reg_entries = [
        SimpleNamespace(unique_id=THING, entity_id="lawn_mower.lawn"),
        SimpleNamespace(unique_id=f"{THING}_battery", entity_id="sensor.lawn_battery"),
        SimpleNamespace(unique_id="other_thing_battery", entity_id="sensor.other"),
        SimpleNamespace(unique_id=None, entity_id="sensor.none"),
    ]
    dev = SimpleNamespace(id="dev1", name="Lawn", name_by_user="Front lawn")
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries),
        patch.object(ws.dr, "async_get", create=True) as dr_get,
    ):
        dr_get.return_value.async_get_device.return_value = dev
        ws.ws_devices(hass, conn, {"id": 1})
    result = conn.send_result.call_args.args[1]
    assert result == [
        {
            "entry_id": "entry1",
            "thing": THING,
            "device_id": "dev1",
            "name": "Front lawn",
            "entities": {"mower": "lawn_mower.lawn", "battery": "sensor.lawn_battery"},
        }
    ]


def test_devices_falls_back_to_api_name_without_device() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator()}}
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=[]),
        patch.object(ws.dr, "async_get", create=True) as dr_get,
    ):
        dr_get.return_value.async_get_device.return_value = None
        ws.ws_devices(hass, conn, {"id": 1})
    device = conn.send_result.call_args.args[1][0]
    assert device["name"] == "Lawn"
    assert device["device_id"] is None


def test_subscribe_unknown_thing_errors() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator()}}
    conn = _connection()
    ws.ws_subscribe(hass, conn, {"id": 7, "thing": "nope"})
    conn.send_error.assert_called_once()
    assert conn.send_error.call_args.args[1] == "not_found"


def test_subscribe_pushes_initial_and_only_changed_snapshots() -> None:
    coord = _coordinator({"mapData": {}})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    conn = _connection()
    ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
    conn.send_result.assert_called_once_with(7)
    assert conn.send_message.call_count == 1  # initial snapshot

    coord.listeners[0]()  # nothing changed → no push
    assert conn.send_message.call_count == 1

    coord.data[THING]["backupMapList"] = [{"file": "x"}]
    coord.listeners[0]()
    assert conn.send_message.call_count == 2
    assert conn.send_message.call_args.args[0]["event"]["backups"][0]["file"] == "x"

    conn.subscriptions[7]()  # unsubscribe removes the listener
    assert coord.listeners == []


def test_snapshot_schedules_unknown_until_received() -> None:
    assert ws.snapshot(_coordinator({}), THING)["schedules"] is None
    assert ws.snapshot(_coordinator({"schedules": []}), THING)["schedules"] == []


def test_devices_hidden_from_users_without_read_access() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator()}}
    reg_entries = [
        SimpleNamespace(unique_id=THING, entity_id="lawn_mower.lawn"),
        SimpleNamespace(unique_id=f"{THING}_battery", entity_id="sensor.lawn_battery"),
    ]
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries),
        patch.object(ws.dr, "async_get", create=True) as dr_get,
    ):
        dr_get.return_value.async_get_device.return_value = None
        hidden = _connection(admin=False)
        ws.ws_devices(hass, hidden, {"id": 1})
        partial = _connection(admin=False, readable={"lawn_mower.lawn"})
        ws.ws_devices(hass, partial, {"id": 2})
    assert hidden.send_result.call_args.args[1] == []
    assert partial.send_result.call_args.args[1][0]["entities"] == {"mower": "lawn_mower.lawn"}


def test_subscribe_rejects_user_without_read_access() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator()}}
    conn = _connection(admin=False)
    with patch.object(ws.er, "async_get", create=True) as er_get:
        er_get.return_value.async_get_entity_id.return_value = "lawn_mower.lawn"
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
    assert conn.send_error.call_args.args[1] == "unauthorized"
    assert conn.subscriptions == {}


def test_subscription_follows_coordinator_after_reload() -> None:
    old = _coordinator({"backupMapList": [{"file": "old"}]})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": old}}
    conn = _connection()
    ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
    assert conn.send_message.call_args.args[0]["event"]["backups"][0]["file"] == "old"

    new = _coordinator({"backupMapList": [{"file": "new"}]})
    hass.data["lymow"]["entry1"] = new
    ws.notify_coordinators_changed(hass)
    assert old.listeners == [] and len(new.listeners) == 1
    assert conn.send_message.call_args.args[0]["event"]["backups"][0]["file"] == "new"

    ws.notify_coordinators_changed(hass)  # same coordinator again: no-op
    hass.data["lymow"] = {}
    ws.notify_coordinators_changed(hass)  # mower gone: keep the old binding
    conn.subscriptions[7]()
    assert new.listeners == [] and hass.data["lymow_ws_rebinders"] == set()
