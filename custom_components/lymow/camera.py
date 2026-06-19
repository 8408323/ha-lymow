"""Camera entity for the Lymow robot's onboard camera.

The robot exposes its camera as a local RTSP H.264 stream on the LAN
(``rtsp://<robot_ip>:10022/h264ESVideoTest``, 640×480).

Green-frame root cause: LIVE555 doesn't include SPS/PPS in the SDP DESCRIBE
response — it sends them inline just before the first IDR frame.  Both HA's
libav and go2rtc start decoding before those arrive → green frames.

Fix: spawn FFmpeg with generous analyzeduration/probesize, wait for the first
IDR+SPS+PPS, then re-mux to a continuous MPEG-TS stream piped to an asyncio
TCP server on localhost.  stream_source() returns that HTTP URL so go2rtc gets
a clean, unbroken feed with no HLS segment boundaries.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature, StreamType
from homeassistant.components.ffmpeg import async_get_image, get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, RTSP_PATH, RTSP_PORT
from .coordinator import LymowCoordinator
from .entity import lymow_device_info

_LOGGER = logging.getLogger(__name__)

_IP_KEYS = ("ipAddress", "wifiIp", "ip_address")
_CHUNK = 32768  # bytes per FFmpeg stdout read


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [LymowCamera(coordinator, device) for device in coordinator.devices]
    if entities:
        async_add_entities(entities)


def _robot_ip(data: dict[str, Any]) -> str | None:
    for key in _IP_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class LymowCamera(CoordinatorEntity[LymowCoordinator], Camera):
    """Robot camera, re-streamed as continuous MPEG-TS over localhost HTTP.

    FFmpeg connects to the robot RTSP with a 5-second probe window so it
    captures the SPS/PPS that LIVE555 sends inline before the first IDR.
    The remuxed MPEG-TS bytes are piped into an asyncio TCP server; each
    HTTP client (go2rtc) receives a broadcast of every chunk.  No segment
    files, no periodic stutter at segment boundaries.
    """

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_icon = "mdi:cctv"
    _attr_frontend_stream_type = StreamType.HLS

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Camera"
        self._attr_unique_id = f"{self._thing_name}_camera"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

        self._proxy_proc: asyncio.subprocess.Process | None = None
        self._ts_server: asyncio.Server | None = None
        self._ts_port: int | None = None
        self._ts_reader_task: asyncio.Task | None = None
        # Each connected HTTP client gets its own asyncio.Queue of TS chunks.
        self._ts_clients: list[asyncio.Queue[bytes | None]] = []
        self._proxy_ip: str | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._stream_url():
            await self._start_proxy()
        self.async_on_remove(self.coordinator.async_add_listener(self._on_coordinator_update))

    async def async_will_remove_from_hass(self) -> None:
        await self._stop_proxy()

    def _on_coordinator_update(self) -> None:
        current_ip = _robot_ip((self.coordinator.data or {}).get(self._thing_name) or {})
        if not current_ip:
            return
        if self._proxy_proc is None or current_ip != self._proxy_ip:
            self.hass.async_create_task(self._restart_proxy())

    async def _restart_proxy(self) -> None:
        await self._stop_proxy()
        await self._start_proxy()

    # ── proxy management ───────────────────────────────────────────────────

    async def _start_proxy(self) -> None:
        rtsp_url = self._stream_url()
        if not rtsp_url:
            return

        self._ts_port = _free_port()
        self._proxy_ip = _robot_ip((self.coordinator.data or {}).get(self._thing_name) or {})
        self._ts_clients = []

        self._ts_server = await asyncio.start_server(
            self._handle_ts_client,
            "127.0.0.1",
            self._ts_port,
        )

        try:
            ffmpeg_bin = get_ffmpeg_manager(self.hass).binary
        except Exception:
            ffmpeg_bin = "ffmpeg"

        try:
            self._proxy_proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin,
                "-loglevel",
                "warning",
                "-rtsp_transport",
                "tcp",
                "-analyzeduration",
                "5000000",
                "-probesize",
                "5000000",
                "-i",
                rtsp_url,
                "-c:v",
                "copy",
                "-f",
                "mpegts",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self._ts_reader_task = self.hass.async_create_task(self._read_ffmpeg_stdout())
            _LOGGER.debug("Lymow MPEG-TS proxy started (pid=%s) for %s", self._proxy_proc.pid, rtsp_url)
        except Exception as exc:
            _LOGGER.warning("Lymow proxy could not start (%s); falling back to direct RTSP", exc)
            self._ts_server.close()
            self._ts_server = None
            self._ts_port = None
            self._ts_clients = []
            self._proxy_ip = None

    async def _stop_proxy(self) -> None:
        if self._ts_reader_task is not None:
            self._ts_reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ts_reader_task
            self._ts_reader_task = None

        if self._proxy_proc is not None:
            with contextlib.suppress(ProcessLookupError):
                self._proxy_proc.terminate()
                await self._proxy_proc.wait()
            self._proxy_proc = None

        if self._ts_server is not None:
            self._ts_server.close()
            await self._ts_server.wait_closed()
            self._ts_server = None

        for q in self._ts_clients:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)
        self._ts_clients = []
        self._ts_port = None
        self._proxy_ip = None

    async def _handle_ts_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve one HTTP client with the live MPEG-TS stream."""
        try:
            # Consume all HTTP request headers before responding.
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line.lower().startswith(b"get "):
                return
            while True:
                hdr = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if hdr in (b"\r\n", b"\n", b""):
                    break
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: video/mp2t\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: keep-alive\r\n"
                b"\r\n"
            )
            await writer.drain()
        except Exception:
            writer.close()
            return

        # Register as a broadcast recipient and stream until disconnected.
        q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        self._ts_clients.append(q)
        try:
            while True:
                chunk = await asyncio.wait_for(q.get(), timeout=15.0)
                if chunk is None:
                    break
                writer.write(chunk)
                await writer.drain()
        except (asyncio.TimeoutError, ConnectionError, Exception):
            pass
        finally:
            if q in self._ts_clients:
                self._ts_clients.remove(q)
            writer.close()

    async def _read_ffmpeg_stdout(self) -> None:
        """Read FFmpeg MPEG-TS output and broadcast to every active client."""
        assert self._proxy_proc is not None
        assert self._proxy_proc.stdout is not None
        try:
            while True:
                chunk = await self._proxy_proc.stdout.read(_CHUNK)
                if not chunk:
                    break
                slow: list[asyncio.Queue[bytes | None]] = []
                for q in list(self._ts_clients):
                    try:
                        q.put_nowait(chunk)
                    except asyncio.QueueFull:
                        slow.append(q)
                for q in slow:
                    if q in self._ts_clients:
                        self._ts_clients.remove(q)
        finally:
            for q in list(self._ts_clients):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)

    # ── camera interface ───────────────────────────────────────────────────

    def _stream_url(self) -> str | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        ip = _robot_ip(data)
        return f"rtsp://{ip}:{RTSP_PORT}/{RTSP_PATH}" if ip else None

    async def stream_source(self) -> str | None:
        """MPEG-TS proxy URL when available, raw RTSP as fallback."""
        if self._ts_port is not None and self._proxy_proc is not None:
            return f"http://127.0.0.1:{self._ts_port}/stream.ts"
        return self._stream_url()

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {"frontend_stream_type": "hls"}
        url = self._stream_url()
        if url:
            attrs["rtsp_url"] = url
        if self._ts_port is not None and self._proxy_proc is not None:
            attrs["mpegts_proxy_url"] = f"http://127.0.0.1:{self._ts_port}/stream.ts"
        return attrs

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        url = self._stream_url()
        if not url:
            return None
        return await async_get_image(self.coordinator.hass, url, width=width, height=height)
