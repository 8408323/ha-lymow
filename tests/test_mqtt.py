"""Tests for Lymow MQTT client."""

from __future__ import annotations

import asyncio
import logging

import pytest
from lymow.mqtt import LymowMqttClient, aiomqtt


@pytest.mark.asyncio
async def test_connect_uses_timeout_and_cleans_up_on_subscribe_failure(monkeypatch, caplog):
    events: dict[str, object] = {}
    topics: list[str] = []
    publish_attempts: list[tuple[str, str, int]] = []

    class FakeClient:
        def __init__(self, **kwargs):
            events["kwargs"] = kwargs

        async def __aenter__(self):
            events["entered"] = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events["exited"] = True

        async def subscribe(self, topic, qos):
            topics.append(topic)
            if topic.endswith("/notify-app"):
                raise aiomqtt.MqttError("subscribe failed")

        async def publish(self, topic, payload, qos):
            publish_attempts.append((topic, payload, qos))

    monkeypatch.setattr(aiomqtt, "Client", FakeClient)

    client = LymowMqttClient(
        host="example.amazonaws.com",
        region="eu-west-1",
        on_state=lambda _thing, _state: None,
        on_online=lambda _thing, _online: None,
    )

    with pytest.raises(aiomqtt.MqttError, match="subscribe failed"):
        await client.connect(
            ["mower-001"],
            access_key="access",
            secret_key="secret",
            session_token=None,
        )

    assert events["entered"] is True
    assert events["exited"] is True
    assert events["kwargs"]["timeout"] == 15
    assert topics == [
        "/device/mower-001/pboutput",
        "/device/mower-001/notify-app",
    ]
    with caplog.at_level(logging.WARNING):
        client.publish_command("mower-001", b"payload")
    assert "MQTT not connected" in caplog.text
    assert publish_attempts == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncIter:
    """Async iterator that yields from a fixed list."""

    def __init__(self, items):
        self._items = list(items)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._idx]
        self._idx += 1
        return item


class _FakeMessage:
    def __init__(self, topic_str: str, payload: bytes = b""):
        self.topic = type("T", (), {"__str__": lambda s: topic_str})()
        self.payload = payload


def _make_success_client(messages=()):
    """Return a fake aiomqtt.Client class that succeeds with given messages."""

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = _AsyncIter(messages)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def subscribe(self, topic, qos):
            pass

        async def publish(self, topic, payload, qos):
            pass

    return FakeClient


def _make_mqtt() -> LymowMqttClient:
    return LymowMqttClient(
        host="host.eu-west-1.amazonaws.com",
        region="eu-west-1",
        on_state=lambda *a: None,
        on_online=lambda *a: None,
    )


# ---------------------------------------------------------------------------
# build_presigned_ws_path with session_token (line 98)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_presigned_ws_path_includes_security_token():
    from lymow.mqtt import build_presigned_ws_path

    path = build_presigned_ws_path(
        host="abc.iot.eu-west-1.amazonaws.com",
        region="eu-west-1",
        access_key="AKID",
        secret_key="secret",
        session_token="MY_SESSION_TOKEN",
    )
    assert "X-Amz-Security-Token=MY_SESSION_TOKEN" in path


# ---------------------------------------------------------------------------
# connect — success path (lines 170-173)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_success_sets_client_and_task(monkeypatch):
    monkeypatch.setattr(aiomqtt, "Client", _make_success_client())
    client = _make_mqtt()
    assert client.is_connected is False  # nothing published before connect
    await client.connect(["mower-001"], "key", "secret", None)
    assert client._client is not None
    assert client.is_connected is True
    assert client._listen_task is not None
    await client.disconnect()
    assert client.is_connected is False


# ---------------------------------------------------------------------------
# disconnect — with running task and client (lines 186-195)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_cancels_task_and_clears_client(monkeypatch):
    monkeypatch.setattr(aiomqtt, "Client", _make_success_client())
    client = _make_mqtt()
    await client.connect(["mower-001"], "key", "secret", None)
    assert client._client is not None
    await client.disconnect()
    assert client._client is None
    assert client._listen_task is None


@pytest.mark.asyncio
async def test_disconnect_is_idempotent_when_not_connected():
    client = _make_mqtt()
    await client.disconnect()  # Should not raise
    assert client._client is None
    assert client._listen_task is None


# ---------------------------------------------------------------------------
# reconnect (lines 182-183)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_disconnects_and_reconnects(monkeypatch):
    connect_count = {"n": 0}
    FakeClient = _make_success_client()
    original_init = FakeClient.__init__

    def counting_init(self, **kwargs):
        connect_count["n"] += 1
        original_init(self, **kwargs)

    FakeClient.__init__ = counting_init
    monkeypatch.setattr(aiomqtt, "Client", FakeClient)

    client = _make_mqtt()
    await client.connect(["thing1"], "key", "secret", None)
    assert connect_count["n"] == 1
    await client.reconnect("new-key", "new-secret", None)
    assert connect_count["n"] == 2
    await client.disconnect()


