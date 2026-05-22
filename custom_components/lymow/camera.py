"""Camera entity for the Lymow robot's onboard camera.

The robot is the WebRTC MASTER on an AWS Kinesis Video Streams signaling
channel; we connect as a VIEWER and decode a single frame on demand
(``async_camera_image``), reusing the handshake proven in
``scripts/camera_feed_test.py``. Live WebRTC streaming to the frontend is a
later step; this gives a refreshable still in the HA UI now.

The robot only joins the channel as MASTER when it is awake **and off the
charging dock** — docked & charging it stays MQTT-online but never starts the
camera, so the handshake yields no frame and we return ``None`` (HA shows the
camera as unavailable rather than erroring).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LymowCoordinator

if TYPE_CHECKING:
    from .api import LymowApiClient

_LOGGER = logging.getLogger(__name__)

# Skip the first frames so the decoder/keyframe settles before we snapshot.
_FRAME_SKIP = 30
_HANDSHAKE_TIMEOUT = 20.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [LymowCamera(coordinator, device) for device in coordinator.devices]
    if entities:
        async_add_entities(entities)


def _build_ice_servers(session: dict[str, Any]) -> list[Any]:
    from aiortc import RTCIceServer

    servers = []
    for entry in session.get("iceServers") or []:
        if not isinstance(entry, dict):
            continue
        urls = entry.get("Uris") or entry.get("uris") or []
        servers.append(RTCIceServer(urls=urls, username=entry.get("Username"), credential=entry.get("Password")))
    return servers


async def async_grab_camera_frame(
    api: LymowApiClient,
    session: dict[str, Any],
    ws_session: aiohttp.ClientSession,
    *,
    timeout: float = _HANDSHAKE_TIMEOUT,
) -> bytes | None:
    """Run the KVS viewer handshake and return one JPEG frame, or None.

    ``None`` means no frame arrived in time — almost always because the robot
    is docked and not acting as MASTER, which is expected, not an error.
    """
    from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
    from aiortc.sdp import candidate_from_sdp

    arn = session.get("channelARN")
    creds = session.get("credentials")
    region = session.get("region")
    wss = (session.get("signalingEndpoints") or {}).get("WSS")
    if not (arn and isinstance(creds, dict) and wss):
        return None

    pc = RTCPeerConnection(RTCConfiguration(iceServers=_build_ice_servers(session)))
    pc.addTransceiver("video", direction="recvonly")
    got = asyncio.Event()
    holder: dict[str, Any] = {}

    @pc.on("track")
    def _on_track(track: Any) -> None:  # noqa: ANN401
        if track.kind != "video":
            return

        async def _grab() -> None:
            try:
                frame = None
                for _ in range(_FRAME_SKIP):
                    frame = await track.recv()
                if frame is not None:
                    holder["image"] = frame.to_image()
            except Exception as err:  # noqa: BLE001 — a failed grab just yields no image
                _LOGGER.debug("camera frame grab failed: %s", err)
            finally:
                got.set()

        asyncio.ensure_future(_grab())  # noqa: RUF006 — lifetime bounded by got/timeout below

    client_id = f"ha-lymow-{uuid4().hex[:8]}"
    url = api.presign_signaling_url(wss, arn, client_id, creds, region=region)
    try:
        async with ws_session.ws_connect(url, max_msg_size=0) as ws:
            await pc.setLocalDescription(await pc.createOffer())
            # aiortc has no trickle ICE — wait for gathering so the offer SDP
            # carries our candidates (otherwise the master has nowhere to send).
            while pc.iceGatheringState != "complete":
                await asyncio.sleep(0.1)
            await ws.send_str(
                json.dumps(
                    {
                        "action": "SDP_OFFER",
                        "messagePayload": base64.b64encode(
                            json.dumps({"type": "offer", "sdp": pc.localDescription.sdp}).encode()
                        ).decode(),
                    }
                )
            )

            async def _signal_loop() -> None:
                async for msg in ws:
                    if msg.type is not aiohttp.WSMsgType.TEXT or not msg.data or not msg.data.strip():
                        continue
                    try:
                        envelope = json.loads(msg.data)
                    except ValueError:
                        continue
                    kind = envelope.get("messageType") or envelope.get("action")
                    raw_payload = envelope.get("messagePayload")
                    if not raw_payload:
                        continue
                    payload = json.loads(base64.b64decode(raw_payload).decode())
                    if kind == "SDP_ANSWER":
                        await pc.setRemoteDescription(RTCSessionDescription(sdp=payload["sdp"], type="answer"))
                    elif kind == "ICE_CANDIDATE" and payload.get("candidate"):
                        candidate = candidate_from_sdp(payload["candidate"].split(":", 1)[1])
                        candidate.sdpMid = payload.get("sdpMid")
                        candidate.sdpMLineIndex = payload.get("sdpMLineIndex")
                        await pc.addIceCandidate(candidate)

            loop_task = asyncio.ensure_future(_signal_loop())
            try:
                await asyncio.wait_for(got.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            finally:
                loop_task.cancel()
    finally:
        await pc.close()

    image = holder.get("image")
    if image is None:
        return None
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class LymowCamera(CoordinatorEntity[LymowCoordinator], Camera):
    """On-demand still from the robot's camera via the KVS WebRTC viewer flow."""

    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._thing_name: str = device["deviceThingName"]
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} Camera"
        self._attr_unique_id = f"{self._thing_name}_camera"

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        try:
            session = await self.coordinator.async_start_video_session(self._thing_name)
        except Exception as err:  # noqa: BLE001 — a transient cloud failure shouldn't raise in the UI
            _LOGGER.debug("camera session start failed for %s: %s", self._thing_name, err)
            return None
        if not isinstance(session, dict):
            return None
        ws_session = async_get_clientsession(self.coordinator.hass)
        return await async_grab_camera_frame(self.coordinator.client, session, ws_session)
