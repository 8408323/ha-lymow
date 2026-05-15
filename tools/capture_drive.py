"""Capture pboutput + notify-app during manual drive / joystick session.

Usage:
    uv run python tools/capture_drive.py [seconds=120]

Writes every decoded message to tools/drive_<timestamp>.jsonl.
Run BEFORE opening the app's manual-drive / Bluetooth-control screen.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import struct
import sys
import uuid
from datetime import datetime

import aiohttp


def _load_dotenv() -> None:
    for path in (os.path.join("scripts", ".env"), ".env"):
        if not os.path.isfile(path):
            continue
        with open(path) as fh:
            for line in fh:
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
from lymow.protocol import _decode_fields, _signed32, decode_pboutput, unwrap_envelope  # noqa: E402


def _f32(raw: int) -> float:
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


def _decode_all(pb: bytes) -> dict:
    """Full decode: all top-level and nested fields."""
    out: dict = {}
    top = _decode_fields(pb)

    for fn, wt, v in top:
        if isinstance(v, bytes):
            # Attempt sub-message decode
            try:
                sub = _decode_fields(v)
                for sfn, swt, sv in sub:
                    key = f"f{fn}.f{sfn}"
                    if isinstance(sv, bytes):
                        # Try UTF-8, fallback to hex
                        try:
                            out[key] = sv.decode("utf-8")
                        except Exception:
                            out[key] = sv.hex()
                    elif swt == 5:  # float32
                        out[key] = round(_f32(sv), 4)
                    else:
                        out[key] = _signed32(sv) if isinstance(sv, int) else sv
            except Exception:
                out[f"f{fn}"] = v.hex()
        elif isinstance(v, int):
            out[f"f{fn}"] = _signed32(v)
        else:
            out[f"f{fn}"] = v

    # Also include the high-level decode for context
    try:
        hl = decode_pboutput(pb)
        out["_decoded"] = hl
    except Exception:
        pass

    return out


async def run(duration: int) -> None:
    user = os.environ.get("LYMOW_USER")
    pw = os.environ.get("LYMOW_PASS")
    if not user or not pw:
        sys.exit("Set LYMOW_USER / LYMOW_PASS in scripts/.env")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), f"drive_{ts}.jsonl")

    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        print("Logging in…")
        tokens = await auth.login(user, pw)
        region = tokens["region"]
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds_data["credentials"]

        client = LymowApiClient(
            session=session,
            access_token=tokens["AccessToken"],
            region=region,
            identity_id=creds_data["identity_id"],
        )
        devices = await client.get_devices()
        thing = next(
            (
                d.get("deviceThingName") or d.get("thingName")
                for d in (devices if isinstance(devices, list) else [])
                if d.get("deviceThingName") or d.get("thingName")
            ),
            None,
        )
        if not thing:
            sys.exit("No device found")

        cfg = REGION_CONFIG[region]
        iot_host = cfg["iot_host"]
        ws_path = build_presigned_ws_path(
            iot_host, region, aws["AccessKeyId"], aws["SecretKey"], aws.get("SessionToken")
        )
        tls = aiomqtt.TLSParameters()

        print(f"thing={thing}")
        print(f"Output → {out_path}")
        print(f"Capturing {duration}s of pboutput + notify-app …\n")

        async with aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"drive-cap-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=tls,
            keepalive=30,
            timeout=15,
        ) as mqtt:
            pbout = f"/device/{thing}/pboutput"
            notify = f"/device/{thing}/notify-app"
            await mqtt.subscribe(pbout, qos=1)
            await mqtt.subscribe(notify, qos=1)
            print(f"Subscribed to:\n  {pbout}\n  {notify}\n")
            print("Drive the robot NOW (open app → manual / BT control → joystick)\n")

            deadline = asyncio.get_event_loop().time() + duration
            msg_count = 0

            async for message in mqtt.messages:
                now = datetime.now().isoformat()
                topic = str(message.topic)
                raw_payload = bytes(message.payload)  # type: ignore[arg-type]

                record: dict = {
                    "ts": now,
                    "topic": topic,
                    "raw_hex": raw_payload.hex(),
                }

                try:
                    pb = unwrap_envelope(raw_payload)
                    record["fields"] = _decode_all(pb)
                except Exception as exc:
                    record["decode_error"] = str(exc)

                with open(out_path, "a") as f:
                    f.write(json.dumps(record) + "\n")

                # Print a one-liner summary
                decoded = record.get("fields", {})
                work_status = decoded.get("f5.f6") or decoded.get("_decoded", {}).get("workStatus", "?")
                is_charging = decoded.get("f5.f8") or decoded.get("_decoded", {}).get("isCharging", "?")
                print(
                    f"[{now[11:23]}] topic={topic.split('/')[-1]:12s}  "
                    f"workStatus={work_status}  isCharging={is_charging}  "
                    f"len={len(raw_payload)}"
                )
                msg_count += 1

                if asyncio.get_event_loop().time() >= deadline:
                    break

    print(f"\n✓ Captured {msg_count} messages → {out_path}")


def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    try:
        asyncio.run(run(duration))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
