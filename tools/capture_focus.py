"""5-min capture focused on unknown sub-fields (f5.f9, f5.f10, f6.f5, f12, f22).

Usage:
    uv run python tools/capture_focus.py [seconds]

Writes a JSONL file to tools/mow_focus_<timestamp>.jsonl.
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
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import _decode_fields, _signed32, unwrap_envelope  # noqa: E402


def _f32(raw: int) -> float:
    return struct.unpack("<f", struct.pack("<I", raw & 0xFFFFFFFF))[0]


def _extract_focus(pb: bytes) -> dict:
    """Extract only the fields we are investigating."""
    out: dict = {}
    top = _decode_fields(pb)

    # f5 = PbRobotInfo — surface only fields NOT already decoded
    f5 = next((v for fn, _, v in top if fn == 5 and isinstance(v, bytes)), None)
    if f5:
        ri = _decode_fields(f5)
        for fn, _, v in ri:
            if fn in (6, 7, 8):  # workStatus / isRecharging / isCharging — include as context
                label = {6: "workStatus", 7: "isRecharging", 8: "isCharging"}[fn]
                out[f"f5.{label}"] = bool(v) if fn in (7, 8) else _signed32(v)
            elif fn not in (2, 3, 4):  # skip battery/wifiSignal/lteSignal (not useful here)
                out[f"f5.f{fn}"] = _signed32(v) if isinstance(v, int) else v.hex() if isinstance(v, bytes) else v

    # f6 = RTK — surface undecoded f5
    f6 = next((v for fn, _, v in top if fn == 6 and isinstance(v, bytes)), None)
    if f6:
        rtk = _decode_fields(f6)
        for fn, _, v in rtk:
            if fn not in (1, 2, 3, 4):  # skip already-decoded satellites/east/north/status
                out[f"f6.f{fn}"] = _signed32(v) if isinstance(v, int) else v

    # f9 — unknown outer field
    f9 = next((v for fn, _, v in top if fn == 9 and isinstance(v, bytes)), None)
    if f9:
        for fn, _, v in _decode_fields(f9):
            out[f"f9.f{fn}"] = _signed32(v) if isinstance(v, int) else v.hex() if isinstance(v, bytes) else v

    # f12 = area / progress
    f12 = next((v for fn, _, v in top if fn == 12 and isinstance(v, bytes)), None)
    if f12:
        for fn, wt, v in _decode_fields(f12):
            if fn == 1:
                out["f12.f1_stripCount"] = _signed32(v)
            elif fn == 2:
                out["f12.f2_totalAreaM2"] = round(_f32(v), 1)
            elif fn == 5:
                out["f12.f5_progress_pct"] = round(_f32(v) * 100, 2)
            else:
                out[f"f12.f{fn}"] = v

    # f22 — unknown outer field
    f22 = next((v for fn, _, v in top if fn == 22 and isinstance(v, bytes)), None)
    if f22:
        for fn, _, v in _decode_fields(f22):
            if isinstance(v, bytes):
                try:
                    out[f"f22.f{fn}"] = v.decode("utf-8")
                except Exception:
                    out[f"f22.f{fn}"] = v.hex()
            else:
                out[f"f22.f{fn}"] = _signed32(v) if isinstance(v, int) else v

    return out


async def run(duration: int) -> None:
    user = os.environ.get("LYMOW_USER")
    pw = os.environ.get("LYMOW_PASS")
    if not user or not pw:
        sys.exit("Set LYMOW_USER / LYMOW_PASS")

    async with aiohttp.ClientSession() as session:
        auth = LymowAuth(session)
        tokens = await auth.login(user, pw)
        region = tokens["region"]
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds_data["credentials"]
        from lymow.api import LymowApiClient

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

        async with aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"cap-{uuid.uuid4().hex[:8]}",
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

            print(f"[{datetime.now().isoformat()}] thing={thing}", flush=True)
            print(f"[{datetime.now().isoformat()}] capturing {duration}s …", flush=True)

            rows = []
            try:
                async with asyncio.timeout(duration):
                    async for msg in mqtt.messages:
                        t = datetime.now().isoformat()
                        topic = str(msg.topic)
                        raw = msg.payload if isinstance(msg.payload, bytes) else msg.payload.encode()
                        try:
                            pb = unwrap_envelope(raw)
                            focus = _extract_focus(pb)
                            rows.append({"t": t, "topic": topic, "focus": focus})
                            print(json.dumps({"t": t, "topic": topic, **focus}), flush=True)
                        except Exception as e:
                            print(f"[{t}] err: {e}", flush=True)
            except (asyncio.TimeoutError, TimeoutError):
                pass

            print(f"[{datetime.now().isoformat()}] done — {len(rows)} frames captured", flush=True)
            out = f"tools/mow_focus_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            with open(out, "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            print(f"saved → {out}", flush=True)


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 3600  # default 60 min — enough to capture full mow+return
    asyncio.run(run(secs))
