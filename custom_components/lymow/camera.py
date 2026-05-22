"""Camera entity for the Lymow robot's onboard camera.

The robot exposes its camera as a **local RTSP h264 stream** on the LAN
(``rtsp://<robot_ip>:10022/h264ESVideoTest``, 640x480). Home Assistant's
stream component serves the live view; still images are grabbed with ffmpeg.

This is the path that works for a LAN client like HA. The AWS KVS WebRTC flow
(see api.py) is the app's *remote* path — the robot only acts as the WebRTC
master for the app's own authenticated cloud session, so it isn't reachable
from a standalone client. Locally, RTSP is simpler and reliable.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, RTSP_PATH, RTSP_PORT
from .coordinator import LymowCoordinator

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


class LymowCamera(CoordinatorEntity[LymowCoordinator], Camera):
    """The robot's onboard camera, served from its local RTSP h264 stream."""

    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_icon = "mdi:cctv"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        Camera.__init__(self)
        self._thing_name: str = device["deviceThingName"]
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} Camera"
        self._attr_unique_id = f"{self._thing_name}_camera"

    def _stream_url(self) -> str | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        ip = _robot_ip(data)
        return f"rtsp://{ip}:{RTSP_PORT}/{RTSP_PATH}" if ip else None

    async def stream_source(self) -> str | None:
        """RTSP source for HA's stream component (None until the robot's IP is known)."""
        return self._stream_url()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        url = self._stream_url()
        if not url:
            return None
        return await async_get_image(self.coordinator.hass, url, width=width, height=height)
