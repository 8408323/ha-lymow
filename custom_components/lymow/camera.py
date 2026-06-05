"""Camera entity for the Lymow robot's onboard camera.

The robot exposes its camera as a **local RTSP h264 stream** on the LAN
(``rtsp://<robot_ip>:10022/h264ESVideoTest``, 640x480).

Root cause of the green-frame problem: the robot's LIVE555 RTSP server does
not include SPS/PPS parameter sets in the SDP DESCRIBE response — it sends
them inline just before the first IDR frame.  HA's stream component passes the
raw RTSP URL to libav with a probe timeout too short to wait for that IDR,
causing the decoder to start without the parameter sets it needs.

Fix: on entity startup we spawn our own FFmpeg process with generous
``analyzeduration`` / ``probesize`` flags (enough to catch the first IDR),
remux stream segments into an HLS playlist in a temp dir, and serve that
playlist from a localhost HTTP server.  ``stream_source()`` returns the local
HLS URL so HA's stream component gets clean, keyframe-aligned segments.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import socket
import tempfile
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature, StreamType
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, RTSP_PATH, RTSP_PORT
from .coordinator import LymowCoordinator
from .entity import lymow_device_info

_LOGGER = logging.getLogger(__name__)

# Robot-state keys that may carry the LAN IP, in priority order.
_IP_KEYS = ("ipAddress", "wifiIp", "ip_address")


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
    """The robot's onboard camera, served via a local FFmpeg HLS proxy.

    The proxy bridges the gap between LIVE555's late-arriving parameter sets
    and HA's short libav probe window.  Segments are served from localhost so
    the stream component never touches the RTSP stream directly.
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
        self._http_server: HTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self._hls_dir: str | None = None
        self._hls_port: int | None = None
        self._proxy_ip: str | None = None  # robot IP the running proxy is tied to

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._stream_url():
            await self._start_proxy()
        self.async_on_remove(
            self.coordinator.async_add_listener(self._on_coordinator_update)
        )

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

        self._hls_dir = tempfile.mkdtemp(prefix="lymow_hls_")
        self._hls_port = _free_port()
        self._proxy_ip = _robot_ip((self.coordinator.data or {}).get(self._thing_name) or {})
        hls_dir = self._hls_dir

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a: Any, **kw: Any) -> None:
                super().__init__(*a, directory=hls_dir, **kw)

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
                pass  # suppress per-request noise from HA logs

        self._http_server = HTTPServer(("127.0.0.1", self._hls_port), _Handler)
        self._http_thread = threading.Thread(
            target=self._http_server.serve_forever,
            daemon=True,
            name=f"lymow_hls_{self._thing_name}",
        )
        self._http_thread.start()

        # Generous analyzeduration/probesize so FFmpeg waits for the first
        # IDR+SPS+PPS that LIVE555 sends inline before the stream starts.
        # -c:v copy remuxes without re-encoding: minimal CPU, no quality loss.
        self._proxy_proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-loglevel", "warning",
            "-rtsp_transport", "tcp",
            "-analyzeduration", "5000000",
            "-probesize", "5000000",
            "-i", rtsp_url,
            "-c:v", "copy",
            "-f", "hls",
            "-hls_time", "1",
            "-hls_list_size", "3",
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", os.path.join(self._hls_dir, "seg%03d.ts"),
            os.path.join(self._hls_dir, "stream.m3u8"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        _LOGGER.debug("Lymow HLS proxy started (pid=%s) for %s", self._proxy_proc.pid, rtsp_url)

    async def _stop_proxy(self) -> None:
        if self._proxy_proc is not None:
            with contextlib.suppress(ProcessLookupError):
                self._proxy_proc.terminate()
                await self._proxy_proc.wait()
            self._proxy_proc = None

        if self._http_server is not None:
            self._http_server.shutdown()
            self._http_server = None
            self._http_thread = None

        if self._hls_dir and os.path.isdir(self._hls_dir):
            shutil.rmtree(self._hls_dir, ignore_errors=True)
            self._hls_dir = None

        self._hls_port = None
        self._proxy_ip = None

    # ── camera interface ───────────────────────────────────────────────────

    def _stream_url(self) -> str | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        ip = _robot_ip(data)
        return f"rtsp://{ip}:{RTSP_PORT}/{RTSP_PATH}" if ip else None

    async def stream_source(self) -> str | None:
        """Local HLS playlist when the proxy is running; raw RTSP as fallback."""
        if self._hls_port is not None and self._hls_dir is not None:
            return f"http://127.0.0.1:{self._hls_port}/stream.m3u8"
        return self._stream_url()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        # Fast path: extract a frame from the latest HLS segment already on
        # disk. Avoids opening a new RTSP connection per snapshot request —
        # ffmpeg reads a local file in ~100-300 ms vs 2-5 s over RTSP.
        if self._hls_dir is not None:
            import glob as _glob
            segs = sorted(_glob.glob(os.path.join(self._hls_dir, "seg*.ts")))
            if segs:
                frame = await async_get_image(self.coordinator.hass, segs[-1], width=width, height=height)
                if frame:
                    return frame
        # Fallback: grab directly from RTSP (used before proxy starts or if it crashed)
        url = self._stream_url()
        if not url:
            return None
        return await async_get_image(self.coordinator.hass, url, width=width, height=height)
