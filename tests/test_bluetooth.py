"""Tests for the BLE manual-drive controller (transport-only, no HA stack)."""

from __future__ import annotations

import asyncio
import base64

import pytest
from lymow.bluetooth import LymowBleController, _bleak_client, _clamp
from lymow.const import (
    BLE_DRIVE_ANGULAR_MAX,
    BLE_DRIVE_CHARACTERISTIC_UUID,
    BLE_DRIVE_LINEAR_MAX,
    BLE_DRIVE_MAX_DURATION_S,
    BLE_DRIVE_REFRESH_HZ,
)
from lymow.protocol import _decode_fields


class FakeClient:
    """Minimal BleakClient stand-in capturing the controller's calls."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.connected = False
        self.connects = 0
        self.disconnects = 0
        self.notify: tuple | None = None
        self.writes: list[tuple] = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connected = True
        self.connects += 1

    async def start_notify(self, char, callback) -> None:
        self.notify = (char, callback)

    async def write_gatt_char(self, char, data, response) -> None:
        self.writes.append((char, data, response))

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnects += 1


def _factory():
    """Return (factory, created-list) so tests can inspect the client."""
    created: list[FakeClient] = []

    def make(address: str) -> FakeClient:
        c = FakeClient(address)
        created.append(c)
        return c

    return make, created


def _drive_floats(payload: bytes) -> tuple[float, float]:
    """Decode an encode_ble_drive payload back to (linear, angular).

    Inner message (field 10) is ``0d <f32 linear> 15 <f32 angular>``.
    """
    import struct

    pb = base64.b64decode(payload)
    inner = dict((fn, val) for fn, _wt, val in _decode_fields(pb))[10]
    lin = struct.unpack("<f", inner[1:5])[0]
    ang = struct.unpack("<f", inner[6:10])[0]
    return lin, ang


def test_clamp_bounds():
    assert _clamp(9.0, 0.5) == 0.5
    assert _clamp(-9.0, 0.5) == -0.5
    assert _clamp(0.2, 0.5) == 0.2


async def test_drive_connects_enables_notify_and_writes():
    make, created = _factory()
    ctrl = LymowBleController("AA:BB:CC:DD:EE:FF", client_factory=make)
    assert ctrl.address == "AA:BB:CC:DD:EE:FF"
    assert not ctrl.is_connected

    await ctrl.async_drive(0.3, -0.4)

    client = created[0]
    assert client.connects == 1
    assert client.notify[0] == BLE_DRIVE_CHARACTERISTIC_UUID
    assert ctrl.is_connected
    char, data, response = client.writes[0]
    assert char == BLE_DRIVE_CHARACTERISTIC_UUID
    assert response is False
    lin, ang = _drive_floats(data)
    assert lin == pytest.approx(0.3, abs=1e-6)
    assert ang == pytest.approx(-0.4, abs=1e-6)


async def test_drive_clamps_to_safe_range():
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive(5.0, -5.0)
    lin, ang = _drive_floats(created[0].writes[0][1])
    assert lin == pytest.approx(BLE_DRIVE_LINEAR_MAX, abs=1e-6)
    assert ang == pytest.approx(-BLE_DRIVE_ANGULAR_MAX, abs=1e-6)


async def test_second_drive_reuses_connection():
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive(0.1, 0.0)
    await ctrl.async_drive(0.2, 0.0)
    assert len(created) == 1
    assert created[0].connects == 1
    assert len(created[0].writes) == 2


async def test_stop_sends_zero_frame():
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_stop()
    lin, ang = _drive_floats(created[0].writes[0][1])
    assert (lin, ang) == (pytest.approx(0.0), pytest.approx(0.0))


async def test_drive_for_loops_then_stops(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive_for(0.3, 0.0, 0.3)  # 0.3s @ 10Hz -> 3 frames + stop

    writes = created[0].writes
    assert len(writes) == 4  # 3 drive frames + 1 stop
    assert len(sleeps) == 3
    assert sleeps[0] == pytest.approx(1.0 / BLE_DRIVE_REFRESH_HZ)
    lin, ang = _drive_floats(writes[-1][1])
    assert (lin, ang) == (pytest.approx(0.0), pytest.approx(0.0))


async def test_drive_for_caps_duration(monkeypatch):
    async def fake_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive_for(0.5, 0.0, 999.0)  # clamped to BLE_DRIVE_MAX_DURATION_S
    expected_frames = round(BLE_DRIVE_MAX_DURATION_S * BLE_DRIVE_REFRESH_HZ)
    assert len(created[0].writes) == expected_frames + 1  # + stop


async def test_drive_for_stops_even_on_write_error(monkeypatch):
    async def fake_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class BoomClient(FakeClient):
        async def write_gatt_char(self, char, data, response):
            await super().write_gatt_char(char, data, response)
            if len(self.writes) == 1:
                raise RuntimeError("ble write failed")

    created: list[BoomClient] = []

    def make(addr):
        c = BoomClient(addr)
        created.append(c)
        return c

    ctrl = LymowBleController("x", client_factory=make)
    with pytest.raises(RuntimeError):
        await ctrl.async_drive_for(0.3, 0.0, 0.5)
    # finally-block stop still ran (a second write attempt)
    assert len(created[0].writes) == 2
    lin, ang = _drive_floats(created[0].writes[-1][1])
    assert (lin, ang) == (pytest.approx(0.0), pytest.approx(0.0))


async def test_notification_stored():
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive(0.0, 0.0)
    assert ctrl.last_status is None
    _char, cb = created[0].notify
    cb(None, bytearray(b"\x01\x02\x03"))
    assert ctrl.last_status == b"\x01\x02\x03"


async def test_disconnect_clears_client():
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive(0.1, 0.0)
    assert ctrl.is_connected
    await ctrl.async_disconnect()
    assert created[0].disconnects == 1
    assert not ctrl.is_connected


async def test_disconnect_noop_when_never_connected():
    ctrl = LymowBleController("x", client_factory=_factory()[0])
    await ctrl.async_disconnect()  # must not raise
    assert not ctrl.is_connected


def test_default_factory_builds_bleak_client():
    client = _bleak_client("AA:BB:CC:DD:EE:FF")
    assert client is not None


async def test_drive_for_zero_duration_sends_only_stop(monkeypatch):
    async def fake_sleep(delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive_for(0.5, 0.0, 0.0)  # non-positive duration -> no drive frames
    assert len(created[0].writes) == 1  # only the stop frame
    lin, ang = _drive_floats(created[0].writes[0][1])
    assert (lin, ang) == (pytest.approx(0.0), pytest.approx(0.0))


async def test_reconnects_after_drop(monkeypatch):
    make, created = _factory()
    ctrl = LymowBleController("x", client_factory=make)
    await ctrl.async_drive(0.1, 0.0)
    created[0].connected = False  # simulate a dropped link
    await ctrl.async_drive(0.2, 0.0)
    assert len(created) == 2  # a fresh client was built
