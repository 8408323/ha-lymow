"""One-shot test: send MODIFY_ZONE_INFO via MQTT, verify next pboutput reflects the new name.

Renames go-zone wsmjco1T from "Front garden" → "Front garden RENAMETEST", waits a few seconds for
the robot to echo, then restores the original name. Lets us validate the rename round-trip
on the robot side without needing the app or the HA integration.

Run from the repo root: `uv run python scripts/rename_test.py`.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import sys
import uuid

import aiohttp


def _load_dotenv() -> None:
    for path in (
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ):
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        break


def _load(name: str, path: str) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

import aiomqtt  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import encode_query_map, encode_rename_zone, unwrap_envelope, wrap_envelope  # noqa: E402

HASH = "wsmjco1T"
ORIGINAL = "Front garden"
TEST = "Front garden RENAMETEST"


def _find_name(pb: bytes, hash_id: str) -> str | None:
    needle = hash_id.encode()
    for m in re.finditer(rb"\x12([\x01-\x40])([\x20-\x7e]+)", pb):
        length = m.group(1)[0]
        text = m.group(2)[:length]
        following = pb[m.end() : m.end() + 40]
        if needle in following:
            return text.decode("utf-8", errors="replace")
    return None


async def main() -> None:
    user = os.environ["LYMOW_USER"]
    pwd = os.environ["LYMOW_PASS"]
    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        tokens = await auth.login(user, pwd)
        region = tokens["region"]
        creds = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds["credentials"]
        from lymow.api import LymowApiClient

        api = LymowApiClient(session, tokens["AccessToken"], region, creds["identity_id"])
        devices = await api.get_devices()
        thing = devices[0]["deviceThingName"]
        iot_host = REGION_CONFIG[region]["iot_host"]
        ws_path = build_presigned_ws_path(iot_host, region, aws["AccessKeyId"], aws["SecretKey"], aws["SessionToken"])
        mqtt = aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"lymow-rename-test-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=aiomqtt.TLSParameters(),
            keepalive=30,
            timeout=20,
        )

        async with mqtt:
            topic_out = f"/device/{thing}/pbinput"
            topic_in = f"/device/{thing}/pboutput"
            await mqtt.subscribe(topic_in, qos=1)

            async def query_name() -> str | None:
                await mqtt.publish(topic_out, wrap_envelope(encode_query_map(0)), qos=1)
                try:
                    async with asyncio.timeout(8):
                        async for msg in mqtt.messages:
                            pb = unwrap_envelope(msg.payload)
                            if len(pb) > 200:
                                found = _find_name(pb, HASH)
                                if found is not None:
                                    return found
                except asyncio.TimeoutError:
                    pass
                return None

            print(f"Step 1: query current name of {HASH}")
            current = await query_name()
            print(f"   → {current!r}")

            for new_name in (TEST, ORIGINAL):
                print(f"\nrename {HASH} → {new_name!r}")
                pb = encode_rename_zone(HASH, new_name)
                print(f"   send hex: {pb.hex()}")
                await mqtt.publish(topic_out, wrap_envelope(pb), qos=1)
                await asyncio.sleep(3.0)
                got = await query_name()
                print(f"   → after rename: {got!r}")
                if got != new_name:
                    print(f"   !! mismatch — expected {new_name!r}, got {got!r}")


if __name__ == "__main__":
    asyncio.run(main())
