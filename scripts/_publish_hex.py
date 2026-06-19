"""Throwaway: publish a raw pbinput hex frame. Usage: uv run python scripts/_publish_hex.py <hex>"""

from __future__ import annotations

import asyncio
import importlib.util
import os
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
                if k.strip() and k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
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
from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import wrap_envelope  # noqa: E402


async def main(hex_frame: str) -> None:
    pb = bytes.fromhex(hex_frame)
    user = os.environ["LYMOW_USER"]
    pwd = os.environ["LYMOW_PASS"]
    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        tokens = await auth.login(user, pwd)
        region = tokens["region"]
        creds = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds["credentials"]
        api = LymowApiClient(session, tokens["AccessToken"], region, creds["identity_id"])
        devices = await api.get_devices()
        thing = devices[0]["deviceThingName"]
        iot_host = REGION_CONFIG[region]["iot_host"]
        ws_path = build_presigned_ws_path(
            iot_host, region, aws["AccessKeyId"], aws["SecretKey"], aws.get("SessionToken")
        )
        async with aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"lymow-pub-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=aiomqtt.TLSParameters(),
            keepalive=30,
            timeout=20,
        ) as mqtt:
            print(f"publish {len(pb)}B to /device/{thing}/pbinput: {pb.hex()}")
            await mqtt.publish(f"/device/{thing}/pbinput", wrap_envelope(pb), qos=1)
            await asyncio.sleep(2)
            print("sent")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
