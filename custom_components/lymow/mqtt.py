"""AWS IoT MQTT over WebSocket for Lymow devices.

Connects to the AWS IoT endpoint using a SigV4-presigned WebSocket URL built
from temporary Cognito Identity credentials. Publishes protobuf commands and
delivers decoded state updates via a callback.

All AWS endpoint details were determined from traffic capture of the Android
app — specifically the WebSocket upgrade request to the IoT MQTT endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiomqtt

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SigV4 presigned WebSocket URL
# ---------------------------------------------------------------------------


def _hmac_sha256(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, date_str: str, region: str, service: str) -> bytes:
    k_date = _hmac_sha256(("AWS4" + secret_key).encode(), date_str)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    return _hmac_sha256(k_service, "aws4_request")


def build_presigned_ws_path(
    host: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str | None,
) -> str:
    """Return the /mqtt?... path for a SigV4-signed MQTT-over-WebSocket URL.

    Constructed to match the query-string format observed in traffic capture
    of the app connecting to the AWS IoT endpoint.
    """
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_str = now.strftime("%Y%m%d")
    service = "iotdevicegateway"
    method = "GET"
    uri = "/mqtt"

    credential_scope = f"{date_str}/{region}/{service}/aws4_request"
    credential = f"{access_key}/{credential_scope}"

    signed_headers = "host"
    # X-Amz-Security-Token must NOT be in the canonical query string — it is appended
    # after the signature. Signing it causes AWS IoT to return HTTP 403 on WebSocket upgrade.
    canonical_qs_parts: dict[str, str] = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": credential,
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "86400",
        "X-Amz-SignedHeaders": signed_headers,
    }

    canonical_qs = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(canonical_qs_parts.items()))

    canonical_request = (
        f"{method}\n{uri}\n{canonical_qs}\nhost:{host}\n\n{signed_headers}\n{hashlib.sha256(b'').hexdigest()}"
    )

    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    sig = _signing_key(secret_key, date_str, region, service)
    signature = hmac.new(sig, string_to_sign.encode(), hashlib.sha256).hexdigest()

    final_qs = canonical_qs + f"&X-Amz-Signature={signature}"
    if session_token:
        final_qs += f"&X-Amz-Security-Token={quote(session_token, safe='')}"
    return f"{uri}?{final_qs}"


# ---------------------------------------------------------------------------
# MQTT client
# ---------------------------------------------------------------------------

StateCallback = Callable[[str, dict[str, Any]], None]  # (thing_name, state_patch)
OnlineCallback = Callable[[str, bool], None]  # (thing_name, is_online)


class LymowMqttClient:
    """Manages a single AWS IoT WebSocket MQTT connection for all devices.

    Uses aiomqtt for a fully async message loop — no background thread,
    no call_soon_threadsafe marshalling needed.
    """

    def __init__(
        self,
        host: str,
        region: str,
        on_state: StateCallback,
        on_online: OnlineCallback,
    ) -> None:
        self._host = host
        self._region = region
        self._on_state = on_state
        self._on_online = on_online

        self._things: list[str] = []
        self._client: aiomqtt.Client | None = None
        self._listen_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(
        self,
        things: list[str],
        access_key: str,
        secret_key: str,
        session_token: str | None,
    ) -> None:
        """Connect to AWS IoT and start the async message listener."""
        self._things = things

        ws_path = build_presigned_ws_path(self._host, self._region, access_key, secret_key, session_token)

        client = aiomqtt.Client(
            hostname=self._host,
            port=443,
            identifier=f"lymow-ha-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": self._host},
            tls_params=aiomqtt.TLSParameters(),
            keepalive=30,
            timeout=15,
        )

        try:
            await client.__aenter__()
            for thing in things:
                await client.subscribe(f"/device/{thing}/pboutput", qos=1)
                await client.subscribe(f"/device/{thing}/notify-app", qos=1)
        except (aiomqtt.MqttError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.warning("MQTT connect setup failed: %s", err)
            await client.__aexit__(*sys.exc_info())
            raise
        self._client = client

        self._listen_task = asyncio.create_task(self._listen_loop())
        _LOGGER.debug("MQTT connected to %s", self._host)

    async def reconnect(
        self,
        access_key: str,
        secret_key: str,
        session_token: str | None,
    ) -> None:
        """Reconnect with fresh credentials (called when AWS creds expire)."""
        await self.disconnect()
        await self.connect(self._things, access_key, secret_key, session_token)

    async def disconnect(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._client:
            await self._client.__aexit__(None, None, None)
            self._client = None

    def publish_command(self, thing_name: str, pb_bytes: bytes) -> None:
        """Schedule a protobuf command publish (fire-and-forget from sync context)."""
        if not self._client:
            _LOGGER.warning("MQTT not connected — command dropped")
            return
        asyncio.ensure_future(self._publish(thing_name, pb_bytes))

    async def async_publish_command(self, thing_name: str, pb_bytes: bytes) -> None:
        """Publish a protobuf command and await completion."""
        from .protocol import wrap_envelope

        if not self._client:
            _LOGGER.warning("MQTT not connected — command dropped")
            return
        topic = f"/device/{thing_name}/pbinput"
        await self._client.publish(topic, wrap_envelope(pb_bytes), qos=1)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _publish(self, thing_name: str, pb_bytes: bytes) -> None:
        from .protocol import wrap_envelope

        if not self._client:
            return
        topic = f"/device/{thing_name}/pbinput"
        await self._client.publish(topic, wrap_envelope(pb_bytes), qos=1)

    async def _listen_loop(self) -> None:
        if not self._client:
            return
        try:
            async for message in self._client.messages:
                self._dispatch(message)
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.exception("MQTT listen loop exited unexpectedly")

    def _dispatch(self, message: aiomqtt.Message) -> None:
        topic = str(message.topic)
        payload = message.payload

        thing_name: str | None = None
        for thing in self._things:
            if f"/device/{thing}/" in topic:
                thing_name = thing
                break
        if thing_name is None:
            return

        try:
            if topic.endswith("/pboutput"):
                self._handle_pboutput(thing_name, payload)
            elif topic.endswith("/notify-app"):
                self._handle_notify(thing_name, payload)
        except Exception:
            _LOGGER.exception("Error handling MQTT message on %s", topic)

    def _handle_pboutput(self, thing_name: str, payload: bytes | str) -> None:
        from .protocol import decode_pboutput, unwrap_envelope

        pb_bytes = unwrap_envelope(payload)
        state = decode_pboutput(pb_bytes)
        self._on_state(thing_name, state)

    def _handle_notify(self, thing_name: str, payload: bytes | str) -> None:
        try:
            data = json.loads(payload)
            is_online = str(data.get("robotState", "")).lower() == "online"
        except Exception:
            return
        self._on_online(thing_name, is_online)
