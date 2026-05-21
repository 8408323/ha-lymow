"""Live WebRTC viewer for the Lymow robot camera — proves the feed works locally.

Resolves the session with the integration's own code (auth → kvs/cmd →
getSignalingChannelEndpoint → get-ice-server-config), then performs the full
KVS WebRTC viewer handshake captured from the app:

  1. SigV4-presign + open the signaling WebSocket (Role=VIEWER).
  2. Send an SDP_OFFER, receive the master's SDP_ANSWER, trickle ICE.
  3. aiortc establishes the peer connection; on the first video frames we save
     a JPEG (proof the feed is live) and exit.

This is the "verify the feed before touching HACS" step. It is a live tool —
it needs real credentials and the robot online, and the WebRTC/media layer
typically needs a couple of live iterations to tune.

Requires extra deps (not in the integration); run them ephemerally with uv:

    cp scripts/.env.example scripts/.env        # LYMOW_USER / LYMOW_PASS
    uv run --with aiortc --with websockets python scripts/camera_feed_test.py

Writes the first decoded frame to tools/camera_frame.jpg (gitignored).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from urllib.parse import quote, urlparse


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
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
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
for _m in ("const", "auth", "api"):
    _load(f"lymow.{_m}", os.path.join(_base, f"{_m}.py"))

import aiohttp  # noqa: E402
from lymow.api import LymowApiClient  # noqa: E402
from lymow.auth import LymowAuth  # noqa: E402


def _hmac(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode(), hashlib.sha256).digest()


def _presign_wss(endpoint: str, channel_arn: str, client_id: str, region: str, creds: dict) -> str:
    """SigV4-presign the KVS signaling WebSocket URL as a VIEWER.

    Mirrors the captured connect: query carries X-Amz-ChannelARN +
    X-Amz-ClientId alongside the standard SigV4 params, and (unlike IoT MQTT)
    the security token is part of the signed canonical query string.
    """
    host = urlparse(endpoint).netloc
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_str = now.strftime("%Y%m%d")
    scope = f"{date_str}/{region}/kinesisvideo/aws4_request"
    q = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-ChannelARN": channel_arn,
        "X-Amz-ClientId": client_id,
        "X-Amz-Credential": f"{creds['accessKeyId']}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "299",
        "X-Amz-Security-Token": creds["sessionToken"],
        "X-Amz-SignedHeaders": "host",
    }
    canonical_qs = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(q.items()))
    canonical = f"GET\n/\n{canonical_qs}\nhost:{host}\n\nhost\n{hashlib.sha256(b'').hexdigest()}"
    sts = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
    k = _hmac(("AWS4" + creds["secretAccessKey"]).encode(), date_str)
    k = _hmac(k, region)
    k = _hmac(k, "kinesisvideo")
    k = _hmac(k, "aws4_request")
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return f"{endpoint}/?{canonical_qs}&X-Amz-Signature={sig}"


async def _resolve_session(client: LymowApiClient, thing: str) -> dict | None:
    session = await client.start_video_session(thing)
    arn, creds = session.get("channelARN"), session.get("credentials")
    region = session.get("region")
    if not (arn and isinstance(creds, dict)):
        print("  no channel/creds — camera offline?")
        return None
    endpoints = await client.get_signaling_channel_endpoint(arn, creds, region=region)
    ice = (
        await client.get_ice_server_config(arn, endpoints["HTTPS"], creds, region=region)
        if endpoints.get("HTTPS")
        else []
    )
    if not endpoints.get("WSS"):
        print("  no WSS endpoint")
        return None
    return {"arn": arn, "creds": creds, "region": region, "wss": endpoints["WSS"], "ice": ice}


async def _view(session: dict) -> bool:
    try:
        import websockets
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
    except ModuleNotFoundError as exc:
        print(
            f"\nMissing WebRTC dependency: {exc.name}. These aren't part of the integration —\n"
            "re-run with them loaded ephemerally (from the repo root):\n\n"
            "  uv run --with aiortc --with websockets python scripts/camera_feed_test.py\n",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    ice_servers = []
    for s in session["ice"]:
        urls = s.get("Uris") or s.get("uris") or []
        ice_servers.append(RTCIceServer(urls=urls, username=s.get("Username"), credential=s.get("Password")))
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")

    got = asyncio.Event()

    @pc.on("track")
    def _on_track(track):  # noqa: ANN001
        if track.kind != "video":
            return

        async def _save():
            for _ in range(30):  # skip a few frames for the encoder to settle
                frame = await track.recv()
            out = os.path.join(os.path.dirname(__file__), "..", "tools", "camera_frame.jpg")
            frame.to_image().save(out)
            print(f"  [PASS] live video frame saved → {out} ({frame.width}x{frame.height})")
            got.set()

        asyncio.ensure_future(_save())

    client_id = f"ha-lymow-{os.getpid()}"
    url = _presign_wss(session["wss"], session["arn"], client_id, session["region"], session["creds"])
    async with websockets.connect(url, max_size=None) as ws:
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await ws.send(
            json.dumps(
                {
                    "action": "SDP_OFFER",
                    "messagePayload": base64.b64encode(
                        json.dumps({"type": "offer", "sdp": pc.localDescription.sdp}).encode()
                    ).decode(),
                }
            )
        )

        async def _signal_loop():
            async for raw in ws:
                msg = json.loads(raw)
                payload = json.loads(base64.b64decode(msg["messagePayload"]).decode())
                if msg.get("action") == "SDP_ANSWER":
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=payload["sdp"], type="answer"))
                elif msg.get("action") == "ICE_CANDIDATE" and payload.get("candidate"):
                    from aiortc.sdp import candidate_from_sdp

                    cand = candidate_from_sdp(payload["candidate"].split(":", 1)[1])
                    cand.sdpMid = payload.get("sdpMid")
                    cand.sdpMLineIndex = payload.get("sdpMLineIndex")
                    await pc.addIceCandidate(cand)

        loop_task = asyncio.ensure_future(_signal_loop())
        try:
            await asyncio.wait_for(got.wait(), timeout=30)
            return True
        except asyncio.TimeoutError:
            print("  [FAIL] no video frame within 30s (check robot online / TURN reachability)")
            return False
        finally:
            loop_task.cancel()
            await pc.close()


async def main() -> int:
    user, pw = os.environ.get("LYMOW_USER"), os.environ.get("LYMOW_PASS")
    if not user or not pw:
        print("Error: set LYMOW_USER and LYMOW_PASS in scripts/.env", file=sys.stderr)
        return 1
    async with aiohttp.ClientSession() as http:
        auth = LymowAuth(http)
        tokens = await auth.login(user, pw)
        cdata = await auth.get_aws_credentials(tokens["IdToken"], tokens["region"])
        aws = cdata["credentials"]
        client = LymowApiClient(http, tokens["AccessToken"], tokens["region"], cdata["identity_id"])
        client.update_aws_credentials(aws["AccessKeyId"], aws["SecretKey"], aws["SessionToken"])
        devices = await client.get_devices()
        things = [d["deviceThingName"] for d in devices if isinstance(d, dict) and "deviceThingName" in d]
        for thing in things:
            print(f"=== {thing} ===")
            session = await _resolve_session(client, thing)
            if session and await _view(session):
                return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
