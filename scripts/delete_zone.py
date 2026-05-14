"""Delete a zone from the robot's map and push the updated map back.

Usage:
    uv run python scripts/delete_zone.py [HASH_ID]

If HASH_ID is omitted, defaults to "5ilVIZvD" (the 12 m² zone).
Credentials are read from scripts/.env (LYMOW_USER / LYMOW_PASS).

Flow:
  1. Authenticate → get AWS credentials
  2. Connect to AWS IoT MQTT
  3. Send query_map — wait up to 15 s for the live map response
  4. Delete the named zone (and its child no-go zones)
  5. Send sync_map with the updated map
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

import aiohttp

# ---------------------------------------------------------------------------
# Minimal .env loader (same as cli.py)
# ---------------------------------------------------------------------------


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
    decode_map_response,
    delete_zone,
    encode_delete_zone,
    encode_query_map,
    unwrap_envelope,
    wrap_envelope,
)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def run(target_hash_id: str) -> None:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print("Error: set LYMOW_USER and LYMOW_PASS in scripts/.env", file=sys.stderr)
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        # --- Auth ---
        print("Logging in…")
        auth = LymowAuth(session)
        tokens = await auth.login(username, password)
        region = tokens["region"]
        print(f"  region={region}")

        print("Getting AWS credentials…")
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds_data["credentials"]
        identity_id = creds_data["identity_id"]
        print(f"  identity_id={identity_id}")

        client = LymowApiClient(
            session=session,
            access_token=tokens["AccessToken"],
            region=region,
            identity_id=identity_id,
        )

        # --- Device ---
        print("Getting device list…")
        devices = await client.get_devices()
        things = [d["deviceThingName"] for d in devices]
        if not things:
            print("No devices found.", file=sys.stderr)
            sys.exit(1)
        thing = things[0]
        print(f"  thing={thing}")

        cfg = REGION_CONFIG[region]
        iot_host = cfg["iot_host"]

        # --- MQTT connect ---
        print(f"Connecting to MQTT ({iot_host})…")
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
        client_id = f"lymow-del-{uuid.uuid4().hex[:8]}"

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
            pbout_topic = f"/device/{thing}/pboutput"
            pbin_topic = f"/device/{thing}/pbinput"

            await mqtt.subscribe(pbout_topic, qos=1)
            print("  subscribed to pboutput")

            # --- Query live map ---
            print("Querying live map…")
            payload = wrap_envelope(encode_query_map())
            await mqtt.publish(pbin_topic, payload, qos=1)

            map_data: dict | None = None
            deadline = asyncio.get_running_loop().time() + 15.0

            async for message in mqtt.messages:
                try:
                    raw = unwrap_envelope(message.payload)
                    m = decode_map_response(raw)
                    if m:  # non-empty → it's the map response
                        map_data = m
                        break
                except Exception:
                    pass
                if asyncio.get_running_loop().time() > deadline:
                    break

            if map_data is None:
                print("ERROR: no map response received within 15 s.", file=sys.stderr)
                sys.exit(1)

            go_ids = [z["hashId"] for z in map_data.get("goZones", [])]
            nogo_ids = [n["hashId"] for n in map_data.get("nogoZones", [])]
            print(f"  live map: {len(go_ids)} go zones, {len(nogo_ids)} nogo zones")
            for z in map_data.get("goZones", []):
                print(f"    goZone  {z['hashId']}  area={z.get('area')} m²")
            for n in map_data.get("nogoZones", []):
                print(f"    nogoZone {n['hashId']}  area={n.get('area')}  parent={n.get('parentZoneHashId')}")

            # --- Delete ---
            if target_hash_id not in go_ids and target_hash_id not in nogo_ids:
                print(f"ERROR: zone '{target_hash_id}' not found in live map.", file=sys.stderr)
                sys.exit(1)

            updated = delete_zone(map_data, target_hash_id)
            removed_go = [z for z in map_data.get("goZones", []) if z["hashId"] == target_hash_id]
            removed_nogo = [n for n in map_data.get("nogoZones", []) if n.get("parentZoneHashId") == target_hash_id]
            print(f"\nDeleting zone '{target_hash_id}':")
            for z in removed_go:
                print(f"  - goZone  {z['hashId']}  area={z.get('area')} m²")
            for n in removed_nogo:
                print(f"  - nogoZone {n['hashId']} (child of {target_hash_id})")
            print(
                f"  Remaining: {len(updated.get('goZones', []))} go zones, {len(updated.get('nogoZones', []))} nogo zones"
            )

            # --- Send delete command (USER_CTRL_CLEAR_ZONE=8, field 12=map, single zone only) ---
            from lymow.const import USER_CTRL_CLEAR_ZONE

            if target_hash_id in go_ids:
                delete_pb = encode_delete_zone(target_hash_id)
                print(
                    f"\nSending delete_zone (USER_CTRL_CLEAR_ZONE={USER_CTRL_CLEAR_ZONE}, field 12=map, hashId={target_hash_id!r}, {len(delete_pb)} B)…"
                )
            else:
                print(
                    f"ERROR: zone '{target_hash_id}' is a nogoZone or channel — only goZone deletion is implemented.",
                    file=sys.stderr,
                )
                sys.exit(1)
            delete_payload = wrap_envelope(delete_pb)
            await mqtt.publish(pbin_topic, delete_payload, qos=1)

            # --- Wait for confirmation re-query ---
            print("Waiting 5 s for robot to apply change…")
            await asyncio.sleep(5)

            print("Re-querying map to confirm…")
            await mqtt.publish(pbin_topic, wrap_envelope(encode_query_map()), qos=1)
            confirm_deadline = asyncio.get_running_loop().time() + 15.0
            confirm_data: dict | None = None
            async for message in mqtt.messages:
                try:
                    raw = unwrap_envelope(message.payload)
                    m = decode_map_response(raw)
                    if m:
                        confirm_data = m
                        break
                except Exception:
                    pass
                if asyncio.get_running_loop().time() > confirm_deadline:
                    break

            if confirm_data is None:
                print("WARNING: no confirmation map response received.", file=sys.stderr)
            else:
                remaining_go = [z["hashId"] for z in confirm_data.get("goZones", [])]
                if target_hash_id in remaining_go:
                    print(f"FAIL — zone {target_hash_id!r} is STILL present: {remaining_go}")
                else:
                    print(f"SUCCESS — zone {target_hash_id!r} deleted. Remaining go zones: {remaining_go}")


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "5ilVIZvD"
    asyncio.run(run(target))


if __name__ == "__main__":
    main()
