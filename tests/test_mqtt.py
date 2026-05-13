"""Tests for Lymow MQTT client."""
from __future__ import annotations

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
