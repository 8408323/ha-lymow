"""Resilience tests for LymowMqttClient — failures the happy-path tests don't trip.

Cloud MQTT is the flakiest surface this integration owns: AWS Cognito session
tokens expire (~1 h), the WebSocket can drop on any network blip, and the
broker can send back malformed JSON for /notify-app or an unwrappable envelope
for /pboutput. These tests pin down what must happen in each case so a future
refactor can't silently regress the recovery behaviour.

No real network: every aiomqtt.Client is replaced with a fake whose
publish/subscribe/iterator behaviour the test controls.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from lymow.mqtt import LymowMqttClient, aiomqtt

# ---------------------------------------------------------------------------
# Local helpers (kept separate from test_mqtt.py's helpers to avoid coupling)
# ---------------------------------------------------------------------------


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._items):
            raise StopAsyncIteration
        v = self._items[self._i]
        self._i += 1
        # Yield control so other tasks get a chance to schedule (race tests rely on this).
        await asyncio.sleep(0)
        if isinstance(v, BaseException):
            raise v
        return v


class _FakeMessage:
    def __init__(self, topic: str, payload: bytes = b""):
        self.topic = type("T", (), {"__str__": lambda s: topic})()
        self.payload = payload


def _make_mqtt(on_state=None, on_online=None) -> tuple[LymowMqttClient, list[tuple[str, dict]], list[tuple[str, bool]]]:
    """Return (client, captured state updates, captured online updates)."""
    states: list[tuple[str, dict]] = []
    onlines: list[tuple[str, bool]] = []
    client = LymowMqttClient(
        host="iot.eu-west-1.amazonaws.com",
        region="eu-west-1",
        on_state=on_state or (lambda t, s: states.append((t, s))),
        on_online=on_online or (lambda t, o: onlines.append((t, o))),
    )
    return client, states, onlines


# ---------------------------------------------------------------------------
# Reconnect / credential refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_signs_url_with_new_credentials(monkeypatch):
    """When the coordinator detects an expiring token and calls reconnect(),
    the new access_key / secret / session_token must reach the SigV4 URL builder
    — otherwise we'd just reconnect with stale (about-to-expire) creds."""
    sign_calls: list[dict] = []

    real_builder = None  # captured below

    def _spy_builder(host, region, access_key, secret_key, session_token):
        sign_calls.append(
            {
                "host": host,
                "region": region,
                "access_key": access_key,
                "secret_key": secret_key,
                "session_token": session_token,
            }
        )
        return real_builder(host, region, access_key, secret_key, session_token)

    import sys

    mqtt_mod = sys.modules["lymow.mqtt"]
    real_builder = mqtt_mod.build_presigned_ws_path
    monkeypatch.setattr(mqtt_mod, "build_presigned_ws_path", _spy_builder)

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = _AsyncIter([])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def subscribe(self, topic, qos):
            pass

    monkeypatch.setattr(aiomqtt, "Client", FakeClient)

    client, _, _ = _make_mqtt()
    await client.connect(["thing-1"], "OLD_ACCESS", "OLD_SECRET", "OLD_TOKEN")
    await client.reconnect("NEW_ACCESS", "NEW_SECRET", "NEW_TOKEN")
    await client.disconnect()

    assert len(sign_calls) == 2
    assert sign_calls[0]["access_key"] == "OLD_ACCESS"
    assert sign_calls[0]["session_token"] == "OLD_TOKEN"
    assert sign_calls[1]["access_key"] == "NEW_ACCESS"
    assert sign_calls[1]["secret_key"] == "NEW_SECRET"
    assert sign_calls[1]["session_token"] == "NEW_TOKEN"


@pytest.mark.asyncio
async def test_reconnect_resubscribes_to_all_things(monkeypatch):
    """Reconnect must re-issue subscriptions for every device — otherwise the
    new connection would be silent (no pboutput / notify-app delivery)."""
    subscribed: list[str] = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.messages = _AsyncIter([])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def subscribe(self, topic, qos):
            subscribed.append(topic)

    monkeypatch.setattr(aiomqtt, "Client", FakeClient)
    client, _, _ = _make_mqtt()
    await client.connect(["thing-a", "thing-b"], "k", "s", None)
    pre = list(subscribed)
    await client.reconnect("k2", "s2", None)
    post = subscribed[len(pre) :]
    await client.disconnect()

    # Both things resubscribed to both topic suffixes on reconnect.
    assert sorted(post) == [
        "/device/thing-a/notify-app",
        "/device/thing-a/pboutput",
        "/device/thing-b/notify-app",
        "/device/thing-b/pboutput",
    ]


# ---------------------------------------------------------------------------
# Mid-stream broker errors — the listen loop must exit cleanly, not propagate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_listen_loop_swallows_mqtt_error_mid_stream(caplog):
    """If the broker drops the WebSocket mid-stream, aiomqtt raises MqttError on
    the next __anext__. The loop must log and exit cleanly so the coordinator's
    reconnect logic can take over — not bubble the exception out."""

    class FakeClient:
        def __init__(self):
            self.messages = _AsyncIter(
                [
                    _FakeMessage("/device/t1/notify-app", b'{"robotState":"online"}'),
                    aiomqtt.MqttError("broker disconnected"),
                ]
            )

    client, _, onlines = _make_mqtt()
    client._things = ["t1"]
    client._client = FakeClient()
    with caplog.at_level(logging.ERROR):
        await client._listen_loop()  # must not raise

    # First message delivered before the disconnect happened.
    assert onlines == [("t1", True)]
    # Disconnect is logged as a listen-loop exception.
    assert "MQTT listen loop exited unexpectedly" in caplog.text


