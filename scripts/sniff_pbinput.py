"""Sniff pbinput messages from the Lymow app to discover userCtrl values.

Usage:
    uv run python scripts/sniff_pbinput.py

Open the Lymow app and perform operations (start mow, edit zones, dock, etc.)
while this script is running.  Each pbinput message is decoded and the userCtrl
field (field 5) is printed so you can identify the correct command numbers.

Press Ctrl-C to stop.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import struct
import sys

import aiohttp

# ---------------------------------------------------------------------------
# .env loader (identical to cli.py / delete_zone.py)
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

# ---------------------------------------------------------------------------
# Minimal protobuf decoder — enough to extract field 5 (userCtrl)
# ---------------------------------------------------------------------------


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    result, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7
    raise ValueError("truncated varint")


def decode_fields(data: bytes) -> dict[int, list]:
    """Return {field_no: [values...]} for every field in a flat protobuf message."""
    out: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        field_no = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:  # varint
            val, pos = _decode_varint(data, pos)
        elif wire_type == 1:  # 64-bit
            val = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            val = data[pos : pos + length]
            pos += length
        elif wire_type == 5:  # 32-bit
            val = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            break  # unknown wire type — stop
        out.setdefault(field_no, []).append(val)
    return out


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
        client_id = f"lymow-sniff-{uuid.uuid4().hex[:8]}"

        print(f"\nConnecting to MQTT ({iot_host})…")
        print("Subscribing to pbINPUT (what the app sends to the robot).")
        print("Use the Lymow app now — start mow, edit zones, dock, etc.")
        print("Press Ctrl-C to stop.\n")
        print(f"{'Len':>6}  {'f2(PbVer)':>10}  {'f5(userCtrl)':>13}  Hex dump")
        print("-" * 70)

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
            pbin_topic = f"/device/{thing}/pbinput"
            await mqtt.subscribe(pbin_topic, qos=1)

            async for message in mqtt.messages:
                try:
                    raw = unwrap_envelope(message.payload)
                    fields = decode_fields(raw)
                    pb_ver = fields.get(2, [None])[0]
                    user_ctrl = fields.get(5, [None])[0]
                    hex_dump = raw[:24].hex()
                    print(f"{len(raw):>6}  {str(pb_ver):>10}  {str(user_ctrl):>13}  {hex_dump}")
                    # If there's a length-delimited field 23 (map data), flag it
                    if 23 in fields:
                        print(f"         ↳ field 23 present (map data) — len={len(fields[23][0])} bytes")
                except Exception as exc:
                    print(f"  [decode error: {exc}]  raw={message.payload[:40]!r}")


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
