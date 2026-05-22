"""Tests for the Lymow camera entity and KVS viewer frame grab.

aiortc is not installed in the test env (it's a manifest requirement only), so
we inject a fake ``aiortc`` package that the module's lazy imports pick up, plus
a scripted fake signaling WebSocket. The signaling/parse logic and the entity
glue are exercised; the media transport itself is faked.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import types
from unittest.mock import MagicMock

import aiohttp
import pytest

# conftest loads lymow.camera into sys.modules via its importlib harness.
camera = sys.modules["lymow.camera"]


# ── fake aiortc ───────────────────────────────────────────────────────────────
class _FakeImage:
    def save(self, buffer, format):  # noqa: A002 — mirrors PIL's signature
        buffer.write(b"JPEGBYTES")


class _FakeFrame:
    kind = "video"

    def __init__(self):
        self.width = 1280
        self.height = 720

    def to_image(self):
        return _FakeImage()


class _FakeTrack:
    def __init__(self, kind="video", frames=None, raise_after=None):
        self.kind = kind
        self._frames = frames if frames is not None else [_FakeFrame()] * 40
        self._i = 0
        self._raise_after = raise_after

    async def recv(self):
        if self._raise_after is not None and self._i >= self._raise_after:
            raise RuntimeError("track ended")
        self._i += 1
        return self._frames[min(self._i - 1, len(self._frames) - 1)]


class _FakePC:
    """Fires the registered ``track`` handler when the answer is set."""

    track_to_emit: _FakeTrack | None = _FakeTrack()

    def __init__(self, config=None):
        self._handlers: dict[str, object] = {}
        self.iceGatheringState = "complete"
        self.localDescription = types.SimpleNamespace(sdp="v=0 local")
        self.added_candidates: list = []
        self.closed = False

    def on(self, event):
        def deco(func):
            self._handlers[event] = func
            return func

        return deco

    def addTransceiver(self, kind, direction):
        pass

    async def createOffer(self):
        return types.SimpleNamespace(sdp="v=0 local", type="offer")

    async def setLocalDescription(self, desc):
        pass

    async def setRemoteDescription(self, desc):
        # Simulate media arriving: invoke the track handler.
        handler = self._handlers.get("track")
        if handler and type(self).track_to_emit is not None:
            handler(type(self).track_to_emit)

    async def addIceCandidate(self, candidate):
        self.added_candidates.append(candidate)

    async def close(self):
        self.closed = True


def _make_fake_aiortc():
    mod = types.ModuleType("aiortc")
    mod.RTCConfiguration = lambda iceServers=None: types.SimpleNamespace(iceServers=iceServers)
    mod.RTCIceServer = lambda urls=None, username=None, credential=None: types.SimpleNamespace(
        urls=urls, username=username, credential=credential
    )
    mod.RTCSessionDescription = lambda sdp=None, type=None: types.SimpleNamespace(sdp=sdp, type=type)  # noqa: A002
    mod.RTCPeerConnection = _FakePC
    sdp_mod = types.ModuleType("aiortc.sdp")
    sdp_mod.candidate_from_sdp = lambda s: types.SimpleNamespace(_raw=s, sdpMid=None, sdpMLineIndex=None)
    mod.sdp = sdp_mod
    return mod, sdp_mod


@pytest.fixture(autouse=True)
def fake_aiortc(monkeypatch):
    mod, sdp_mod = _make_fake_aiortc()
    monkeypatch.setitem(sys.modules, "aiortc", mod)
    monkeypatch.setitem(sys.modules, "aiortc.sdp", sdp_mod)
    _FakePC.track_to_emit = _FakeTrack()
    yield


# ── fake signaling WebSocket ────────────────────────────────────────────────────
def _envelope(kind, payload):
    return types.SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT,
        data=json.dumps(
            {"messageType": kind, "messagePayload": base64.b64encode(json.dumps(payload).encode()).decode()}
        ),
    )


class _FakeWS:
    def __init__(self, messages):
        self._messages = messages
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_str(self, data):
        self.sent.append(data)

    def __aiter__(self):
        self._it = iter(self._messages)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            # Keep the socket "open" so the grab waits on the frame, not EOF.
            await asyncio.sleep(0.01)
            raise StopAsyncIteration


def _ws_session(messages):
    session = MagicMock()
    session.ws_connect = MagicMock(return_value=_FakeWS(messages))
    return session


_API = MagicMock()
_API.presign_signaling_url = MagicMock(return_value="wss://signed.example/?X-Amz-Signature=abc")

_SESSION = {
    "channelARN": "arn:test:chan",
    "credentials": {"accessKeyId": "AK", "secretAccessKey": "SK", "sessionToken": "ST"},
    "region": "eu-west-1",
    "signalingEndpoints": {"WSS": "wss://v-1.kinesisvideo.eu-west-1.amazonaws.com"},
    "iceServers": [{"Uris": ["turn:1.2.3.4:443"], "Username": "u", "Password": "p"}],
}

_ANSWER = _envelope("SDP_ANSWER", {"sdp": "v=0 answer", "type": "answer"})


class TestBuildIceServers:
    def test_builds_and_skips_non_dict(self):
        session = {"iceServers": [{"Uris": ["turn:a"], "Username": "u", "Password": "p"}, "junk", None]}
        servers = camera._build_ice_servers(session)
        assert len(servers) == 1
        assert servers[0].urls == ["turn:a"] and servers[0].username == "u"

    def test_lowercase_uris_fallback_and_empty(self):
        servers = camera._build_ice_servers({"iceServers": [{"uris": ["stun:b"]}]})
        assert servers[0].urls == ["stun:b"]
        assert camera._build_ice_servers({}) == []


class TestGrabCameraFrame:
    async def test_returns_jpeg_on_answer_and_frame(self):
        ws = _ws_session([_ANSWER])
        out = await camera.async_grab_camera_frame(_API, _SESSION, ws, timeout=2)
        assert out == b"JPEGBYTES"
        assert json.loads(ws.ws_connect.return_value.sent[0])["action"] == "SDP_OFFER"

    async def test_waits_for_ice_gathering_to_complete(self):
        # PC reports gathering in progress once, then complete — exercises the wait loop.
        class _GatheringPC(_FakePC):
            def __init__(self, config=None):
                super().__init__(config)
                self._checks = 0

            @property
            def iceGatheringState(self):
                self._checks += 1
                return "gathering" if self._checks == 1 else "complete"

            @iceGatheringState.setter
            def iceGatheringState(self, value):
                pass

        sys.modules["aiortc"].RTCPeerConnection = _GatheringPC
        out = await camera.async_grab_camera_frame(_API, _SESSION, _ws_session([_ANSWER]), timeout=2)
        assert out == b"JPEGBYTES"

    @pytest.mark.parametrize(
        "session",
        [
            {},
            {"channelARN": "arn", "credentials": "notadict", "signalingEndpoints": {"WSS": "wss://x"}},
            {"channelARN": "arn", "credentials": {"a": 1}, "signalingEndpoints": {}},
        ],
    )
    async def test_returns_none_when_session_incomplete(self, session):
        assert await camera.async_grab_camera_frame(_API, session, _ws_session([]), timeout=1) is None

    async def test_timeout_returns_none_when_no_frame(self):
        # No SDP_ANSWER ever arrives → no track → got never set → timeout.
        out = await camera.async_grab_camera_frame(_API, _SESSION, _ws_session([]), timeout=0.2)
        assert out is None

    async def test_relays_ice_candidate_and_ignores_noise(self):
        ice = _envelope(
            "ICE_CANDIDATE", {"candidate": "candidate:1 1 udp 1 1.2.3.4 5 typ host", "sdpMid": "0", "sdpMLineIndex": 0}
        )
        ice_empty = _envelope("ICE_CANDIDATE", {"candidate": None})
        noise_bin = types.SimpleNamespace(type=aiohttp.WSMsgType.BINARY, data=b"\x00")
        empty = types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="   ")
        bad_json = types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data="{not json")
        no_payload = types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps({"messageType": "STATUS"}))
        ws = _ws_session([noise_bin, empty, bad_json, no_payload, ice, ice_empty, _ANSWER])
        out = await camera.async_grab_camera_frame(_API, _SESSION, ws, timeout=2)
        assert out == b"JPEGBYTES"

    async def test_non_video_track_is_ignored(self):
        _FakePC.track_to_emit = _FakeTrack(kind="audio")
        out = await camera.async_grab_camera_frame(_API, _SESSION, _ws_session([_ANSWER]), timeout=0.3)
        assert out is None

    async def test_no_track_emitted_times_out(self):
        _FakePC.track_to_emit = None
        out = await camera.async_grab_camera_frame(_API, _SESSION, _ws_session([_ANSWER]), timeout=0.3)
        assert out is None

    async def test_frame_grab_error_yields_none(self):
        _FakePC.track_to_emit = _FakeTrack(raise_after=0)
        out = await camera.async_grab_camera_frame(_API, _SESSION, _ws_session([_ANSWER]), timeout=2)
        assert out is None


class _Coord:
    def __init__(self, session, devices=None, raise_session=False):
        self._session = session
        self._raise = raise_session
        self.client = _API
        self.hass = MagicMock()
        self.devices = devices or []

    async def async_start_video_session(self, thing):
        if self._raise:
            raise RuntimeError("cloud down")
        return self._session


class TestLymowCamera:
    def _entity(self, coord, device=None):
        device = device or {"deviceThingName": "device_x", "deviceName": "Mower"}
        ent = camera.LymowCamera(coord, device)
        return ent

    async def test_image_happy_path(self, monkeypatch):
        coord = _Coord(_SESSION)
        ent = self._entity(coord)
        monkeypatch.setattr(camera, "async_get_clientsession", lambda hass: _ws_session([_ANSWER]))
        assert ent._attr_unique_id == "device_x_camera"
        assert ent._attr_name == "Mower Camera"
        assert await ent.async_camera_image() == b"JPEGBYTES"

    async def test_image_none_when_session_raises(self):
        ent = self._entity(_Coord(None, raise_session=True))
        assert await ent.async_camera_image() is None

    async def test_image_none_when_session_not_dict(self):
        ent = self._entity(_Coord("not-a-dict"))
        assert await ent.async_camera_image() is None

    def test_name_falls_back_to_sn_then_thing(self):
        ent = camera.LymowCamera(_Coord(None), {"deviceThingName": "t", "sn": "SN123"})
        assert ent._attr_name == "SN123 Camera"
        ent2 = camera.LymowCamera(_Coord(None), {"deviceThingName": "t"})
        assert ent2._attr_name == "t Camera"


class TestSetupEntry:
    async def test_adds_camera_per_device(self):
        coord = _Coord(_SESSION, devices=[{"deviceThingName": "a"}, {"deviceThingName": "b"}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"entry1": coord}}
        entry = MagicMock(entry_id="entry1")
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert len(added) == 2

    async def test_no_devices_adds_nothing(self):
        coord = _Coord(_SESSION, devices=[])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"entry1": coord}}
        add = MagicMock()
        await camera.async_setup_entry(hass, MagicMock(entry_id="entry1"), add)
        add.assert_not_called()
