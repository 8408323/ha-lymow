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
        assert ent._attr_has_entity_name is True
        assert ent._attr_name == "Camera"
        assert ent._attr_device_info["name"] == "Mower"
        assert ent._attr_supported_features is camera.CameraEntityFeature.STREAM

    def test_device_name_falls_back_to_sn_then_thing(self):
        assert _entity({}, {"deviceThingName": THING, "sn": "SN1"})._attr_device_info["name"] == "SN1"
        assert _entity({}, {"deviceThingName": THING})._attr_device_info["name"] == THING

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

    async def test_setup_applies_rtsp_options_to_stream_url(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}}, devices=[{"deviceThingName": THING}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        entry = MagicMock(entry_id="e1")
        entry.options = {"rtsp_path": "h264ESVideoMain", "rtsp_port": 8554}
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert await added[0].stream_source() == "rtsp://192.168.1.85:8554/h264ESVideoMain"

    async def test_setup_defaults_rtsp_when_options_absent(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}}, devices=[{"deviceThingName": THING}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        entry = MagicMock(entry_id="e1")
        entry.options = {}
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert await added[0].stream_source() == RTSP

    async def test_setup_strips_leading_slash_from_rtsp_path(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}}, devices=[{"deviceThingName": THING}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        entry = MagicMock(entry_id="e1")
        entry.options = {"rtsp_path": "/h264ESVideoMain", "rtsp_port": 8554}
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert await added[0].stream_source() == "rtsp://192.168.1.85:8554/h264ESVideoMain"

    async def test_setup_strips_multiple_leading_slashes_from_rtsp_path(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}}, devices=[{"deviceThingName": THING}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        entry = MagicMock(entry_id="e1")
        entry.options = {"rtsp_path": "///h264ESVideoMain", "rtsp_port": 8554}
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert await added[0].stream_source() == "rtsp://192.168.1.85:8554/h264ESVideoMain"

    async def test_setup_uses_default_path_when_rtsp_path_is_whitespace(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}}, devices=[{"deviceThingName": THING}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        entry = MagicMock(entry_id="e1")
        entry.options = {"rtsp_path": "   ", "rtsp_port": 8554}
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert await added[0].stream_source() == "rtsp://192.168.1.85:8554/h264ESVideoTest"

    async def test_setup_uses_default_path_when_rtsp_path_has_only_slashes(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}}, devices=[{"deviceThingName": THING}])
        hass = MagicMock()
        hass.data = {camera.DOMAIN: {"e1": coord}}
        entry = MagicMock(entry_id="e1")
        entry.options = {"rtsp_path": "///", "rtsp_port": 8554}
        added = []
        await camera.async_setup_entry(hass, entry, lambda e: added.extend(e))
        assert await added[0].stream_source() == "rtsp://192.168.1.85:8554/h264ESVideoTest"


class TestFreePort:
    def test_returns_bound_port(self):
        port = camera._free_port()
        assert isinstance(port, int)
        assert 1 <= port <= 65535