# ---------------------------------------------------------------------------
# publish_command with client (line 202)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_command_with_client_schedules_publish():
    import asyncio

    published = []

    class FakeClient:
        async def publish(self, topic, payload, qos):
            published.append(topic)

        async def __aexit__(self, *a):
            pass

    client = _make_mqtt()
    client._client = FakeClient()
    client.publish_command("mower-001", b"cmd")
    await asyncio.sleep(0)  # let ensure_future run
    assert published == ["/device/mower-001/pbinput"]


# ---------------------------------------------------------------------------
# async_publish_command (lines 206-212)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_publish_command_no_client_logs_warning(caplog):
    client = _make_mqtt()
    with caplog.at_level(logging.WARNING):
        await client.async_publish_command("mower-001", b"cmd")
    assert "MQTT not connected" in caplog.text


@pytest.mark.asyncio
async def test_async_publish_command_with_client():
    published = []

    class FakeClient:
        async def publish(self, topic, payload, qos):
            published.append(topic)

    client = _make_mqtt()
    client._client = FakeClient()
    await client.async_publish_command("mower-001", b"cmd")
    assert published == ["/device/mower-001/pbinput"]


# ---------------------------------------------------------------------------
# _publish (lines 219-224)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_no_client_returns_early():
    client = _make_mqtt()
    await client._publish("mower-001", b"cmd")  # no exception


@pytest.mark.asyncio
async def test_publish_with_client_sends_to_correct_topic():
    published = []

    class FakeClient:
        async def publish(self, topic, payload, qos):
            published.append(topic)

    client = _make_mqtt()
    client._client = FakeClient()
    await client._publish("mower-001", b"cmd")
    assert published == ["/device/mower-001/pbinput"]


# ---------------------------------------------------------------------------
# _listen_loop (lines 227-235)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_loop_no_client_returns_immediately():
    client = _make_mqtt()
    await client._listen_loop()  # No exception, returns immediately


@pytest.mark.asyncio
async def test_listen_loop_dispatches_messages():
    dispatched = []

    class FakeClient:
        def __init__(self):
            self.messages = _AsyncIter([_FakeMessage("/device/t1/notify-app", b'{"robotState":"online"}')])

    client = _make_mqtt()
    client._things = ["t1"]
    client._client = FakeClient()
    client._dispatch = lambda msg: dispatched.append(str(msg.topic))
    await client._listen_loop()
    assert dispatched == ["/device/t1/notify-app"]


@pytest.mark.asyncio
async def test_listen_loop_handles_cancelled_error():
    class _CancelIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise asyncio.CancelledError

    class FakeClient:
        messages = _CancelIter()

    client = _make_mqtt()
    client._client = FakeClient()
    await client._listen_loop()  # CancelledError is swallowed


@pytest.mark.asyncio
async def test_listen_loop_handles_unexpected_exception(caplog):
    class _BoomIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("unexpected boom")

    class FakeClient:
        messages = _BoomIter()

    client = _make_mqtt()
    client._client = FakeClient()
    with caplog.at_level(logging.ERROR):
        await client._listen_loop()
    assert "MQTT listen loop exited unexpectedly" in caplog.text


# ---------------------------------------------------------------------------
# _dispatch (lines 238-255)
# ---------------------------------------------------------------------------


def test_dispatch_ignores_unknown_topic():
    client = _make_mqtt()
    client._things = ["mower-001"]
    msg = _FakeMessage("/device/mower-999/pboutput", b"data")
    client._dispatch(msg)  # thing_name is None — returns silently


def test_dispatch_routes_pboutput(monkeypatch):
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: b"")
    monkeypatch.setattr(proto, "decode_pboutput", lambda b: {"s": 1})
    monkeypatch.setattr(proto, "decode_map_response", lambda b: None)

    states = {}
    client = LymowMqttClient("h", "eu-west-1", lambda t, s: states.update(s), lambda *a: None)
    client._things = ["m1"]
    client._dispatch(_FakeMessage("/device/m1/pboutput", b"payload"))
    assert states == {"s": 1}


def test_dispatch_routes_notify(monkeypatch):
    import json

    online_events = {}
    client = LymowMqttClient("h", "eu-west-1", lambda *a: None, lambda t, o: online_events.update({t: o}))
    client._things = ["m1"]
    client._dispatch(_FakeMessage("/device/m1/notify-app", json.dumps({"robotState": "online"}).encode()))
    assert online_events == {"m1": True}


