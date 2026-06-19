"""Capture the robot's reaction to USER_CTRL_CHARGING_STATION_RESET (userCtrl=17).

Mower must be docked. Subscribes to every /device/<thing>/# subtopic, captures a
short baseline, sends the realign command, then logs every inbound message
(with topic + decoded protobuf fields) for ~60s. Output goes to stdout and to
scripts/dock_realign_capture.log so we can paste into BRANCH_STATUS.md.

Run: uv run python scripts/dock_realign_capture.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
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
from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG, USER_CTRL_CHARGING_STATION_RESET  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import decode_pboutput, encode_userctrl, unwrap_envelope, wrap_envelope  # noqa: E402

LOG_PATH = os.path.join(os.path.dirname(__file__), "dock_realign_capture.log")

BASELINE_S = 5.0
CAPTURE_S = 60.0


class Tee:
    def __init__(self, path: str) -> None:
        self.f = open(path, "w")  # noqa: SIM115

    def emit(self, line: str) -> None:
        print(line)
        self.f.write(line + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def _summarize_payload(topic: str, payload: bytes) -> str:
    leaf = topic.rsplit("/", 1)[-1]
    text_preview = ""
    try:
        text = payload.decode("utf-8")
        text_preview = text.strip()
        if text_preview and (text_preview.startswith("{") or text_preview.startswith("[")):
            try:
                obj = json.loads(text_preview)
                text_preview = json.dumps(obj, separators=(",", ":"))
            except ValueError:
                pass
    except UnicodeDecodeError:
        text_preview = ""

    inner = None
    if text_preview.startswith("{") and '"message"' in text_preview:
        try:
            inner = unwrap_envelope(payload)
        except Exception:  # noqa: BLE001
            inner = None

    parts = [f"len={len(payload)}"]
    if text_preview and not text_preview.startswith("{") and not text_preview.startswith("["):
        parts.append(f"text={text_preview[:200]}")
    elif text_preview and inner is None:
        parts.append(f"json={text_preview[:300]}")

    if inner is not None:
        parts.append(f"pb_hex={inner.hex()[:200]}")
        if leaf == "pboutput":
            try:
                decoded = decode_pboutput(inner)
                parts.append(f"pboutput={json.dumps(decoded, default=str)[:600]}")
            except Exception as exc:  # noqa: BLE001
                parts.append(f"decode_error={exc!s}")
    return " | ".join(parts)


async def run() -> None:
    user = os.environ["LYMOW_USER"]
    pwd = os.environ["LYMOW_PASS"]
    tee = Tee(LOG_PATH)
    try:
        async with aiohttp.ClientSession() as session:
            auth = LymowAuth(session)
            tee.emit("→ login")
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
            tee.emit(f"→ thing={thing} region={region}")

            async with aiomqtt.Client(
                hostname=iot_host,
                port=443,
                identifier=f"lymow-dockrealign-{uuid.uuid4().hex[:8]}",
                transport="websockets",
                websocket_path=ws_path,
                websocket_headers={"Host": iot_host},
                tls_params=aiomqtt.TLSParameters(),
                keepalive=30,
                timeout=20,
            ) as mqtt:
                # Only subscribe to topics our AWS IoT policy actually allows
                # (verified by sniff_all_topics: pboutput + notify-app work, others
                # are denied and the broker closes the session if we try).
                topics = [f"/device/{thing}/pboutput", f"/device/{thing}/notify-app"]
                for t in topics:
                    await mqtt.subscribe(t, qos=1)
                    tee.emit(f"→ subscribed to {t}")

                async def pump(label: str, duration: float) -> None:
                    end = time.monotonic() + duration
                    try:
                        async with asyncio.timeout(duration + 1):
                            async for msg in mqtt.messages:
                                topic = str(msg.topic)
                                payload = (
                                    bytes(msg.payload)
                                    if isinstance(msg.payload, (bytes, bytearray))
                                    else msg.payload.encode()
                                )
                                ts = time.strftime("%H:%M:%S")
                                summary = _summarize_payload(topic, payload)
                                tee.emit(f"[{ts}] {label} {topic}  {summary}")
                                if time.monotonic() >= end:
                                    return
                    except asyncio.TimeoutError:
                        return

                tee.emit(f"\n=== BASELINE {BASELINE_S}s (no command sent) ===")
                await pump("BASE", BASELINE_S)

                cmd = encode_userctrl(USER_CTRL_CHARGING_STATION_RESET)
                envelope = wrap_envelope(cmd)
                tee.emit(
                    f"\n=== SEND userCtrl={USER_CTRL_CHARGING_STATION_RESET} "
                    f"CHARGING_STATION_RESET pb_hex={cmd.hex()} envelope={envelope} ==="
                )
                await mqtt.publish(f"/device/{thing}/pbinput", envelope, qos=1)
                tee.emit(f"=== CAPTURE {CAPTURE_S}s post-send ===")
                await pump("POST", CAPTURE_S)

                tee.emit("\n=== DONE ===")
    finally:
        tee.close()


if __name__ == "__main__":
    asyncio.run(run())