class TestExtraStateAttributes:
    def test_includes_rtsp_url_when_ip_present(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        attrs = ent.extra_state_attributes
        assert attrs["frontend_stream_type"] == "hls"
        assert attrs["rtsp_url"] == RTSP
        assert "mpegts_proxy_url" not in attrs

    def test_omits_rtsp_url_without_ip(self):
        ent = _entity({"battery": 50})
        attrs = ent.extra_state_attributes
        assert attrs == {"frontend_stream_type": "hls"}

    def test_includes_proxy_url_when_proxy_running(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        ent._ts_port = 54321
        ent._proxy_proc = MagicMock()
        attrs = ent.extra_state_attributes
        assert attrs["mpegts_proxy_url"] == "http://127.0.0.1:54321/stream.ts"


class TestStreamSourceProxy:
    async def test_returns_proxy_url_when_proxy_active(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        ent._ts_port = 12345
        ent._proxy_proc = MagicMock()
        assert await ent.stream_source() == "http://127.0.0.1:12345/stream.ts"


class TestLifecycle:
    async def test_added_to_hass_starts_proxy_when_stream_url(self, monkeypatch):
        ent = _entity({"ipAddress": "192.168.1.85"})

        async def _super():
            return None

        monkeypatch.setattr(type(ent).__mro__[1], "async_added_to_hass", lambda self: _super(), raising=False)
        started = {}

        async def _start():
            started["yes"] = True

        ent._start_proxy = _start
        ent.async_on_remove = MagicMock()
        ent.coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        await ent.async_added_to_hass()
        assert started.get("yes") is True
        ent.async_on_remove.assert_called_once()

    async def test_added_to_hass_skips_proxy_without_stream_url(self, monkeypatch):
        ent = _entity({"battery": 1})

        async def _super():
            return None

        monkeypatch.setattr(type(ent).__mro__[1], "async_added_to_hass", lambda self: _super(), raising=False)

        async def _start():
            raise AssertionError("should not start")

        ent._start_proxy = _start
        ent.async_on_remove = MagicMock()
        ent.coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        await ent.async_added_to_hass()
        ent.async_on_remove.assert_called_once()

    async def test_will_remove_stops_proxy(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        stopped = {}

        async def _stop():
            stopped["yes"] = True

        ent._stop_proxy = _stop
        await ent.async_will_remove_from_hass()
        assert stopped.get("yes") is True


class TestOnCoordinatorUpdate:
    def test_no_ip_does_nothing(self):
        coord = _Coord({THING: {"battery": 1}})
        ent = camera.LymowCamera(coord, {"deviceThingName": THING})
        ent.hass = MagicMock()
        ent._on_coordinator_update()
        ent.hass.async_create_task.assert_not_called()

    def test_starts_restart_when_no_proc(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}})
        ent = camera.LymowCamera(coord, {"deviceThingName": THING})
        ent.hass = MagicMock()
        captured = {}
        ent.hass.async_create_task = lambda coro: captured.setdefault("coro", coro)
        ent._proxy_proc = None
        ent._on_coordinator_update()
        assert "coro" in captured
        captured["coro"].close()

    def test_starts_restart_when_ip_changed(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.99"}})
        ent = camera.LymowCamera(coord, {"deviceThingName": THING})
        ent.hass = MagicMock()
        captured = {}
        ent.hass.async_create_task = lambda coro: captured.setdefault("coro", coro)
        ent._proxy_proc = MagicMock()
        ent._proxy_ip = "192.168.1.85"
        ent._on_coordinator_update()
        assert "coro" in captured
        captured["coro"].close()

    def test_no_restart_when_ip_unchanged(self):
        coord = _Coord({THING: {"ipAddress": "192.168.1.85"}})
        ent = camera.LymowCamera(coord, {"deviceThingName": THING})
        ent.hass = MagicMock()
        ent._proxy_proc = MagicMock()
        ent._proxy_ip = "192.168.1.85"
        ent._on_coordinator_update()
        ent.hass.async_create_task.assert_not_called()


class TestStartProxy:
    async def test_no_op_without_rtsp_url(self):
        ent = _entity({"battery": 1})
        await ent._start_proxy()
        assert ent._ts_server is None
        assert ent._proxy_proc is None

    async def test_starts_server_and_ffmpeg(self, monkeypatch):
        ent = _entity({"ipAddress": "192.168.1.85"})
        ent.hass = MagicMock()
        created_task = {}
        ent.hass.async_create_task = lambda coro: created_task.setdefault("coro", coro) or "task"

        monkeypatch.setattr(camera, "_free_port", lambda: 45678)

        fake_server = MagicMock()

        async def _start_server(handler, host, port):
            assert host == "127.0.0.1"
            assert port == 45678
            return fake_server

        monkeypatch.setattr(camera.asyncio, "start_server", _start_server)

        fake_proc = MagicMock()
        fake_proc.pid = 4242

        async def _create_subprocess(*args, **kwargs):
            return fake_proc

        monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _create_subprocess)
        monkeypatch.setattr(camera, "get_ffmpeg_manager", lambda hass: MagicMock(binary="/usr/bin/ffmpeg"))

        await ent._start_proxy()
        assert ent._ts_server is fake_server
        assert ent._ts_port == 45678
        assert ent._proxy_proc is fake_proc
        assert ent._proxy_ip == "192.168.1.85"
        # cleanup the un-awaited coroutine
        created_task["coro"].close()

    async def test_ffmpeg_manager_failure_falls_back_to_default_binary(self, monkeypatch):
        ent = _entity({"ipAddress": "192.168.1.85"})
        ent.hass = MagicMock()
        captured = {}
        ent.hass.async_create_task = lambda coro: captured.setdefault("coro", coro) or "task"

        monkeypatch.setattr(camera, "_free_port", lambda: 45679)

        async def _start_server(handler, host, port):
            return MagicMock()

        monkeypatch.setattr(camera.asyncio, "start_server", _start_server)

        async def _create_subprocess(binary, *args, **kwargs):
            captured["binary"] = binary
            return MagicMock(pid=1)

        monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _create_subprocess)

        def _boom(hass):
            raise RuntimeError("no manager")

        monkeypatch.setattr(camera, "get_ffmpeg_manager", _boom)

        await ent._start_proxy()
        assert captured["binary"] == "ffmpeg"
        captured["coro"].close()

    async def test_ffmpeg_spawn_failure_resets_state(self, monkeypatch):
        ent = _entity({"ipAddress": "192.168.1.85"})
        ent.hass = MagicMock()

        monkeypatch.setattr(camera, "_free_port", lambda: 45680)

        fake_server = MagicMock()

        async def _start_server(handler, host, port):
            return fake_server

        monkeypatch.setattr(camera.asyncio, "start_server", _start_server)

        async def _create_subprocess(*args, **kwargs):
            raise OSError("ffmpeg missing")

        monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _create_subprocess)
        monkeypatch.setattr(camera, "get_ffmpeg_manager", lambda hass: MagicMock(binary="ffmpeg"))

        await ent._start_proxy()
        assert ent._proxy_proc is None
        assert ent._ts_server is None
        assert ent._ts_port is None
        assert ent._proxy_ip is None
        fake_server.close.assert_called_once()


