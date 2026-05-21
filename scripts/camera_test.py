"""Resolve the robot's live-video WebRTC session via the integration's own code.

Runs the camera path the HA integration uses — auth → /prod/kvs/cmd →
getSignalingChannelEndpoint → get-ice-server-config — and prints the complete
viewer connect config (channel ARN, signaling WSS/HTTPS endpoints, ICE/TURN
servers). This validates everything up to the WebRTC media handshake against
the live robot, without HA installed.

It does NOT pipe video (that needs a WebRTC media client / camera entity); it
proves the integration can hand a viewer a working session.

Usage:
    cp scripts/.env.example scripts/.env   # fill in LYMOW_USER / LYMOW_PASS
    uv run python scripts/camera_test.py
Exit code 0 = a full session resolved for at least one device.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

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
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break


def _load(name: str, path: str) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402


async def main() -> int:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print("Error: set LYMOW_USER and LYMOW_PASS in scripts/.env", file=sys.stderr)
        return 1

    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        tokens = await auth.login(username, password)
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], tokens["region"])
        aws = creds_data["credentials"]
        client = LymowApiClient(session, tokens["AccessToken"], tokens["region"], creds_data["identity_id"])
        client.update_aws_credentials(aws["AccessKeyId"], aws["SecretKey"], aws["SessionToken"])

        devices = await client.get_devices()
        things = [d["deviceThingName"] for d in devices if isinstance(d, dict) and "deviceThingName" in d]
        if not things:
            print("No devices.", file=sys.stderr)
            return 1

        ok_any = False
        for thing in things:
            print(f"\n=== {thing} ===")
            session_info = await client.start_video_session(thing)
            arn = session_info.get("channelARN")
            kvs_creds = session_info.get("credentials")
            region = session_info.get("region") or tokens["region"]
            print(f"  kvs/cmd: channelARN={'yes' if arn else 'MISSING'}, creds={'yes' if kvs_creds else 'MISSING'}")
            if not (arn and isinstance(kvs_creds, dict)):
                print("  [FAIL] no channel/creds — camera may be offline")
                continue
            endpoints = await client.get_signaling_channel_endpoint(arn, kvs_creds, region=region)
            print(
                f"  signaling: WSS={'yes' if endpoints.get('WSS') else 'no'}, HTTPS={'yes' if endpoints.get('HTTPS') else 'no'}"
            )
            ice = []
            if endpoints.get("HTTPS"):
                ice = await client.get_ice_server_config(arn, endpoints["HTTPS"], kvs_creds, region=region)
            print(f"  ICE/TURN servers: {len(ice)}")
            if endpoints.get("WSS") and ice:
                print("  [PASS] full WebRTC viewer session resolved")
                ok_any = True
            else:
                print("  [PARTIAL] session opened but endpoints/ICE incomplete")

    return 0 if ok_any else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
