"""Send a start-mow command targeting specific zone hash IDs.

Usage:
    uv run python scripts/mow_zone.py [HASH_ID ...]

If no HASH_ID is given, queries the robot's live map and lists available zones.
With one or more hash IDs, sends encode_start_zones to the robot and prints
the robot's response (workStatus, battery, etc.).

Example:
    uv run python scripts/mow_zone.py AbCdEfG
    uv run python scripts/mow_zone.py AbCdEfG 1AbC23dE
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

import aiohttp

# ---------------------------------------------------------------------------
# .env loader (identical to cli.py / delete_zone.py)
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
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
        except OSError as exc:
            print(f"Error: could not read {path}: {exc}", file=sys.stderr)
            sys.exit(1)
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
    decode_pboutput,
    encode_query_map,
    encode_start_zones,
    unwrap_envelope,
    wrap_envelope,
)

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def run(zone_ids: list[str]) -> None:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print(
            "Error: LYMOW_USER and LYMOW_PASS must be set.\n"
            "Copy scripts/.env.example to scripts/.env and fill in your credentials.",
            file=sys.stderr,
        )
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
        things = [
            d.get("deviceThingName") or d.get("thingName")
            for d in (devices if isinstance(devices, list) else [])
            if d.get("deviceThingName") or d.get("thingName")
        ]
        if not things:
            print("No devices found.", file=sys.stderr)
            sys.exit(1)
        thing = things[0]
        print(f"  thing={thing}")

        cfg = REGION_CONFIG[region]
        iot_host = cfg["iot_host"]
        assert isinstance(iot_host, str)

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
        client_id = f"lymow-mow-{uuid.uuid4().hex[:8]}"

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

            # --- Always query live map first (list zones + validate hash IDs) ---
            print("Querying live map…")
            await mqtt.publish(pbin_topic, wrap_envelope(encode_query_map()), qos=1)

            async def _recv_map() -> dict | None:
                async for message in mqtt.messages:
                    try:
                        raw = unwrap_envelope(message.payload)
                        m = decode_map_response(raw)
                        if m:
                            return m
                    except Exception:
                        pass
                return None

            try:
                map_data = await asyncio.wait_for(_recv_map(), timeout=15.0)
            except asyncio.TimeoutError:
                map_data = None

            if map_data is None:
                print("ERROR: no map response received within 15 s.", file=sys.stderr)
                sys.exit(1)

            go_zones = map_data.get("goZones", [])
            print(f"\nAvailable go zones ({len(go_zones)}):")
            for z in go_zones:
                hash_id = z.get("hashId") or ""
                if not hash_id:
                    continue
                marker = " ◀ selected" if hash_id in zone_ids else ""
                print(
                    f"  {hash_id:12s}  area={z.get('area', '?'):>6} m²  "
                    f"cutHeight={z.get('cutHeight', '?'):>3} mm{marker}"
                )

            if not zone_ids:
                print("\nNo zone IDs provided — pass hash IDs as arguments to start mowing.")
                first_id = next((z.get("hashId") for z in go_zones if z.get("hashId")), "HASH_ID")
                print("Example:  uv run python scripts/mow_zone.py", first_id)
                return

            # --- Validate that all requested zone IDs exist ---
            go_ids = {z.get("hashId") for z in go_zones if z.get("hashId")}
            missing = [zid for zid in zone_ids if zid not in go_ids]
            if missing:
                print(f"\nERROR: zone(s) not found in live map: {missing}", file=sys.stderr)
                sys.exit(1)

            # --- Send start-zones command ---
            print(f"\nSending start-zones command for: {zone_ids}")
            cmd = encode_start_zones(zone_ids)
            print(f"  payload hex: {cmd.hex()}")
            await mqtt.publish(pbin_topic, wrap_envelope(cmd), qos=1)

            # --- Wait for robot state update ---
            print("Waiting for robot response (up to 10 s)…")

            async def _recv_state() -> dict | None:
                async for message in mqtt.messages:
                    try:
                        raw = unwrap_envelope(message.payload)
                        state = decode_pboutput(raw)
                        if state.get("workStatus") is not None:
                            return state
                    except Exception:
                        pass
                return None

            try:
                state = await asyncio.wait_for(_recv_state(), timeout=10.0)
            except asyncio.TimeoutError:
                state = None

            if state:
                print("\nRobot state:")
                print(f"  workStatus : {state.get('workStatus')}")
                print(f"  battery    : {state.get('battery')}%")
                print(f"  errorCodes : {state.get('errorCodes')}")
                print(f"  warningCodes: {state.get('warningCodes')}")
            else:
                print("(no state response within 10 s)")


def main() -> None:
    zone_ids = sys.argv[1:]
    try:
        asyncio.run(run(zone_ids))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