@pytest.mark.asyncio
async def test_dispatch_swallows_handler_exception_in_pboutput(monkeypatch, caplog):
    """A pboutput handler that blows up (e.g. unwrap_envelope raises on garbage
    bytes) must not kill the dispatch loop — the dispatch try/except logs and
    continues so the next message is still delivered."""
    import sys

    proto = sys.modules["lymow.protocol"]
    monkeypatch.setattr(proto, "unwrap_envelope", lambda b: (_ for _ in ()).throw(ValueError("bad envelope")))

    client, states, _ = _make_mqtt()
    client._things = ["t1"]
    with caplog.at_level(logging.ERROR):
        client._dispatch(_FakeMessage("/device/t1/pboutput", b"garbage"))

    assert states == []  # nothing pushed to coordinator
    assert "Error handling MQTT message" in caplog.text


# ---------------------------------------------------------------------------
# Malformed broker payloads — /notify-app robustness
# ---------------------------------------------------------------------------


def test_notify_handler_treats_missing_robotstate_as_offline():
    """A notify-app message with valid JSON but no robotState key must default
    to offline rather than crashing — the broker has historically sent shape-
    drifted payloads on firmware updates."""
    client, _, onlines = _make_mqtt()
    client._handle_notify("t1", b'{"someOtherField": 1}')
    assert onlines == [("t1", False)]


def test_notify_handler_handles_non_string_robotstate():
    """If robotState arrives as a non-string (list, dict, int), the str() coerce
    + lower() must not raise. Any value that doesn't lowercase to 'online' is
    treated as offline."""
    client, _, onlines = _make_mqtt()
    # int 1 stringifies to "1" — not "online" → offline
    client._handle_notify("t1", b'{"robotState": 1}')
    # list stringifies to "[1, 2]" → offline
    client._handle_notify("t1", b'{"robotState": [1, 2]}')
    assert onlines == [("t1", False), ("t1", False)]


def test_notify_handler_treats_uppercase_online_as_online():
    """Robot/firmware variants may send 'ONLINE' instead of 'online'.
    Casefolded match must accept both."""
    client, _, onlines = _make_mqtt()
    client._handle_notify("t1", b'{"robotState": "ONLINE"}')
    assert onlines == [("t1", True)]


def test_notify_handler_silently_drops_invalid_json():
    """Already covered elsewhere — pin the contract: bad JSON → no callback,
    no exception (the dispatch try/except is not enough; _handle_notify owns it
    so a garbled payload from one device doesn't affect the loop)."""
    client, _, onlines = _make_mqtt()
    client._handle_notify("t1", b"not-json-at-all")
    assert onlines == []


# ---------------------------------------------------------------------------
# Async-publish during reconnect race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_command_dropped_silently_when_reconnecting(caplog):
    """A publish_command (fire-and-forget) issued while self._client is None
    (between disconnect and connect during reconnect) must NOT raise — the
    coordinator's button/service handlers don't await it, so an exception
    would propagate into HA's exception logger as a noisy unhandled task."""
    client, _, _ = _make_mqtt()
    assert client._client is None  # mid-reconnect, no client yet

    with caplog.at_level(logging.WARNING):
        client.publish_command("t1", b"\x01\x02")
        # Give the scheduled coroutine a tick to run (if it were scheduled).
        await asyncio.sleep(0)
    assert "MQTT not connected" in caplog.text


@pytest.mark.asyncio
async def test_async_publish_command_dropped_silently_when_reconnecting(caplog):
    """Same contract as publish_command, but for the awaitable variant — must
    return cleanly without raising or hanging."""
    client, _, _ = _make_mqtt()
    with caplog.at_level(logging.WARNING):
        await client.async_publish_command("t1", b"\x01\x02")
    assert "MQTT not connected" in caplog.text


# ---------------------------------------------------------------------------
# Connect failures — different exception classes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_propagates_oserror_after_cleanup(monkeypatch, caplog):
    """OSError on subscribe (e.g. TCP RST after WebSocket upgrade) must propagate
    to the caller so async_setup_entry raises ConfigEntryNotReady — but only
    after __aexit__ cleanup, not as a leaked client."""
    exited = {"v": False}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            exited["v"] = True

        async def subscribe(self, topic, qos):
            raise OSError("connection reset")

    monkeypatch.setattr(aiomqtt, "Client", FakeClient)
    client, _, _ = _make_mqtt()
    with caplog.at_level(logging.WARNING):
        with pytest.raises(OSError, match="connection reset"):
            await client.connect(["t1"], "k", "s", None)

    assert exited["v"] is True  # __aexit__ ran for cleanup
    assert client._client is None  # never stored
    assert "MQTT connect setup failed" in caplog.text


@pytest.mark.asyncio
async def test_connect_propagates_asyncio_timeout(monkeypatch):
    """A TimeoutError during the connect handshake must cleanup and propagate."""
    exited = {"v": False}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            exited["v"] = True

        async def subscribe(self, topic, qos):
            raise asyncio.TimeoutError()

    monkeypatch.setattr(aiomqtt, "Client", FakeClient)
    client, _, _ = _make_mqtt()
    with pytest.raises(asyncio.TimeoutError):
        await client.connect(["t1"], "k", "s", None)
    assert exited["v"] is True
    assert client._client is None
