"""Local BLE manual-drive transport for the Lymow robot.

The robot exposes a proprietary GATT service whose drive characteristic
(``BLE_DRIVE_CHARACTERISTIC_UUID``, ATT handle 0x0014) accepts protobuf
joystick frames written without response. The app streams frames at ~10 Hz
while the joystick is held; a zero frame stops the robot.

This module is transport-only and intentionally free of Home Assistant
imports so it can be unit-tested without the HA stack. The ``client_factory``
seam lets tests inject a fake BleakClient.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .const import (
    BLE_DRIVE_ANGULAR_MAX,
    BLE_DRIVE_CHARACTERISTIC_UUID,
    BLE_DRIVE_LINEAR_MAX,
    BLE_DRIVE_MAX_DURATION_S,
    BLE_DRIVE_REFRESH_HZ,
)
from .protocol import encode_ble_drive

ClientFactory = Callable[[str], Any]


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _bleak_client(address: str) -> Any:
    """Default factory: a plain BleakClient for the given address."""
    from bleak import BleakClient

    return BleakClient(address)


class LymowBleController:
    """Connect to the robot over BLE and stream manual-drive frames."""

    def __init__(self, address: str, client_factory: ClientFactory | None = None) -> None:
        self._address = address
        self._client_factory = client_factory or _bleak_client
        self._client: Any | None = None
        self._last_status: bytes | None = None
        self._lock = asyncio.Lock()

    @property
    def address(self) -> str:
        return self._address

    @property
    def last_status(self) -> bytes | None:
        """Most recent raw notification from the drive characteristic, if any."""
        return self._last_status

    @property
    def is_connected(self) -> bool:
        return self._client is not None and bool(self._client.is_connected)

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        self._last_status = bytes(data)

    async def _connected_client(self) -> Any:
        if self.is_connected:
            return self._client
        # Drop any stale (disconnected) client before establishing a fresh one.
        self._client = None
        client = self._client_factory(self._address)
        await client.connect()
        # start_notify writes the CCCD; the robot ignores angular commands until
        # notifications are enabled on the drive characteristic.
        await client.start_notify(BLE_DRIVE_CHARACTERISTIC_UUID, self._on_notify)
        self._client = client
        return client

    async def _write_frame(self, client: Any, linear: float, angular: float) -> None:
        """Encode + write one clamped drive frame (caller holds the lock)."""
        payload = encode_ble_drive(
            _clamp(float(linear), BLE_DRIVE_LINEAR_MAX),
            _clamp(float(angular), BLE_DRIVE_ANGULAR_MAX),
        )
        await client.write_gatt_char(BLE_DRIVE_CHARACTERISTIC_UUID, payload, response=False)

    async def async_drive(self, linear: float, angular: float) -> None:
        """Send one drive frame. Velocities are clamped to the safe range."""
        async with self._lock:
            client = await self._connected_client()
            await self._write_frame(client, linear, angular)

    async def async_stop(self) -> None:
        """Send a zero frame to halt the robot."""
        await self.async_drive(0.0, 0.0)

    async def async_drive_for(self, linear: float, angular: float, duration: float) -> None:
        """Stream a drive frame for ``duration`` seconds, then always stop.

        Duration is clamped to ``BLE_DRIVE_MAX_DURATION_S`` so a single call can
        never run the robot away. The lock is held for the whole timed drive so
        other tasks can't interleave frames mid-motion. A non-positive duration
        sends no drive frames — only the final stop.
        """
        duration = max(0.0, min(float(duration), BLE_DRIVE_MAX_DURATION_S))
        interval = 1.0 / BLE_DRIVE_REFRESH_HZ
        loops = round(duration / interval)
        async with self._lock:
            client = await self._connected_client()
            try:
                for _ in range(loops):
                    await self._write_frame(client, linear, angular)
                    await asyncio.sleep(interval)
            finally:
                await self._write_frame(client, 0.0, 0.0)

    async def async_disconnect(self) -> None:
        async with self._lock:
            client = self._client
            self._client = None
            if client is not None:
                await client.disconnect()
