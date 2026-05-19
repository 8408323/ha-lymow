"""Capture ALL MQTT messages under /device/<thing>/# to discover hidden topics.

Usage:
    uv run python scripts/sniff_all_topics.py

Run this script, then:
  1. Cold-start the Lymow app on the phone (force-stop first, then open fresh)
  2. Wait for the home screen to load
  3. Tap the joystick icon to enter manual-drive mode
  4. Drive the robot a bit
  5. Exit joystick mode
  6. Press Ctrl-C here

Every MQTT message on any subtopic is printed with a timestamp, topic,
and full hex dump, so we can spot any BLE-register or BLE-enable command.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time

import aiohttp

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
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
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        break


def _load(module_name: str, path: str) -> None:
    if module_name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import unwrap_envelope  # noqa: E402


def try_unwrap(payload: bytes) -> bytes | None:
    try:
        return unwrap_envelope(payload)
    except Exception:
        return None


def hex_lines(data: bytes, width: int = 32) -> list[str]:
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:04x}: {hex_part:<{width * 3}}  {ascii_part}")
    return lines


async def run() -> None:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print("Error: set LYMOW_USER and LYMOW_PASS in scripts/.env", file=sys.stderr)
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        print("Logging in…")
        tokens = await auth.login(username, password)
        region = tokens["region"]

        print("Getting AWS credentials…")
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds_data["credentials"]

        client = LymowApiClient(
            session=session,
            access_token=tokens["AccessToken"],
            region=region,
            identity_id=creds_data["identity_id"],
        )
        devices = await client.get_devices()
        things = [d["deviceThingName"] for d in devices]
        if not things:
            print("No devices found.", file=sys.stderr)
            sys.exit(1)
        thing = things[0]
        print(f"  thing={thing}")

        cfg = REGION_CONFIG[region]
        iot_host = cfg["iot_host"]

        import uuid

        import aiomqtt

        ws_path = build_presigned_ws_path(
            iot_host,
            region,
            aws["AccessKeyId"],
            aws["SecretKey"],
            aws.get("SessionToken"),
        )
        tls = aiomqtt.TLSParameters()
        client_id = f"lymow-sniffall-{uuid.uuid4().hex[:8]}"

        print(f"\nConnecting to MQTT ({iot_host})…")

        async with aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=client_id,
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=tls,
            keepalive=30,
            timeout=15,
        ) as mqtt:
            # Subscribe to ALL subtopics under the device
            wildcard = f"/device/{thing}/#"
            await mqtt.subscribe(wildcard, qos=1)
            print(f"Subscribed to: {wildcard}")
            print()
            print("NOW: force-stop the Lymow app, then cold-start it on the phone.")
            print("Watch for any BLE/bluetooth-related topic or payload.")
            print("Press Ctrl-C to stop.\n")
            print("=" * 72)

            async for message in mqtt.messages:
                topic = str(message.topic)
                payload = (
                    bytes(message.payload)
                    if isinstance(message.payload, (bytes, bytearray))
                    else message.payload.encode()
                )
                ts = time.strftime("%H:%M:%S")

                topic.split("/")[-1]

                # Try to decode as text (JSON) first
                try:
                    text = payload.decode("utf-8")
                    print(f"\n[{ts}] TOPIC: {topic}  ({len(payload)} bytes)  [UTF-8]")
                    print(f"  {text[:400]}")
                except UnicodeDecodeError:
                    print(f"\n[{ts}] TOPIC: {topic}  ({len(payload)} bytes)  [binary]")
                    for ln in hex_lines(payload[:128]):
                        print(ln)

                # Also try unwrapping as protobuf envelope
                inner = try_unwrap(payload)
                if inner is not None and inner != payload:
                    print(f"  → unwrapped protobuf ({len(inner)} bytes):")
                    for ln in hex_lines(inner[:128]):
                        print(ln)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
