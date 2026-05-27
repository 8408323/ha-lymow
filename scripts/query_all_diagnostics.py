"""Send every diagnostic / status userCtrl in turn and dump the pboutput response.

Drives discovery of unmapped response fields (RTK per-band diagnostics, network
detail, cleaning summary, run-time config) so we can add proper decoders.

Usage: uv run python scripts/query_all_diagnostics.py
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
from lymow.protocol import _decode_fields, encode_userctrl, unwrap_envelope, wrap_envelope  # noqa: E402

# (label, userCtrl) — only diagnostic / read-only queries; no destructive ops.
QUERIES: list[tuple[str, int]] = [
    ("query_cleaning_summary", 34),
    ("query_robot_config", 52),
    ("query_run_time_config", 51),
    ("query_wifi_4g", 35),
    ("query_net_detail", 53),
    ("query_rtk_diagnostic_l1", 57),
    ("query_rtk_diagnostic_l2", 58),
    ("query_cleaning_info", 24),
    ("query_path", 23),
    ("query_channels", 32),
]


def _float32(raw: int) -> float:
    return struct.unpack("<f", struct.pack("<I", raw))[0]


def _dump_fields(data: bytes, indent: int = 0, max_depth: int = 4) -> None:
    prefix = "  " * indent
    try:
        fields = _decode_fields(data)
    except Exception as e:
        print(f"{prefix}(decode error: {e})")
        return
    for fn, wt, val in fields:
        if isinstance(val, bytes):
            extra = ""
            if 0 < len(val) <= 32:
                try:
                    s = val.decode("utf-8")
                    if s.isprintable():
                        extra = f'  str="{s}"'
                except (UnicodeDecodeError, ValueError):
                    pass
            print(f"{prefix}f{fn}({len(val)}B): {val.hex()[:64]}{extra}")
            if indent < max_depth and len(val) >= 2:
                try:
                    sub = _decode_fields(val)
                    if sub and all(0 < sfn <= 100 for sfn, _, _ in sub):
                        _dump_fields(val, indent + 1, max_depth)
                except Exception:  # noqa: BLE001
                    pass
        elif wt == 5:
            print(f"{prefix}f{fn}(i32): 0x{val:08x} = {_float32(val):.6f}f")
        else:
            sv = val if val < (1 << 63) else val - (1 << 64)
            print(f"{prefix}f{fn}(varint): {sv}")


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

        out_path = os.path.join(os.path.dirname(__file__), "query_all_diagnostics.log")
        log = open(out_path, "w")  # noqa: SIM115

        def _say(msg: str) -> None:
            print(msg)
            log.write(msg + "\n")
            log.flush()

        async with aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"lymow-diag-{uuid.uuid4().hex[:8]}",
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
            _say(f"Connected to MQTT, thing={thing}")

            async def _drain(label: str, timeout: float) -> list[bytes]:
                received: list[bytes] = []
                try:
                    async with asyncio.timeout(timeout):
                        async for msg in mqtt.messages:
                            if str(msg.topic).endswith("/notify-app"):
                                continue
                            pb = unwrap_envelope(msg.payload)
                            received.append(pb)
                            _say(f"  [{label}] recv {len(pb)}B: {pb.hex()[:160]}")
                except asyncio.TimeoutError:
                    pass
                return received

            # Drain heartbeats first
            _say("\n--- baseline (2s) ---")
            baseline = await _drain("baseline", 2.0)
            baseline_hexes = {pb.hex() for pb in baseline}

            for label, code in QUERIES:
                _say(f"\n--- {label} (userCtrl={code}) ---")
                pb = encode_userctrl(code)
                await mqtt.publish(topic_out, wrap_envelope(pb), qos=1)
                responses = await _drain(label, 4.0)
                # Filter out heartbeats we'd see anyway
                novel = [r for r in responses if r.hex() not in baseline_hexes]
                for r in novel:
                    _say(f"\n  *** novel response for {label} ({len(r)}B) ***")
                    _say(f"  hex: {r.hex()}")
                    _say("  decoded:")
                    # capture decoded output
                    import contextlib
                    import io

                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        _dump_fields(r, indent=2)
                    _say(buf.getvalue().rstrip())
                    baseline_hexes.add(r.hex())

            _say("\n--- final 10s listen ---")
            await _drain("final", 10.0)

        log.close()
        _say(f"\nDone. Log written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