class TestStopProxy:
    async def test_stops_all_resources(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        import asyncio as _aio

        async def _runner():
            await _aio.sleep(3600)

        real_task = _aio.ensure_future(_runner())
        ent._ts_reader_task = real_task

        proc = MagicMock()

        async def _wait():
            return 0

        proc.wait = _wait
        proc.terminate = MagicMock()
        ent._proxy_proc = proc

        server = MagicMock()

        async def _wait_closed():
            return None

        server.wait_closed = _wait_closed
        ent._ts_server = server

        q = _aio.Queue(maxsize=1)
        ent._ts_clients = [q]
        ent._ts_port = 9999
        ent._proxy_ip = "192.168.1.85"

        await ent._stop_proxy()

        assert real_task.cancelled()
        proc.terminate.assert_called_once()
        server.close.assert_called_once()
        assert ent._proxy_proc is None
        assert ent._ts_server is None
        assert ent._ts_reader_task is None
        assert ent._ts_clients == []
        assert ent._ts_port is None
        assert ent._proxy_ip is None
        assert q.get_nowait() is None

    async def test_terminate_process_lookup_error_suppressed(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=ProcessLookupError())
        ent._proxy_proc = proc
        await ent._stop_proxy()
        assert ent._proxy_proc is None

    async def test_noop_when_nothing_running(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        await ent._stop_proxy()
        assert ent._proxy_proc is None
        assert ent._ts_server is None


class TestHandleTsClient:
    async def test_rejects_non_get_request(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        reader = MagicMock()

        async def _readline():
            return b"POST / HTTP/1.1\r\n"

        reader.readline = _readline
        writer = MagicMock()
        await ent._handle_ts_client(reader, writer)
        writer.write.assert_not_called()
        assert ent._ts_clients == []

    async def test_streams_chunks_until_none_sentinel(self):
        import asyncio as _aio

        ent = _entity({"ipAddress": "192.168.1.85"})
        lines = [b"GET /stream.ts HTTP/1.1\r\n", b"Host: x\r\n", b"\r\n"]
        idx = {"i": 0}

        async def _readline():
            i = idx["i"]
            idx["i"] += 1
            return lines[i]

        reader = MagicMock()
        reader.readline = _readline

        written = []
        writer = MagicMock()
        writer.write = lambda b: written.append(b)

        async def _drain():
            return None

        writer.drain = _drain

        async def _client():
            await ent._handle_ts_client(reader, writer)

        task = _aio.ensure_future(_client())
        # Wait for the client to register its queue.
        for _ in range(100):
            await _aio.sleep(0)
            if ent._ts_clients:
                break
        assert len(ent._ts_clients) == 1
        ent._ts_clients[0].put_nowait(b"TSCHUNK")
        ent._ts_clients[0].put_nowait(None)
        await task

        assert any(b == b"TSCHUNK" for b in written)
        assert written[0].startswith(b"HTTP/1.1 200 OK")
        assert ent._ts_clients == []
        writer.close.assert_called()

    async def test_header_read_exception_closes_writer(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        reader = MagicMock()

        async def _readline():
            raise ConnectionResetError("boom")

        reader.readline = _readline
        writer = MagicMock()
        await ent._handle_ts_client(reader, writer)
        writer.close.assert_called_once()
        assert ent._ts_clients == []


class TestReadFfmpegStdout:
    async def test_broadcasts_chunks_then_sentinels_on_eof(self):
        import asyncio as _aio

        ent = _entity({"ipAddress": "192.168.1.85"})
        chunks = [b"AAA", b"BBB", b""]
        idx = {"i": 0}

        async def _read(n):
            i = idx["i"]
            idx["i"] += 1
            return chunks[i]

        proc = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.read = _read
        ent._proxy_proc = proc

        q = _aio.Queue(maxsize=10)
        ent._ts_clients = [q]

        await ent._read_ffmpeg_stdout()

        got = []
        while not q.empty():
            got.append(q.get_nowait())
        assert got == [b"AAA", b"BBB", None]

    async def test_drops_slow_client_when_queue_full(self):
        import asyncio as _aio

        ent = _entity({"ipAddress": "192.168.1.85"})
        chunks = [b"X", b""]
        idx = {"i": 0}

        async def _read(n):
            i = idx["i"]
            idx["i"] += 1
            return chunks[i]

        proc = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.read = _read
        ent._proxy_proc = proc

        full_q = _aio.Queue(maxsize=1)
        full_q.put_nowait(b"already-full")
        ent._ts_clients = [full_q]

        await ent._read_ffmpeg_stdout()
        # Slow client was removed before the EOF sentinel loop.
        assert ent._ts_clients == []


class TestRestartProxy:
    async def test_stops_then_starts(self):
        ent = _entity({"ipAddress": "192.168.1.85"})
        calls = []

        async def _stop():
            calls.append("stop")

        async def _start():
            calls.append("start")

        ent._stop_proxy = _stop
        ent._start_proxy = _start
        await ent._restart_proxy()
        assert calls == ["stop", "start"]


class TestHandleTsClientStreamError:
    async def test_drain_error_during_stream_removes_client(self):
        import asyncio as _aio

        ent = _entity({"ipAddress": "192.168.1.85"})
        lines = [b"GET /stream.ts HTTP/1.1\r\n", b"\r\n"]
        idx = {"i": 0}

        async def _readline():
            i = idx["i"]
            idx["i"] += 1
            return lines[i]

        reader = MagicMock()
        reader.readline = _readline

        drain_calls = {"n": 0}

        async def _drain():
            drain_calls["n"] += 1
            if drain_calls["n"] >= 2:
                raise ConnectionResetError("client gone")

        writer = MagicMock()
        writer.drain = _drain

        async def _client():
            await ent._handle_ts_client(reader, writer)

        task = _aio.ensure_future(_client())
        for _ in range(100):
            await _aio.sleep(0)
            if ent._ts_clients:
                break
        ent._ts_clients[0].put_nowait(b"TSCHUNK")
        await task

        assert ent._ts_clients == []
        writer.close.assert_called()
