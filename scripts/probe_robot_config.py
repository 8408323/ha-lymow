"""Probe PbRobotConfig field numbers in a live pboutput response.

Sends USER_CTRL_QUERY_ROBOT_CONFIG (52) and dumps every field the robot
returns in PbOutput.f17, regardless of whether the existing decoder reads
it. Used to discover unmapped fields (vehLedStatus, lcdPinCode shape,
camLedStatus, etc.) without touching the APK Hermes bytecode.

Usage: uv run python scripts/probe_robot_config.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import struct
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
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

import aiomqtt  # noqa: E402
from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import _decode_fields, _first, encode_userctrl, unwrap_envelope, wrap_envelope  # noqa: E402


def _float32(raw: int) -> float:
    return struct.unpack("<f", struct.pack("<I", raw))[0]


def _signed64(v: int) -> int:
    return v if v < (1 << 63) else v - (1 << 64)


def _dump_robot_config(rc_bytes: bytes) -> None:
    """Print every field in PbRobotConfig with type + value annotation."""
    fields = _decode_fields(rc_bytes)
    print(f"  PbRobotConfig ({len(rc_bytes)}B): {rc_bytes.hex()}")
    for fno, wt, val in fields:
        if isinstance(val, bytes):
            extra = ""
            try:
                s = val.decode("utf-8")
                if s.isprintable():
                    extra = f'  str="{s}"'
            except (UnicodeDecodeError, ValueError):
                pass
            # Try to decode as a sub-message
            sub_info = ""
            try:
                sub_fields = _decode_fields(val)
                if sub_fields:
                    sub_info = f"\n      sub-fields: {[(f, w, v if not isinstance(v, bytes) else v.hex()[:32]) for f, w, v in sub_fields]}"
            except Exception:  # noqa: BLE001
                pass
            print(f"    f{fno}({len(val)}B, bytes): {val.hex()}{extra}{sub_info}")
        elif wt == 5:
            print(f"    f{fno}(i32): 0x{val:08x} = {_float32(val):.4f}f / int={val}")
        else:
            sv = _signed64(val)
            print(f"    f{fno}(varint): {val} (signed: {sv})")


async def main() -> None:
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
            identifier=f"lymow-probe-rc-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=aiomqtt.TLSParameters(),
            keepalive=30,
            timeout=20,
        ) as mqtt:
            topic_out = f"/device/{thing}/pbinput"
            topic_in = f"/device/{thing}/pboutput"
            await mqtt.subscribe(topic_in, qos=1)

            print(f"Connected, thing={thing}")
            print("Sending QUERY_ROBOT_CONFIG (userCtrl=52)...")
            await mqtt.publish(topic_out, wrap_envelope(encode_userctrl(52)), qos=1)

            print("\n--- listening 15s for any pboutput with f17 (robotConfig) ---")
            try:
                async with asyncio.timeout(15):
                    async for msg in mqtt.messages:
                        if str(msg.topic).endswith("/notify-app"):
                            continue
                        pb = unwrap_envelope(msg.payload)
                        rc_raw = _first(_decode_fields(pb), 17)
                        if isinstance(rc_raw, bytes) and len(rc_raw) > 0:
                            print(f"\n[robotConfig in {len(pb)}B pboutput]")
                            _dump_robot_config(rc_raw)
                        else:
                            print(f"  [pboutput {len(pb)}B no robotConfig]")
            except asyncio.TimeoutError:
                pass


if __name__ == "__main__":
    asyncio.run(main())
