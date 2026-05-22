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

NOTE: the viewer client id must embed the account owner's Cognito sub as
"…_userId_<sub>" (the app does this; a random id is rejected). Even so, in
testing the robot only acts as the WebRTC MASTER for the live app's own
session and behaves as single-viewer — replaying these captured cloud calls
from a standalone client did not get the robot to answer. The robot streams
fine while docked (confirmed in the app). Treat a timeout here as "the robot
isn't serving this session", not a signaling bug.

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


def _jwt_sub(token: str) -> str:
    """Extract the Cognito 'sub' claim from a JWT (no signature check needed)."""
    payload = token.split(".")[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("sub", "")


async def _resolve_session(client: LymowApiClient, thing: str) -> dict | None:
    session = await client.start_video_session(thing)
    arn, creds = session.get("channelARN"), session.get("credentials")
    region = session.get("region") or client._region  # noqa: SLF001 — fall back if response omits it
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


async def _view(session: dict, client: LymowApiClient, thing: str) -> bool:
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
    print(f"  ICE servers: {len(ice_servers)}")
    pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
    pc.addTransceiver("video", direction="recvonly")
    pc.addTransceiver("audio", direction="recvonly")

    @pc.on("iceconnectionstatechange")
    def _ice():  # noqa: ANN202
        print(f"  ICE state: {pc.iceConnectionState}")

    @pc.on("connectionstatechange")
    def _conn():  # noqa: ANN202
        print(f"  PC state: {pc.connectionState}")

    got = asyncio.Event()

    @pc.on("track")
    def _on_track(track):  # noqa: ANN001
        if track.kind != "video":
            return

        async def _save():
            try:
                for _ in range(30):  # skip a few frames for the encoder to settle
                    frame = await track.recv()
                out = os.path.join(os.path.dirname(__file__), "..", "tools", "camera_frame.jpg")
                frame.to_image().save(out)
                print(f"  [PASS] live video frame saved → {out} ({frame.width}x{frame.height})")
                got.set()
            except Exception as exc:
                print(f"  [FAIL] video track error: {exc}")
                got.set()

        asyncio.create_task(_save())

    # The robot's master only answers viewers whose KVS client id carries the
    # owner's Cognito sub as "…_userId_<sub>" (the app uses this format). A
    # random client id is silently ignored — this is why the offer goes
    # unanswered even with the master up.
    client_id = f"ha-lymow_{os.getpid()}_userId_{session['user_sub']}"
    url = _presign_wss(session["wss"], session["arn"], client_id, session["region"], session["creds"])
    answered = asyncio.Event()
    async with websockets.connect(url, max_size=None) as ws:
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        # aiortc has no trickle ICE — wait for gathering to finish so the
        # offer SDP carries our candidates (the app sends them inline too).
        # Without this the master has nowhere to send media and never connects.
        for _t in range(300):  # up to 30 s; fail fast if STUN is unreachable
            if pc.iceGatheringState == "complete":
                break
            await asyncio.sleep(0.1)
        else:
            print("  [WARN] ICE gathering did not complete in 30 s — sending offer with partial candidates")
        print(f"  WSS connected; ICE gathering {pc.iceGatheringState}, sending SDP_OFFER")
        offer_frame = json.dumps(
            {
                "action": "SDP_OFFER",
                "messagePayload": base64.b64encode(
                    json.dumps({"type": "offer", "sdp": pc.localDescription.sdp}).encode()
                ).decode(),
            }
        )
        await ws.send(offer_frame)

        async def _resend_offer() -> None:
            # KVS does not buffer an offer for an absent master; the robot's
            # camera can take 30-60s to wake and join. Resend the offer (and
            # re-nudge "start") until the master answers.
            for i in range(1, 30):
                await asyncio.sleep(4)
                if answered.is_set():
                    return
                if i % 5 == 0:
                    try:
                        await client.start_video_session(thing)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  re-nudge start failed: {type(exc).__name__}")
                print(f"  no answer yet — resending SDP_OFFER (attempt {i + 1})")
                await ws.send(offer_frame)

        async def _handle_frame(raw, candidate_from_sdp) -> None:
            # KVS interleaves empty (0-byte) keepalive/status frames before the
            # SDP_ANSWER — skip anything that isn't JSON.
            if not raw or not str(raw).strip():
                return
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                print(f"  recv non-JSON frame ({len(raw)}B), skipping")
                return
            # master→viewer uses "messageType"; viewer→master uses "action".
            kind = msg.get("messageType") or msg.get("action")
            raw_payload = msg.get("messagePayload")
            if not raw_payload:
                print(f"  recv {kind} (no payload)")
                return
            try:
                payload = json.loads(base64.b64decode(raw_payload).decode())
            except Exception as exc:  # noqa: BLE001
                print(f"  recv {kind} — malformed payload, skipping ({type(exc).__name__})")
                return
            print(f"  recv {kind}")
            if kind == "SDP_ANSWER":
                answered.set()
                await pc.setRemoteDescription(RTCSessionDescription(sdp=payload["sdp"], type="answer"))
            elif kind == "ICE_CANDIDATE" and payload.get("candidate"):
                cand = candidate_from_sdp(payload["candidate"].split(":", 1)[1])
                cand.sdpMid = payload.get("sdpMid")
                cand.sdpMLineIndex = payload.get("sdpMLineIndex")
                await pc.addIceCandidate(cand)

        async def _signal_loop():
            from aiortc.sdp import candidate_from_sdp

            try:
                async for raw in ws:
                    await _handle_frame(raw, candidate_from_sdp)
                print(f"  signaling socket closed by server (code={ws.close_code})")
            except Exception as exc:  # noqa: BLE001 — surface, don't swallow in the task
                print(f"  signaling loop error: {type(exc).__name__}: {exc}")

        loop_task = asyncio.create_task(_signal_loop())
        resend_task = asyncio.create_task(_resend_offer())
        try:
            await asyncio.wait_for(got.wait(), timeout=120)
            return True
        except asyncio.TimeoutError:
            state = "answered" if answered.is_set() else "no SDP_ANSWER — robot never joined as master"
            print(f"  [FAIL] no video frame within 120s ({state})")
            return False
        finally:
            # Cancel both helper tasks and await them together, swallowing any
            # error they raise during teardown (CancelledError, or a
            # ConnectionClosed from a resend racing the socket close).
            resend_task.cancel()
            loop_task.cancel()
            await asyncio.gather(resend_task, loop_task, return_exceptions=True)
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
        user_sub = _jwt_sub(tokens["IdToken"])
        devices = await client.get_devices()
        things = [d["deviceThingName"] for d in devices if isinstance(d, dict) and "deviceThingName" in d]
        for thing in things:
            print(f"=== {thing} ===")
            session = await _resolve_session(client, thing)
            if session and await _view({**session, "user_sub": user_sub}, client, thing):
                return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
