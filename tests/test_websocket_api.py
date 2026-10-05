"""Tests for the panel's websocket commands."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ws = sys.modules["lymow.websocket_api"]


@pytest.fixture(autouse=True)
def _no_push_throttle():
    """Send every change immediately; the throttle has its own test."""
    with patch.object(ws, "_MIN_PUSH_INTERVAL_S", 0):
        yield


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
    sched = {"id": 1, "hour": 6, "minute": 0, "timeZone": 2, "dayOfWeek": [1], "zones": ["z1"]}
    coord = _coordinator(
        {
            "schedules": [sched],
            "backupMapList": [
                {"file": "a"},
                {"file": None, "name": "broken"},
                "junk",
                {"file": "b", "name": 5, "backupTime": "x"},
                {
                    "file": "c",
                    "backupTime": 1,
                    "preview": {
                        "goZones": [
                            {
                                "polygon": [{"x": 1, "y": 2}, {"x": "bad"}, {"x": float("inf"), "y": 0}],
                                "isEnabled": False,
                            },
                            7,
                            {"polygon": "x"},
                        ],
                        "nogoZones": "x",
                    },
                },
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
        {
            "file": "c",
            "name": "",
            "backupTime": 1,
            "preview": {
                "goZones": [
                    {"polygon": [{"x": 1.0, "y": 2.0}], "isEnabled": False},
                    {"polygon": [], "isEnabled": True},
                ],
                "nogoZones": [],
                "channels": [],
            },
        },
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
    dev = SimpleNamespace(
        id="dev1", name="Lawn", name_by_user="Front lawn", identifiers={("lymow", "other"), ("lymow", THING)}
    )
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True) as dr_entries,
    ):
        dr_entries.return_value = [dev]
        ws.ws_devices(hass, conn, {"id": 1})
    result = conn.send_result.call_args.args[1]
    assert result == [
        {
            "entry_id": "entry1",
            "thing": THING,
            "device_id": "dev1",
            "name": "Front lawn",
            "entities": {"mower": "lawn_mower.lawn", "battery": "sensor.lawn_battery"},
            "read_only": [],
            "can_control": True,
        }
    ]


def test_devices_falls_back_to_api_name_without_device() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator()}}
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=[]),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True) as dr_entries,
    ):
        dr_entries.return_value = []
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
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True) as dr_entries,
    ):
        dr_entries.return_value = []
        hidden = _connection(admin=False)
        ws.ws_devices(hass, hidden, {"id": 1})
        partial = _connection(admin=False, readable={"lawn_mower.lawn"})
        ws.ws_devices(hass, partial, {"id": 2})
    assert hidden.send_result.call_args.args[1] == []
    assert partial.send_result.call_args.args[1][0]["entities"] == {"mower": "lawn_mower.lawn"}
    # Read-only (the stub grants "read" and "control" alike, so deny control explicitly).
    viewer = _connection(admin=False, readable={"lawn_mower.lawn"})
    viewer.user.permissions.check_entity = lambda eid, policy: policy == "read" and eid == "lawn_mower.lawn"
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True, return_value=[]),
    ):
        ws.ws_devices(hass, viewer, {"id": 3})
    assert viewer.send_result.call_args.args[1][0]["can_control"] is False
    assert viewer.send_result.call_args.args[1][0]["read_only"] == ["mower"]


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
    assert len(new.listeners) == 1
    hass.data["lymow"] = {}
    ws.notify_coordinators_changed(hass)  # entry unloaded: stream ends with "gone"
    assert new.listeners == [] and hass.data["lymow_ws_rebinders"] == set()
    assert conn.send_message.call_args.args[0]["event"] == {"thing": THING, "gone": True}
    conn.subscriptions[7]()  # unsubscribing afterwards is harmless


def test_devices_prefers_live_name_after_rename() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator({"deviceName": "Renamed"})}}
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=[]),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True) as dr_entries,
    ):
        dr_entries.return_value = [
            SimpleNamespace(id="d", name="Old", name_by_user=None, identifiers={("lymow", THING)})
        ]
        ws.ws_devices(hass, conn, {"id": 1})
    assert conn.send_result.call_args.args[1][0]["name"] == "Renamed"


def test_devices_ignores_non_string_live_name() -> None:
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": _coordinator({"deviceName": {"bad": 1}})}}
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=[]),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True, return_value=[]),
    ):
        ws.ws_devices(hass, conn, {"id": 1})
    assert conn.send_result.call_args.args[1][0]["name"] == "Lawn"  # registered raw name


def test_snapshot_drops_non_finite_map_values() -> None:
    nan, inf = float("nan"), float("inf")
    coord = _coordinator(
        {
            "mapData": {
                "goZones": [{"hashId": "z", "polygon": [{"x": 1.0, "y": 2.0}, {"x": nan, "y": 0.0}]}],
                "chargingStation": {"x": inf, "y": 0.0},
            },
            "poseEastM": nan,
            "poseNorthM": 1.5,
        }
    )
    m = ws.snapshot(coord, THING)["map"]
    assert m["go_zones"][0]["polygon"] == [{"x": 1.0, "y": 2.0}]
    assert "charging_station" not in m
    assert "poseEastM" not in m and m["poseNorthM"] == 1.5


def test_snapshot_survives_malformed_map_points() -> None:
    coord = _coordinator(
        {
            "mapData": {
                "goZones": [
                    {"hashId": "z", "polygon": [{"x": 1, "y": 2}, "junk", {"x": "a", "y": 1}, {"y": 3}]},
                    "junk",
                ],
                "nogoZones": [{"hashId": "n", "polygon": "nope"}],
                "channels": "nope",
            },
            "pathData": {"segments": [[{"x": 1, "y": 1}, None], "bad"]},
        }
    )
    m = ws.snapshot(coord, THING)["map"]
    assert m["go_zones"] == [{"hashId": "z", "polygon": [{"x": 1, "y": 2}]}]
    assert m["nogo_zones"] == [{"hashId": "n", "polygon": []}]
    assert m["channels"] == []
    assert m["mow_path"] == {"segments": [[{"x": 1, "y": 1}]]}


def test_backup_time_must_be_a_sane_epoch() -> None:
    assert ws._backup({"file": "a", "backupTime": float("inf")})["backupTime"] is None
    assert ws._backup({"file": "a", "backupTime": -5})["backupTime"] is None
    assert ws._backup({"file": "a", "backupTime": 1_784_475_670})["backupTime"] == 1_784_475_670


def test_snapshot_run_time_config_prefers_mirrored_writes() -> None:
    coord = _coordinator(
        {"mapData": {"runTimeConfig": {"cutHeight": 40, "moveSpeed": 0.5}}, "runTimeConfig": {"cutHeight": 60}}
    )
    assert ws.snapshot(coord, THING)["run_time_config"] == {"cutHeight": 60, "moveSpeed": 0.5}


def test_pushes_are_throttled_with_a_trailing_send() -> None:
    coord = _coordinator({"poseEastM": 1.0})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    conn = _connection()
    later: list = []
    clock = [100.0]

    def _call_later(_hass, delay, action):
        later.append((delay, action))
        return lambda: later.clear()

    with (
        patch.object(ws, "_MIN_PUSH_INTERVAL_S", 1.0),
        patch.object(ws, "async_call_later", _call_later),
        patch.object(ws.time, "monotonic", lambda: clock[0]),
    ):
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
        assert conn.send_message.call_count == 1  # initial snapshot, immediately

        coord.data[THING]["poseEastM"] = 2.0
        coord.listeners[0]()
        coord.listeners[0]()  # a second update while a send is pending: no new timer
        assert conn.send_message.call_count == 1 and len(later) == 1
        assert 0 < later[0][0] <= ws._MIN_PUSH_INTERVAL_S

        clock[0] += 1.0
        later.pop()[1](None)  # trailing send carries the latest state
        assert conn.send_message.call_count == 2
        assert conn.send_message.call_args.args[0]["event"]["map"]["poseEastM"] == 2.0

        coord.data[THING]["poseEastM"] = 3.0
        coord.listeners[0]()  # schedule another
        conn.subscriptions[7]()  # unsubscribing cancels it
        assert later == []


def test_stream_ends_when_read_access_is_revoked() -> None:
    coord = _coordinator({"poseEastM": 1.0})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    readable = {"lawn_mower.lawn"}
    conn = _connection(admin=False, readable=readable)
    with patch.object(ws.er, "async_get", create=True) as er_get:
        er_get.return_value.async_get_entity_id.return_value = "lawn_mower.lawn"
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
        assert conn.send_message.call_count == 1
        readable.clear()  # admin revokes access
        coord.data[THING]["poseEastM"] = 2.0
        coord.listeners and coord.listeners[0]()
    assert conn.send_message.call_args.args[0]["event"] == {"thing": THING, "unauthorized": True}
    assert coord.listeners == []
    count = conn.send_message.call_count
    ws.notify_coordinators_changed(hass)  # ended streams don't come back
    assert conn.send_message.call_count == count


def test_gone_cancels_a_pending_trailing_send() -> None:
    coord = _coordinator({"poseEastM": 1.0})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    conn = _connection()
    cancelled: list = []
    with (
        patch.object(ws, "_MIN_PUSH_INTERVAL_S", 10.0),
        patch.object(ws, "async_call_later", lambda _h, _d, _a: lambda: cancelled.append(True)),
    ):
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
        coord.data[THING]["poseEastM"] = 2.0
        coord.listeners[0]()  # schedules a trailing send
        hass.data["lymow"] = {}
        ws.notify_coordinators_changed(hass)
    assert cancelled == [True]
    assert conn.send_message.call_args.args[0]["event"] == {"thing": THING, "gone": True}


def test_snapshot_drops_malformed_schedules() -> None:
    good = {"id": 7, "dayOfWeek": [1], "hour": 22, "minute": 30, "timeZone": 2, "zones": ["z1", 5], "isRepeated": True}
    coord = _coordinator(
        {
            "schedules": [
                good,
                "junk",
                {"hour": "x", "minute": 0},
                {"hour": 1, "minute": 0, "timeZone": 99},
                {"id": 2, "hour": 1, "minute": 0, "dayOfWeek": [9]},
                {"id": 3, "hour": 1, "minute": 0, "dayOfWeek": "mon"},
                {"id": 8, "hour": 1, "minute": 0, "zones": "z1"},  # zones not a list
                {"id": 9, "hour": 1, "minute": 0, "zones": ["z2"]},
                {"id": 10, "hour": 1, "minute": 0, "zones": ["", "  "]},  # blank ids only
                {"hour": 1, "minute": 0},  # no id
                {"id": 2**53, "hour": 1, "minute": 0, "zones": ["z"]},  # not exact in JS
            ]
        }
    )
    out = ws.snapshot(coord, THING)["schedules"]
    assert out[0]["hour"] == 0 and out[0]["dayOfWeek"] == [2] and out[0]["zones"] == ["z1"]  # 22:30 UTC+2 → Tue 00:30
    assert len(out) == 2 and out[1]["zones"] == ["z2"] and out[1]["dayOfWeek"] == []


def test_stream_hides_parts_the_user_cannot_read() -> None:
    coord = _coordinator(
        {
            "mapData": {"goZones": [{"hashId": "z", "polygon": [{"x": 1.0, "y": 2.0}]}], "gpsOrigin": {"lat": 59.0}},
            "schedules": [{"id": 1, "hour": 1, "minute": 0, "zones": ["z"]}],
            "backupMapList": [{"file": "b"}],
            "runTimeConfig": {"cutHeight": 50},
            "mapReceivedAt": 123.0,
        }
    )
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    ids = {"lawn_mower": "lawn_mower.lawn"}
    lookup = lambda domain, _d, uid: ids.get(domain) or f"{domain}.{uid}"  # noqa: E731
    conn = _connection(admin=False, readable={"lawn_mower.lawn", f"sensor.{THING}_schedules"})
    with patch.object(ws.er, "async_get", create=True) as er_get:
        er_get.return_value.async_get_entity_id.side_effect = lookup
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
    event = conn.send_message.call_args.args[0]["event"]
    assert event["map"] == {} and event["map_hidden"] is True
    assert event["backups"] == "hidden" and event["run_time_config"] == {}
    assert event["map_received_at"] is None
    assert event["schedules"][0]["minute"] == 0


def test_snapshot_omits_gps_origin() -> None:
    coord = _coordinator({"mapData": {"gpsOrigin": {"lat": 59.0, "lon": 16.0}}})
    assert "gps_origin" not in ws.snapshot(coord, THING)["map"]


def test_snapshot_online_uses_positive_signals() -> None:
    assert ws.snapshot(_coordinator({}), THING)["online"] is False  # unknown is not online
    assert ws.snapshot(_coordinator({"deviceState": "ONLINE"}), THING)["online"] is True
    assert ws.snapshot(_coordinator({"deviceState": "weird", "isOnline": True}), THING)["online"] is True
    assert ws.snapshot(_coordinator({"deviceState": "offline"}), THING)["online"] is False


def test_devices_longest_thing_prefix_owns_the_entity() -> None:
    coord = _coordinator()
    coord.devices = [{"deviceThingName": "abc"}, {"deviceThingName": "abc_mower"}]
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    reg_entries = [
        SimpleNamespace(unique_id="abc", entity_id="lawn_mower.a"),
        SimpleNamespace(unique_id="abc_battery", entity_id="sensor.a_battery"),
        SimpleNamespace(unique_id="abc_mower", entity_id="lawn_mower.b"),
        SimpleNamespace(unique_id="abc_mower_battery", entity_id="sensor.b_battery"),
    ]
    conn = _connection()
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True, return_value=[]),
    ):
        ws.ws_devices(hass, conn, {"id": 1})
    a, b = conn.send_result.call_args.args[1]
    assert a["entities"] == {"mower": "lawn_mower.a", "battery": "sensor.a_battery"}
    assert b["entities"] == {"mower": "lawn_mower.b", "battery": "sensor.b_battery"}


def test_snapshot_survives_non_list_sections_and_far_points() -> None:
    coord = _coordinator(
        {
            "schedules": 1,
            "backupMapList": 1,
            "mapData": {"goZones": [{"hashId": "z", "polygon": [{"x": 1.0, "y": 2.0}, {"x": 1e6, "y": 0.0}]}]},
        }
    )
    snap = ws.snapshot(coord, THING)
    assert snap["schedules"] == [] and snap["backups"] == []
    assert snap["map"]["go_zones"][0]["polygon"] == [{"x": 1.0, "y": 2.0}]


def test_snapshot_tolerates_non_mapping_map_data() -> None:
    snap = ws.snapshot(_coordinator({"mapData": [1, 2], "runTimeConfig": 5}), THING)
    assert snap["run_time_config"] == {} and isinstance(snap["map"], dict)


def test_hidden_schedules_are_marked_not_loading() -> None:
    coord = _coordinator({"schedules": [{"id": 1, "hour": 1, "minute": 0, "zones": ["z"]}]})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    conn = _connection(admin=False, readable={"lawn_mower.lawn"})
    with patch.object(ws.er, "async_get", create=True) as er_get:
        er_get.return_value.async_get_entity_id.side_effect = lambda d, _x, uid: (
            "lawn_mower.lawn" if d == "lawn_mower" else f"{d}.{uid}"
        )
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
    assert conn.send_message.call_args.args[0]["event"]["schedules"] == "hidden"


def test_devices_use_device_registry_ownership_and_tolerate_missing_mower() -> None:
    coord = _coordinator()
    coord.devices = [{"deviceThingName": "abc"}, {"deviceThingName": "abc_battery"}]
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    reg_entries = [
        SimpleNamespace(unique_id="abc_battery", entity_id="sensor.a_battery", device_id="devA"),  # abc's sensor
        SimpleNamespace(unique_id="abc_battery", entity_id="lawn_mower.b", device_id="devB"),
    ]
    devs = [
        SimpleNamespace(id="devA", name="A", name_by_user=None, identifiers={("lymow", "abc")}),
        SimpleNamespace(id="devB", name="B", name_by_user=None, identifiers={("lymow", "abc_battery")}),
    ]
    conn = _connection()  # admin: devices without a registered mower are still listed
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True, return_value=devs),
    ):
        ws.ws_devices(hass, conn, {"id": 1})
    a, b = conn.send_result.call_args.args[1]
    assert a["entities"] == {"battery": "sensor.a_battery"} and a["can_control"] is True
    assert b["entities"] == {"mower": "lawn_mower.b"}
    viewer = _connection(admin=False, readable=set())
    viewer.user.permissions.check_entity = lambda eid, p: True
    with (
        patch.object(ws.er, "async_get", create=True),
        patch.object(ws.er, "async_entries_for_config_entry", create=True, return_value=reg_entries[:1]),
        patch.object(ws.dr, "async_get", create=True),
        patch.object(ws.dr, "async_entries_for_config_entry", create=True, return_value=devs),
    ):
        ws.ws_devices(hass, viewer, {"id": 2})  # no mower entity: must not raise


def test_rebind_cancels_a_pending_trailing_send() -> None:
    coord = _coordinator({"poseEastM": 1.0})
    hass = MagicMock()
    hass.data = {"lymow": {"entry1": coord}}
    conn = _connection()
    cancelled: list = []
    with (
        patch.object(ws, "_MIN_PUSH_INTERVAL_S", 10.0),
        patch.object(ws, "async_call_later", lambda _h, _d, _a: lambda: cancelled.append(True)),
    ):
        ws.ws_subscribe(hass, conn, {"id": 7, "thing": THING})
        coord.data[THING]["poseEastM"] = 2.0
        coord.listeners[0]()  # schedules a trailing send
        hass.data["lymow"]["entry1"] = _coordinator({"poseEastM": 3.0})
        ws.notify_coordinators_changed(hass)
    assert cancelled == [True]
    hass.data["lymow"] = {}
    ws.notify_coordinators_changed(hass)


def test_snapshot_tolerates_non_mapping_nested_run_time_config() -> None:
    assert ws.snapshot(_coordinator({"mapData": {"runTimeConfig": 1}}), THING)["run_time_config"] == {}


def test_schedules_use_home_assistant_offset_for_fractional_zones() -> None:
    coord = _coordinator(
        {"schedules": [{"id": 1, "hour": 3, "minute": 30, "timeZone": 5, "dayOfWeek": [1], "zones": ["z"]}]}
    )
    coord.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Asia/Kolkata"))  # UTC+5:30, no DST
    row = ws.snapshot(coord, THING)["schedules"][0]
    assert (row["hour"], row["minute"], row["dayOfWeek"]) == (9, 0, [1])  # 03:30 UTC → 09:00 local
    late = _coordinator({"schedules": [{"id": 2, "hour": 20, "minute": 0, "dayOfWeek": [6], "zones": ["z"]}]})
    late.hass = coord.hass
    assert ws.snapshot(late, THING)["schedules"][0]["dayOfWeek"] == [0]  # 01:30 next day, Sat → Sun
    bad = _coordinator({"schedules": [{"id": 3, "hour": 1, "minute": 0, "zones": ["z"]}]})
    bad.hass = SimpleNamespace(config=SimpleNamespace(time_zone="Not/AZone"))
    assert ws.snapshot(bad, THING)["schedules"][0]["hour"] == 1  # falls back to the stored offset
