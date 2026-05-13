"""Tests for Lymow MQTT client."""
from __future__ import annotations

import sys

import pytest

from lymow.mqtt import LymowMqttClient

mqtt_module = sys.modules["lymow.mqtt"]


@pytest.mark.asyncio
async def test_connect_uses_timeout_and_cleans_up_on_subscribe_failure(monkeypatch):
    events: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            events["kwargs"] = kwargs

        async def __aenter__(self):
            events["entered"] = True
            return self

        async def __aexit__(self, exc_type, exc, tb):
            events["exited"] = True

        async def subscribe(self, topic, qos):
            events["topic"] = topic
            raise RuntimeError("subscribe failed")

    monkeypatch.setattr(mqtt_module.aiomqtt, "Client", FakeClient)

    client = LymowMqttClient(
        host="example.amazonaws.com",
        region="eu-west-1",
        on_state=lambda _thing, _state: None,
        on_online=lambda _thing, _online: None,
    )

    with pytest.raises(RuntimeError, match="subscribe failed"):
        await client.connect(
            ["mower-001"],
            access_key="access",
            secret_key="secret",
            session_token=None,
        )

    assert events["entered"] is True
    assert events["exited"] is True
    assert events["kwargs"]["timeout"] == 15
    assert client._client is None