def test_dispatch_silently_drops_known_thing_unknown_suffix():
    """Known-thing topic with unrecognized suffix must be ignored without raising."""
    state_events: list = []
    online_events: list = []
    client = LymowMqttClient("h", "eu-west-1", lambda *a: state_events.append(a), lambda *a: online_events.append(a))
    client._things = ["m1"]
    # Topic contains /device/m1/ so the thing matches, but suffix is neither known one.
    client._dispatch(_FakeMessage("/device/m1/shadow/update", b"{}"))
    assert state_events == []
    assert online_events == []


def test_dispatch_handles_exception_in_handler(caplog, monkeypatch):
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: (_ for _ in []).throw(RuntimeError("boom")))

    client = _make_mqtt()
    client._things = ["m1"]
    with caplog.at_level(logging.ERROR):
        client._dispatch(_FakeMessage("/device/m1/pboutput", b"x"))
    assert "Error handling MQTT message" in caplog.text


# ---------------------------------------------------------------------------
# _handle_pboutput (lines 258-265)
# ---------------------------------------------------------------------------


def test_handle_pboutput_without_map_data(monkeypatch):
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: b"")
    monkeypatch.setattr(proto, "decode_pboutput", lambda b: {"state": 2})
    monkeypatch.setattr(proto, "decode_map_response", lambda b: None)

    states = {}
    client = LymowMqttClient("h", "eu-west-1", lambda t, s: states.update(s), lambda *a: None)
    client._handle_pboutput("m1", b"raw")
    assert states == {"state": 2}
    assert "mapData" not in states


def test_handle_pboutput_with_map_data(monkeypatch):
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: b"")
    monkeypatch.setattr(proto, "decode_pboutput", lambda b: {"state": 1})
    monkeypatch.setattr(proto, "decode_map_response", lambda b: {"zones": []})

    states = {}
    client = LymowMqttClient("h", "eu-west-1", lambda t, s: states.update(s), lambda *a: None)
    client._handle_pboutput("m1", b"raw")
    assert states["mapData"] == {"zones": []}


def test_handle_pboutput_with_path_data(monkeypatch):
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: b"")
    monkeypatch.setattr(proto, "decode_pboutput", lambda b: {"state": 1})
    monkeypatch.setattr(proto, "decode_map_response", lambda b: {})
    monkeypatch.setattr(proto, "decode_path_response", lambda b: {"segments": [[{"x": 1.0, "y": 2.0}]]})

    states = {}
    client = LymowMqttClient("h", "eu-west-1", lambda t, s: states.update(s), lambda *a: None)
    client._handle_pboutput("m1", b"raw")
    assert states["pathData"] == {"segments": [[{"x": 1.0, "y": 2.0}]]}


def test_handle_pboutput_path_reply_skips_map_decode(monkeypatch):
    """A path reply must not run the map decoder (it would mis-read the path's
    point list as bogus zones and corrupt the map)."""
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: b"")
    monkeypatch.setattr(proto, "decode_pboutput", lambda b: {})
    map_calls = []
    monkeypatch.setattr(proto, "decode_map_response", lambda b: map_calls.append(1) or {"goZones": [1]})
    monkeypatch.setattr(proto, "decode_path_response", lambda b: {"segments": [[{"x": 1.0, "y": 2.0}]]})

    states = {}
    client = LymowMqttClient("h", "eu-west-1", lambda t, s: states.update(s), lambda *a: None)
    client._handle_pboutput("m1", b"raw")
    assert "mapData" not in states
    assert map_calls == []


def test_handle_pboutput_falls_through_to_map_without_path(monkeypatch):
    """A non-path reply falls through to the map decoder."""
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: b"")
    monkeypatch.setattr(proto, "decode_pboutput", lambda b: {})
    monkeypatch.setattr(proto, "decode_map_response", lambda b: {"goZones": [{"hashId": "z1"}]})
    monkeypatch.setattr(proto, "decode_path_response", lambda b: {"segments": []})

    states = {}
    client = LymowMqttClient("h", "eu-west-1", lambda t, s: states.update(s), lambda *a: None)
    client._handle_pboutput("m1", b"raw")
    assert states["mapData"] == {"goZones": [{"hashId": "z1"}]}
    assert "pathData" not in states


# ---------------------------------------------------------------------------
# _handle_notify (lines 268-273)
# ---------------------------------------------------------------------------


def test_handle_notify_reports_online():
    import json

    events = {}
    client = LymowMqttClient("h", "eu-west-1", lambda *a: None, lambda t, o: events.update({t: o}))
    client._handle_notify("m1", json.dumps({"robotState": "Online"}).encode())
    assert events == {"m1": True}


def test_handle_notify_reports_offline():
    import json

    events = {}
    client = LymowMqttClient("h", "eu-west-1", lambda *a: None, lambda t, o: events.update({t: o}))
    client._handle_notify("m1", json.dumps({"robotState": "Offline"}).encode())
    assert events == {"m1": False}


def test_handle_notify_invalid_json_is_ignored():
    client = _make_mqtt()
    client._handle_notify("m1", b"{{not json}}")  # Should not raise
