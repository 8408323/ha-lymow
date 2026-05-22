"""Tests for the Lymow RTSP camera entity.

The robot serves its camera as a local RTSP h264 stream
(rtsp://<ip>:10022/h264ESVideoTest). The entity exposes that to HA's stream
component and grabs stills via ffmpeg; here ffmpeg is stubbed.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

# conftest loads lymow.camera into sys.modules via its importlib harness.
camera = sys.modules["lymow.camera"]

THING = "device_x"
RTSP = "rtsp://192.168.1.85:10022/h264ESVideoTest"


class _Coord:
    def __init__(self, data, devices=None):
        self.data = data
        self.hass = MagicMock()
        self.devices = devices or []


def _entity(data, device=None):
    coord = _Coord({THING: data})
    return camera.LymowCamera(coord, device or {"deviceThingName": THING, "deviceName": "Mower"})


class TestRobotIp:
    def test_prefers_ipaddress_then_falls_back(self):
        assert camera._robot_ip({"ipAddress": "192.168.1.85"}) == "192.168.1.85"
        assert camera._robot_ip({"wifiIp": "192.168.1.9"}) == "192.168.1.9"
        assert camera._robot_ip({"ip_address": "10.0.0.4"}) == "10.0.0.4"

    def test_none_when_absent_or_blank(self):
        assert camera._robot_ip({}) is None
        assert camera._robot_ip({"ipAddress": ""}) is None
        assert camera._robot_ip({"ipAddress": 123}) is None


class TestLymowCamera:
    def test_identity_and_features(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        assert ent._attr_unique_id == f"{THING}_camera"
        assert ent._attr_name == "Mower Camera"
        assert ent._attr_supported_features is camera.CameraEntityFeature.STREAM

    def test_name_falls_back_to_sn_then_thing(self):
        assert _entity({}, {"deviceThingName": THING, "sn": "SN1"})._attr_name == "SN1 Camera"
        assert _entity({}, {"deviceThingName": THING})._attr_name == f"{THING} Camera"

    async def test_stream_source_builds_rtsp_url(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        assert await ent.stream_source() == RTSP

    async def test_stream_source_none_without_ip(self):
        ent = _entity({"battery": 50})
        assert await ent.stream_source() is None

    async def test_stream_source_none_when_no_device_data(self):
        coord = _Coord({})  # no entry for THING
        ent = camera.LymowCamera(coord, {"deviceThingName": THING})
        assert await ent.stream_source() is None

    async def test_stream_source_none_when_data_is_none(self):
        coord = _Coord(None)
        ent = camera.LymowCamera(coord, {"deviceThingName": THING})
        assert await ent.stream_source() is None

    async def test_camera_image_grabs_via_ffmpeg(self, monkeypatch):
        ent = _entity({"ipAddress": "192.168.1.85"})
        captured = {}

        async def _get_image(hass, source, **kw):
            captured["source"] = source
            captured["kw"] = kw
            return b"JPEGBYTES"

        monkeypatch.setattr(camera, "async_get_image", _get_image)
        out = await ent.async_camera_image(width=640, height=480)
        assert out == b"JPEGBYTES"
        assert captured["source"] == RTSP
        assert captured["kw"] == {"width": 640, "height": 480}

    async def test_camera_image_none_without_ip(self, monkeypatch):
        ent = _entity({})
        monkeypatch.setattr(camera, "async_get_image", AsyncMock(side_effect=AssertionError("should not be called")))
        assert await ent.async_camera_image() is None


class TestSetupEntry:
    async def test_adds_camera_per_device(self):
        coord = _Coord({}, devices=[{"deviceThingName": "a"}, {"deviceThingName": "b"}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        added = []
        await camera.async_setup_entry(hass, MagicMock(entry_id="e1"), lambda e: added.extend(e))
        assert len(added) == 2

    async def test_no_devices_adds_nothing(self):
        coord = _Coord({}, devices=[])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        add = MagicMock()
        await camera.async_setup_entry(hass, MagicMock(entry_id="e1"), add)
        add.assert_not_called()
