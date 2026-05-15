"""Debug script: connect via MQTT and dump raw protobuf from the robot.

Also tries REST IoT Data fallback if MQTT fails.

Usage:
    uv run python scripts/debug_mqtt.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from urllib.parse import quote

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
for _mod in ("const", "auth", "api", "protocol", "mqtt"):
    _load(f"lymow.{_mod}", os.path.join(_base, f"{_mod}.py"))

from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402
from lymow.mqtt import build_presigned_ws_path  # noqa: E402
from lymow.protocol import (  # noqa: E402
    _decode_fields,
    decode_pboutput,
    encode_query_map,
    unwrap_envelope,
    wrap_envelope,
)

# ---------------------------------------------------------------------------
# SigV4 for IoT Data REST API (not WebSocket — used as fallback)
# ---------------------------------------------------------------------------


def _hmac_sha256(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode("utf-8"), hashlib.sha256).digest()


def _sigv4_headers(
    method: str,
    host: str,
    uri: str,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    session_token: str,
    payload: bytes = b"",
    query: str = "",
) -> dict[str, str]:
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_str = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(payload).hexdigest()

    canonical = (
        f"{method}\n{uri}\n{query}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n\n"
        f"host;x-amz-content-sha256;x-amz-date;x-amz-security-token\n"
        f"{payload_hash}"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date;x-amz-security-token"
    scope = f"{date_str}/{region}/{service}/aws4_request"
    sts = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    k = _hmac_sha256(("AWS4" + secret_key).encode(), date_str)
    k = _hmac_sha256(k, region)
    k = _hmac_sha256(k, service)
    k = _hmac_sha256(k, "aws4_request")
    signature = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()

    auth = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        "Authorization": auth,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "x-amz-security-token": session_token,
    }


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------


def _pretty_fields(data: bytes, indent: int = 0) -> None:
    prefix = "  " * indent
    try:
        fields = _decode_fields(data)
    except Exception as e:
        print(f"{prefix}  (decode error: {e})")
        return
    for field_no, wire_type, value in fields:
        wt_name = {0: "varint", 1: "64bit", 2: "bytes", 5: "32bit"}.get(wire_type, f"wt{wire_type}")
        if isinstance(value, bytes):
            if len(value) <= 64:
                print(f"{prefix}  field {field_no} ({wt_name}): {value.hex()} ({len(value)}B)")
            else:
                print(f"{prefix}  field {field_no} ({wt_name}): {value[:32].hex()}... ({len(value)}B)")
            # Attempt recursive decode
            try:
                sub = _decode_fields(value)
                if sub:
                    print(f"{prefix}    → sub-message:")
                    for sf, swt, sv in sub:
                        swt_name = {0: "varint", 1: "64bit", 2: "bytes", 5: "32bit"}.get(swt, f"wt{swt}")
                        if isinstance(sv, bytes):
                            print(f"{prefix}      field {sf} ({swt_name}): {sv.hex()} ({len(sv)}B)")
                        else:
                            print(f"{prefix}      field {sf} ({swt_name}): {sv}")
            except Exception:
                pass
        else:
            print(f"{prefix}  field {field_no} ({wt_name}): {value}")


# ---------------------------------------------------------------------------
# REST IoT Data fallback: GET /things/{thing}/shadow
# ---------------------------------------------------------------------------


async def try_iot_rest_shadow(
    session: aiohttp.ClientSession,
    iot_host: str,
    region: str,
    thing_name: str,
    access_key: str,
    secret_key: str,
    session_token: str,
) -> None:
    """GET the device shadow via IoT Data REST (no MQTT/WebSocket needed)."""
    print(f"\n--- IoT Data REST: GET shadow for {thing_name} ---")
    uri = f"/things/{quote(thing_name, safe='')}/shadow"
    headers = _sigv4_headers(
        method="GET",
        host=iot_host,
        uri=uri,
        region=region,
        service="iotdata",
        access_key=access_key,
        secret_key=secret_key,
        session_token=session_token,
    )
    url = f"https://{iot_host}{uri}"
    try:
        async with session.get(url, headers=headers) as resp:
            body = await resp.text()
            print(f"  Status: {resp.status}")
            try:
                print(json.dumps(json.loads(body), indent=2))
            except Exception:
                print(body)
    except Exception as exc:
        print(f"  Error: {exc}")


# ---------------------------------------------------------------------------
# REST IoT Data: list thing principals (diagnostics)
# ---------------------------------------------------------------------------


async def try_iot_list_principals(
    session: aiohttp.ClientSession,
    region: str,
    thing_name: str,
    access_key: str,
    secret_key: str,
    session_token: str,
) -> None:
    """Use IoT control-plane to list thing principals (checks IAM perms)."""
    print(f"\n--- IoT Control Plane: list principals for {thing_name} ---")
    host = f"iot.{region}.amazonaws.com"
    uri = f"/things/{quote(thing_name, safe='')}/principals"
    headers = _sigv4_headers(
        method="GET",
        host=host,
        uri=uri,
        region=region,
        service="execute-api",
        access_key=access_key,
        secret_key=secret_key,
        session_token=session_token,
    )
    url = f"https://{host}{uri}"
    try:
        async with session.get(url, headers=headers) as resp:
            body = await resp.text()
            print(f"  Status: {resp.status}")
            print(body[:500])
    except Exception as exc:
        print(f"  Error: {exc}")


# ---------------------------------------------------------------------------
# MQTT: connect + publish userCtrl=19 + dump response
# ---------------------------------------------------------------------------


async def try_mqtt_query_map(
    iot_host: str,
    region: str,
    thing_name: str,
    access_key: str,
    secret_key: str,
    session_token: str,
) -> None:
    import uuid

    import aiomqtt

    print(f"\n--- MQTT: query map for {thing_name} ---")
    ws_path = build_presigned_ws_path(iot_host, region, access_key, secret_key, session_token)
    print(f"  WS path (first 120 chars): {ws_path[:120]}...")

    cmd = encode_query_map(query_index=0)
    envelope = wrap_envelope(cmd)
    topic_out = f"/device/{thing_name}/pbinput"
    topic_in = f"/device/{thing_name}/pboutput"
    topic_notify = f"/device/{thing_name}/notify-app"

    try:
        client = aiomqtt.Client(
            hostname=iot_host,
            port=443,
            identifier=f"lymow-debug-{uuid.uuid4().hex[:8]}",
            transport="websockets",
            websocket_path=ws_path,
            websocket_headers={"Host": iot_host},
            tls_params=aiomqtt.TLSParameters(),
            keepalive=30,
            timeout=20,
        )
        async with client:
            print("  MQTT connected!")
            await client.subscribe(topic_in, qos=1)
            await client.subscribe(topic_notify, qos=1)
            print(f"  Subscribed to {topic_in}")

            # Wait a moment for initial state push
            print("  Waiting 3s for initial pboutput state push...")
            try:
                async with asyncio.timeout(3):
                    async for message in client.messages:
                        t = str(message.topic)
                        payload = message.payload
                        print(f"\n  [INITIAL] topic={t}")
                        if t.endswith("/pboutput"):
                            pb = unwrap_envelope(payload)
                            print(f"  pb bytes ({len(pb)}B): {pb.hex()}")
                            state = decode_pboutput(pb)
                            print(f"  decoded state: {json.dumps(state, indent=4)}")
                            print("  raw fields:")
                            _pretty_fields(pb)
                        elif t.endswith("/notify-app"):
                            print(f"  notify payload: {payload}")
            except asyncio.TimeoutError:
                pass

            # Send userCtrl=19 (query map)
            print(f"\n  Publishing userCtrl=19 to {topic_out}")
            print(f"  cmd hex: {cmd.hex()}")
            await client.publish(topic_out, envelope, qos=1)

            # Wait for response
            print("  Waiting 15s for map response...")
            try:
                async with asyncio.timeout(15):
                    async for message in client.messages:
                        t = str(message.topic)
                        payload = message.payload
                        print(f"\n  [RESPONSE] topic={t}")
                        if t.endswith("/pboutput"):
                            pb = unwrap_envelope(payload)
                            print(f"  pb bytes ({len(pb)}B):")
                            print(f"  hex: {pb.hex()}")
                            print(f"  decoded state: {json.dumps(decode_pboutput(pb), indent=4)}")
                            print("  raw fields (full recursive decode):")
                            _pretty_fields(pb)
            except asyncio.TimeoutError:
                print("  Timeout — no further messages")

    except Exception as exc:
        print(f"  MQTT failed: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    username = os.environ.get("LYMOW_USER")
    password = os.environ.get("LYMOW_PASS")
    if not username or not password:
        print("Error: LYMOW_USER and LYMOW_PASS must be set in scripts/.env", file=sys.stderr)
        sys.exit(1)

    async with aiohttp.ClientSession() as session:
        print("--- Authenticating ---")
        auth = LymowAuth(session)
        tokens = await auth.login(username, password)
        region = tokens["region"]
        print(f"  Region: {region}")

        print("\n--- Getting AWS credentials ---")
        creds_data = await auth.get_aws_credentials(tokens["IdToken"], region)
        aws = creds_data["credentials"]
        identity_id = creds_data["identity_id"]
        access_key = aws["AccessKeyId"]
        secret_key = aws["SecretKey"]
        session_token = aws["SessionToken"]
        print(f"  Identity ID: {identity_id}")

        client = LymowApiClient(session, tokens["AccessToken"], region, identity_id)
        client.update_aws_credentials(access_key, secret_key, session_token)

        print("\n--- Device list ---")
        devices = await client.get_devices()
        things = []
        for device in devices if isinstance(devices, list) else []:
            thing = device.get("deviceThingName") or device.get("thingName")
            if thing:
                things.append(thing)
                print(f"  thing: {thing}")

        if not things:
            print("No devices found")
            return

        thing = things[0]
        from lymow.const import REGION_CONFIG

        iot_host = REGION_CONFIG[region]["iot_host"]
        assert isinstance(iot_host, str)

        # Try REST IoT Data shadow first (no WebSocket, reliable from WSL)
        await try_iot_rest_shadow(session, iot_host, region, thing, access_key, secret_key, session_token)

        # Try MQTT
        await try_mqtt_query_map(iot_host, region, thing, access_key, secret_key, session_token)


if __name__ == "__main__":
    asyncio.run(main())
