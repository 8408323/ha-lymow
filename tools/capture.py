"""mitmproxy addon — capture Lymow app traffic and dump to capture-lymow.txt.

Usage:
    mitmdump -s tools/capture.py --listen-port 8080 --ssl-insecure

The phone must be configured to use this machine (192.168.1.147:8080) as its
HTTP proxy, and the mitmproxy CA certificate must be installed on the phone.

All requests/responses matching known Lymow hosts are written to
tools/capture-lymow.txt in a human-readable format.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime

from mitmproxy import http

# Hosts we care about
LYMOW_HOSTS = (
    "amazonaws.com",
    "execute-api",
    "cognito-idp",
    "cognito-identity",
    "iot.",
    "bitv.is",
    "lymow",
    "eiotclub",  # 3rd-party SIM provider; called by Network Settings
)

OUT = os.path.join(os.path.dirname(__file__), "capture-lymow.txt")


def _is_lymow(flow: http.HTTPFlow) -> bool:
    host = flow.request.pretty_host
    return any(h in host for h in LYMOW_HOSTS)


def _ts() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]


def _write(text: str) -> None:
    with open(OUT, "a") as f:
        f.write(text + "\n")
    print(text, flush=True)


def _pretty_body(content: bytes, content_type: str) -> str:
    if not content:
        return "  (empty)"
    # Try JSON
    if "json" in content_type or content[:1] in (b"{", b"["):
        try:
            return "  " + json.dumps(json.loads(content), indent=2).replace("\n", "\n  ")
        except Exception:
            pass
    # Try base64 protobuf envelope
    try:
        obj = json.loads(content)
        for key in ("message", "value", "data", "payload"):
            if key in obj:
                pb = base64.b64decode(obj[key])
                return f"  JSON envelope key={key!r}, pb={len(pb)} bytes\n  pb hex: {pb.hex()}"
    except Exception:
        pass
    # Raw
    if len(content) <= 512:
        return f"  raw ({len(content)}B): {content.hex()}"
    return f"  binary ({len(content)}B): {content[:64].hex()}..."


def _decode_mqtt_publish(buf: bytes) -> tuple[str, bytes] | None:
    """Best-effort MQTT 3.1.1 PUBLISH parser. Returns (topic, app_payload) or None.

    Bounds the returned payload by the parsed Remaining Length, so a
    WebSocket binary frame that contains multiple coalesced MQTT control
    packets only yields the *first* packet's payload — not bytes from
    subsequent packets that happen to share the frame.
    """
    if len(buf) < 2 or buf[0] >> 4 != 3:  # PUBLISH = type 3
        return None
    pos = 1
    multiplier = 1
    rem_len = 0
    for _ in range(4):
        if pos >= len(buf):
            return None
        b = buf[pos]
        pos += 1
        rem_len += (b & 0x7F) * multiplier
        if not (b & 0x80):
            break
        multiplier *= 128
    # End of *this* PUBLISH packet (anything beyond is a separate packet).
    packet_end = pos + rem_len
    if packet_end > len(buf):
        # Truncated frame — refuse to guess where the packet ends.
        return None
    if pos + 2 > packet_end:
        return None
    topic_len = (buf[pos] << 8) | buf[pos + 1]
    pos += 2
    if pos + topic_len > packet_end:
        return None
    topic = buf[pos : pos + topic_len].decode("utf-8", errors="replace")
    pos += topic_len
    qos = (buf[0] >> 1) & 0x03
    if qos > 0:
        if pos + 2 > packet_end:
            return None
        pos += 2  # packet identifier
    return topic, buf[pos:packet_end]


def _pretty_mqtt_payload(body: bytes) -> str:
    if not body:
        return "  (empty)"
    try:
        obj = json.loads(body)
        out = "  " + json.dumps(obj, indent=2).replace("\n", "\n  ")
        if isinstance(obj, dict):
            for key in ("message", "value", "data", "payload"):
                if isinstance(obj.get(key), str):
                    try:
                        pb = base64.b64decode(obj[key])
                        out += f"\n  → {key}: {len(pb)} pb bytes hex: {pb.hex()}"
                    except Exception:
                        pass
        return out
    except Exception:
        return f"  raw ({len(body)}B): {body.hex()[:512]}"


class LymowCapture:
    def request(self, flow: http.HTTPFlow) -> None:
        if not _is_lymow(flow):
            return
        req = flow.request
        ct = req.headers.get("content-type", "")
        body = _pretty_body(req.content or b"", ct)
        target = req.headers.get("X-Amz-Target", "")
        lines = [
            f"\n{'=' * 70}",
            f"[{_ts()}] REQUEST  {req.method} {req.pretty_url}",
        ]
        if target:
            lines.append(f"  X-Amz-Target: {target}")
        # Show auth header type without leaking the token
        auth = req.headers.get("Authorization", "")
        if auth:
            lines.append(f"  Authorization: {auth[:40]}...")
        if req.content:
            lines.append(f"  Body:\n{body}")
        _write("\n".join(lines))

    def response(self, flow: http.HTTPFlow) -> None:
        if not _is_lymow(flow):
            return
        resp = flow.response
        ct = resp.headers.get("content-type", "")
        body = _pretty_body(resp.content or b"", ct)
        lines = [
            f"[{_ts()}] RESPONSE {resp.status_code} ← {flow.request.pretty_url}",
            f"  Body:\n{body}",
        ]
        _write("\n".join(lines))

    def websocket_message(self, flow: http.HTTPFlow) -> None:
        """Log every MQTT-over-WebSocket frame on the iot.<region>.amazonaws.com host."""
        if "iot." not in flow.request.pretty_host:
            return
        if not flow.websocket or not flow.websocket.messages:
            return
        msg = flow.websocket.messages[-1]
        if not msg.is_text and isinstance(msg.content, bytes):
            parsed = _decode_mqtt_publish(msg.content)
            if parsed:
                topic, body = parsed
                arrow = "→" if msg.from_client else "←"
                _write(f"\n[{_ts()}] MQTT {arrow} {topic} ({len(body)}B)\n{_pretty_mqtt_payload(body)}")
                return
            ctrl = msg.content[0] >> 4 if msg.content else 0
            ctrl_name = {1: "CONNECT", 2: "CONNACK", 8: "SUB", 9: "SUBACK", 12: "PINGREQ", 13: "PINGRESP"}.get(ctrl, f"type{ctrl}")
            if ctrl_name not in ("PINGREQ", "PINGRESP"):
                arrow = "→" if msg.from_client else "←"
                _write(f"[{_ts()}] MQTT {arrow} {ctrl_name} ({len(msg.content)}B)")


addons = [LymowCapture()]
