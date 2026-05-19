"""Send a choreographed BLE drive sequence to the Lymow robot.

Sequence (waits 3 s before moving so you can abort with Ctrl-C):
  1. Backward  — DRIVE_VEL for DRIVE_SECS
  2. Forward   — DRIVE_VEL for DRIVE_SECS (same distance, back to start)
  3. Spin right — TURN_VEL for TURN_SECS
  4. Spin left  — TURN_VEL for TURN_SECS (same amount back)

Requires the bleak BLE library (not in default deps):
    uv add --group dev bleak
    -- or ad-hoc --
    pip install bleak

Robot MAC address: set LYMOW_BLE_MAC in scripts/.env (or pass as CLI arg):
    uv run python scripts/ble_drive_test.py [MAC]

Example:
    LYMOW_BLE_MAC=AA:BB:11:CC:22:DD uv run python scripts/ble_drive_test.py

IMPORTANT — close the Lymow app before running:
    The robot accepts only one BLE connection at a time. If the phone app is open
    and connected, the laptop's connect() call will time out. Force-close the app
    (or disable Bluetooth on the phone) before running this script.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time

# ---------------------------------------------------------------------------
# Motion parameters — tune these before running
# ---------------------------------------------------------------------------
DRIVE_VEL: float = 0.3  # linear velocity  (max ±0.5; positive = forward)
TURN_VEL: float = 0.4  # angular velocity (max ±0.6; positive = right)
DRIVE_SECS: float = 1.5  # seconds to drive backward / forward
TURN_SECS: float = 2.0  # seconds to spin right / left
HZ: int = 10  # command send rate — matches the app's observed rate

# BLE characteristic UUID confirmed from HCI BTSnoop capture (2025-05)
DRIVE_UUID = "12345678-1234-5678-1234-56789abcdef1"
# CCCD handle for enabling notifications (mirrors what the app does on connect)
CCCD_HANDLE = 0x0015


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Load key=value pairs from scripts/.env into os.environ (no overrides)."""
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        break


def _load_protocol() -> object:
    """Load lymow.protocol from the custom_components source tree."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    const_path = os.path.join(repo_root, "custom_components", "lymow", "const.py")
    proto_path = os.path.join(repo_root, "custom_components", "lymow", "protocol.py")
    for name, path in (("lymow.const", const_path), ("lymow.protocol", proto_path)):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["lymow.protocol"]


# ---------------------------------------------------------------------------
# Motion helpers
# ---------------------------------------------------------------------------


async def _drive(client: object, encode_fn, linear: float, angular: float, secs: float) -> None:
    """Send repeated drive commands at HZ for the given duration."""
    payload = encode_fn(linear, angular)
    interval = 1.0 / HZ
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        await client.write_gatt_char(DRIVE_UUID, payload, response=False)
        await asyncio.sleep(interval)


async def _stop(client: object, encode_fn) -> None:
    """Send a single zero-velocity frame to halt the robot."""
    await client.write_gatt_char(DRIVE_UUID, encode_fn(0.0, 0.0), response=False)


# ---------------------------------------------------------------------------
# Main sequence
# ---------------------------------------------------------------------------


async def run(mac: str) -> None:
    try:
        from bleak import BleakClient
    except ImportError:
        print("ERROR: bleak is not installed.  Run:  uv add --group dev bleak", file=sys.stderr)
        sys.exit(1)

    proto = _load_protocol()
    encode = proto.encode_ble_drive

    # 3-second countdown so you can abort before anything moves
    print(f"Connecting to {mac} …")
    print("Press Ctrl-C NOW to abort.")
    for remaining in range(3, 0, -1):
        print(f"  Starting in {remaining} …")
        await asyncio.sleep(1)

    try:
        async with BleakClient(mac) as client:
            print(f"Connected: {client.is_connected}")

            # Mirror the app: enable notifications on the drive characteristic
            try:
                await client.write_gatt_descriptor(CCCD_HANDLE, b"\x01\x00")
            except Exception:
                pass  # notifications not strictly required for drive commands

            steps = [
                ("Backward", -DRIVE_VEL, 0.0, DRIVE_SECS),
                ("Forward", +DRIVE_VEL, 0.0, DRIVE_SECS),
                ("Spin right", 0.0, +TURN_VEL, TURN_SECS),
                ("Spin left", 0.0, -TURN_VEL, TURN_SECS),
            ]

            for label, lin, ang, secs in steps:
                print(f"  {label:<12} linear={lin:+.1f}  angular={ang:+.1f}  for {secs:.1f}s …")
                await _drive(client, encode, lin, ang, secs)
                await _stop(client, encode)
                await asyncio.sleep(0.3)  # brief pause between moves

            print("Done — robot stopped.")
    except Exception as exc:
        exc_str = str(exc)
        if "org.bluez" in exc_str or "ServiceUnknown" in exc_str or "DBus" in exc_str:
            print(
                "ERROR: No Bluetooth chip available on this machine.\n"
                "  The BlueZ service (org.bluez) is not running — this happens on\n"
                "  WSL2 and machines without a Bluetooth adapter.\n"
                "  Run the script from a machine that has Bluetooth hardware.",
                file=sys.stderr,
            )
        else:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    _load_dotenv()
    mac = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LYMOW_BLE_MAC", "")
    if not mac:
        print(
            "ERROR: robot MAC address required.\n"
            "  Set LYMOW_BLE_MAC in scripts/.env or pass as argument:\n"
            "  uv run python scripts/ble_drive_test.py AA:BB:11:CC:22:DD",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        asyncio.run(run(mac.upper()))
    except KeyboardInterrupt:
        print("\nAborted.")


if __name__ == "__main__":
    main()
