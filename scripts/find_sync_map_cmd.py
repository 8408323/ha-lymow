"""Brute-force the USER_CTRL_SYNC_MAP value by trying candidates in sequence.

For each candidate, we:
  1. Build a sync_map payload with the candidate userCtrl value
  2. Publish it to pbinput
  3. Re-query the map and check if the target zone disappeared

Usage:
    uv run python scripts/find_sync_map_cmd.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

import aiohttp


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
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


_load_dotenv()
_base = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")
for _m in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.const import REGION_CONFIG  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import (  # noqa: E402
    PB_VERSION,
    _encode_map_content,
    _field_bytes,
    _field_i32,
    decode_map_response,
    delete_zone,
    encode_query_map,
    unwrap_envelope,
    wrap_envelope,
)

TARGET_ZONE = "5ilVIZvD"

# Candidates to try — most likely first (23 is the gap, then 25-35)
# We skip 24 since it was already tried without success.
CANDIDATES = [23, 25, 26, 27, 29, 30, 31, 32, 34, 35]


def _encode_sync_map_with_cmd(map_data: dict, cmd: int) -> bytes:
    """Like encode_sync_map but with an overridden command number."""
    content = _encode_map_content(map_data)
    wrapper = _field_bytes(2, _field_bytes(3, content))
    pb = _field_i32(2, PB_VERSION)
    pb += _field_i32(5, cmd)
    pb += _field_bytes(23, wrapper)
    return pb


async def _next_map(mqtt) -> dict | None:
    """Consume messages until we get a map response or the generator is exhausted."""
    async for message in mqtt.messages:
        try:
            raw = unwrap_envelope(message.payload)
            m = decode_map_response(raw)
            if m:
                return m
        except Exception:
            pass
    return None


async def query_map(mqtt, pbin_topic: str) -> dict | None:
    """Publish query_map and wait up to 15 s for the response."""
    await mqtt.publish(pbin_topic, wrap_envelope(encode_query_map()), qos=1)
    try:
        return await asyncio.wait_for(_next_map(mqtt), timeout=15.0)
    except asyncio.TimeoutError:
        return None


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
        client_id = f"lymow-brute-{uuid.uuid4().hex[:8]}"

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
            pbout = f"/device/{thing}/pboutput"
            pbin = f"/device/{thing}/pbinput"
            await mqtt.subscribe(pbout, qos=1)

            # --- Get base map ---
            print("Fetching live map…")
            base_map = await query_map(mqtt, pbin)
            if not base_map:
                print("ERROR: no map response", file=sys.stderr)
                sys.exit(1)

            go_ids = [z["hashId"] for z in base_map.get("goZones", [])]
            print(f"  go zones: {go_ids}")

            if TARGET_ZONE not in go_ids:
                print(f"Zone {TARGET_ZONE} is already gone!")
                return

            updated = delete_zone(base_map, TARGET_ZONE)
            print(f"  After delete: {[z['hashId'] for z in updated.get('goZones', [])]}")

            # --- Try each candidate ---
            for cmd in CANDIDATES:
                print(f"\n--- Trying userCtrl={cmd} ---")
                try:
                    payload = wrap_envelope(_encode_sync_map_with_cmd(updated, cmd))
                except Exception as exc:
                    print(f"  encode error: {exc}")
                    continue

                await mqtt.publish(pbin, payload, qos=1)
                await asyncio.sleep(3)  # let robot process

                # Re-query
                print("  Re-querying map…")
                new_map = await query_map(mqtt, pbin)
                if not new_map:
                    print("  No map response — robot may be sleeping or cmd caused reset")
                    continue

                new_ids = [z["hashId"] for z in new_map.get("goZones", [])]
                print(f"  go zones: {new_ids}")
                if TARGET_ZONE not in new_ids:
                    print(f"\n✅ SUCCESS! userCtrl={cmd} is USER_CTRL_SYNC_MAP!")
                    print(f"  Zone {TARGET_ZONE} has been deleted from the robot.")
                    return
                else:
                    print(f"  Zone still present — cmd {cmd} is not SYNC_MAP")

            print("\n❌ None of the candidates worked. Need ADB logcat capture to find the real value.")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
