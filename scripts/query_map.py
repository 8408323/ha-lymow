"""Send userCtrl=19 variants via MQTT and dump the raw map protobuf response.

Run this while the robot has zones defined and is online.

Usage:
    uv run python scripts/query_map.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
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
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import (  # noqa: E402
    PB_VERSION,
    _decode_fields,
    _field_bytes,
    _field_i32,
    decode_pboutput,
    encode_query_map,
    encode_query_schedules,
    unwrap_envelope,
    wrap_envelope,
)


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
            floats = ""
            if len(val) > 0 and len(val) % 4 == 0 and len(val) <= 128:
                fs = [round(_float32(struct.unpack_from("<I", val, i)[0]), 4) for i in range(0, len(val), 4)]
                floats = f"  floats={fs}"
            # Try to decode as UTF-8 string
            try:
                s = val.decode("utf-8")
                if s.isprintable():
                    floats += f'  str="{s}"'
            except Exception:
                pass
            print(f"{prefix}f{fn}({len(val)}B): {val.hex()}{floats}")
            if indent < max_depth and len(val) >= 2:
                try:
                    sub = _decode_fields(val)
                    if sub and all(sfn <= 100 for sfn, _, _ in sub):
                        _dump_fields(val, indent + 1, max_depth)
                except Exception:
                    pass
        elif wt == 5:
            f = _float32(val)
            print(f"{prefix}f{fn}(i32): 0x{val:08x} = {f:.6f}f")
        else:
            sv = val if val < (1 << 63) else val - (1 << 64)
            print(f"{prefix}f{fn}(varint): {sv}")


# All command variants to try
def _commands() -> list[tuple[str, bytes]]:
    # simple — no sub-message
    simple = _field_i32(2, PB_VERSION) + _field_i32(5, 19)
    # flag-only (no queryIndex)
    flag_only = _field_i32(2, PB_VERSION) + _field_i32(5, 19) + _field_bytes(13, _field_i32(2, 1))
    # queryIndex=0 flag=1 (current)
    qi0 = encode_query_map(0)
    # queryIndex=1 flag=1
    qi1 = encode_query_map(1)
    # queryIndex=2 flag=1
    qi2 = encode_query_map(2)
    # schedules query
    sched = encode_query_schedules()
    # userCtrl=23 (QUERY_PATH)
    path_cmd = _field_i32(2, PB_VERSION) + _field_i32(5, 23)

    return [
        ("simple (no sub-msg)", simple),
        ("flag_only", flag_only),
        ("queryIndex=0 flag=1", qi0),
        ("queryIndex=1 flag=1", qi1),
        ("queryIndex=2 flag=1", qi2),
        ("query_schedules (ctrl=20)", sched),
        ("query_path (ctrl=23)", path_cmd),
    ]


async def main() -> None:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print("Error: set LYMOW_USER and LYMOW_PASS in scripts/.env", file=sys.stderr)
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        print("Authenticating...")
        auth = LymowAuth(session)
        tokens = await auth.login(username, password)
        region = tokens["region"]
        creds = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds["credentials"]

        from lymow.api import LymowApiClient

        client_api = LymowApiClient(session, tokens["AccessToken"], region, creds["identity_id"])
        devices = await client_api.get_devices()
        things = [
            d.get("deviceThingName") or d.get("thingName")
            for d in (devices if isinstance(devices, list) else [])
            if d.get("deviceThingName") or d.get("thingName")
        ]
        if not things:
            print("No devices found")
            return

        thing = things[0]
        iot_host = REGION_CONFIG[region]["iot_host"]
        assert isinstance(iot_host, str)

        ws_path = build_presigned_ws_path(iot_host, region, aws["AccessKeyId"], aws["SecretKey"], aws["SessionToken"])

        print(f"Connecting to MQTT ({thing})...")
        mqtt = aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"lymow-map-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=aiomqtt.TLSParameters(),
            keepalive=30,
            timeout=20,
        )

        all_payloads: list[tuple[str, bytes]] = []  # (label, pb_bytes)

        async with mqtt:
            print("Connected.")
            topic_out = f"/device/{thing}/pbinput"
            topic_in = f"/device/{thing}/pboutput"
            await mqtt.subscribe(topic_in, qos=1)
            await mqtt.subscribe(f"/device/{thing}/notify-app", qos=1)

            async def _drain(label: str, timeout: float) -> list[bytes]:
                received: list[bytes] = []
                try:
                    async with asyncio.timeout(timeout):
                        async for msg in mqtt.messages:
                            t = str(msg.topic)
                            if t.endswith("/notify-app"):
                                data = json.loads(msg.payload)
                                print(f"  [notify] robotState={data.get('robotState')}")
                                continue
                            pb = unwrap_envelope(msg.payload)
                            received.append(pb)
                            all_payloads.append((label, pb))
                            size_tag = f" *** LARGE ({len(pb)}B) ***" if len(pb) > 150 else f" ({len(pb)}B)"
                            print(f"  [recv{size_tag}] hex: {pb.hex()}")
                            if len(pb) > 150:
                                print("  decoded fields:")
                                _dump_fields(pb, indent=2)
                except asyncio.TimeoutError:
                    pass
                return received

            # Drain initial state push
            print("\n--- Initial state (2s) ---")
            await _drain("initial", 2.0)

            # Try each command variant
            cmds = _commands()
            for label, cmd in cmds:
                print(f"\n--- Sending {label}: {cmd.hex()} ---")
                await mqtt.publish(topic_out, wrap_envelope(cmd), qos=1)
                responses = await _drain(label, 5.0)
                if any(len(pb) > 150 for pb in responses):
                    print("  ↑ Got large response — trying next query indices too")

            # Final 10s listen for anything delayed
            print("\n--- Final listen (10s) ---")
            await _drain("final", 10.0)

        # Summary
        large = [(lbl, pb) for lbl, pb in all_payloads if len(pb) > 150]
        print(f"\n{'=' * 60}")
        print(f"Total messages: {len(all_payloads)}, large (>150B): {len(large)}")

        if large:
            for lbl, pb in large:
                out = os.path.join(os.path.dirname(__file__), "map_response.bin")
                with open(out, "wb") as f:
                    f.write(pb)
                print(f"\nLargest map response from '{lbl}' ({len(pb)}B) saved to {out}")
                print(f"hex: {pb.hex()}")
        else:
            print("\nNo large messages received.")
            print("All messages received:")
            for lbl, pb in all_payloads:
                state = decode_pboutput(pb)
                print(f"  [{lbl}] {len(pb)}B workStatus={state.get('workStatus')} battery={state.get('battery')}")


if __name__ == "__main__":
    asyncio.run(main())
