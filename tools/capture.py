"""mitmproxy addon — capture Lymow app traffic and dump to capture-lymow.txt.

Usage:
    mitmdump -s tools/capture.py --listen-port 8080 --ssl-insecure

The phone must be configured to use this machine (e.g. 192.168.1.100:8080) as its
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


def _parse_mqtt_packets(buf: bytes) -> tuple[list[tuple[int, int, bytes, int]], int]:
    """Parse complete MQTT packets from the front of a byte stream.

    MQTT-over-WebSocket chunks the MQTT byte stream across WS messages
    arbitrarily, so a single packet (e.g. a large command PUBLISH + config) can
    span message boundaries while several small packets share one message. This
    parses every *complete* packet from the front and reports how many bytes were
    consumed, so the caller can carry the unparsed remainder into the next message.

    Returns ``(packets, consumed)`` where each packet is
    ``(ctrl_type, qos, variable, packet_len)``.
    """
    packets: list[tuple[int, int, bytes, int]] = []
    pos, n = 0, len(buf)
    while pos + 2 <= n:
        ctrl = buf[pos] >> 4
        qos = (buf[pos] >> 1) & 0x03
        p = pos + 1
        multiplier = 1
        rem_len = 0
        complete = False
        for _ in range(4):
            if p >= n:
                break  # length field itself is incomplete — wait for more bytes
            b = buf[p]
            p += 1
            rem_len += (b & 0x7F) * multiplier
            if not (b & 0x80):
                complete = True
                break
            multiplier *= 128
        if not complete:
            if p - pos > 4:
                # Continuation bit still set after the 4th Remaining Length byte:
                # the field is malformed per MQTT spec — skip past it and resync.
                pos = p
            break
        packet_end = p + rem_len
        if packet_end > n:
            break  # packet continues in a later WS message — keep remainder
        packets.append((ctrl, qos, buf[p:packet_end], packet_end - pos))
        pos = packet_end
    return packets, pos


def _decode_publish(qos: int, var: bytes) -> tuple[str, bytes] | None:
    """Parse topic + application payload from a single PUBLISH packet's variable header."""
    if len(var) < 2:
        return None
    topic_len = (var[0] << 8) | var[1]
    pos = 2 + topic_len
    if pos > len(var):
        return None
    topic = var[2 : 2 + topic_len].decode("utf-8", errors="replace")
    if qos > 0:
        pos += 2  # packet identifier
        if pos > len(var):
            return None
    return topic, var[pos:]


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


_CTRL_NAMES = {1: "CONNECT", 2: "CONNACK", 4: "PUBACK", 8: "SUB", 9: "SUBACK", 12: "PINGREQ", 13: "PINGRESP"}

# Cap on per-direction MQTT reassembly tail. A real PUBLISH carrying map data is
# only ~tens of KB, so anything past this is a corrupt/hostile Remaining Length
# stalling the parser — drop the tail and resync rather than buffer forever.
_WS_BUF_MAX = 1 << 20  # 1 MiB


class LymowCapture:
    def __init__(self) -> None:
        # Per-flow count of WS messages already logged, so we process every new
        # message (mitmproxy may deliver several between handler invocations)
        # instead of only the latest.
        self._ws_seen: dict[str, int] = {}
        # Per (flow, direction) MQTT byte-stream remainder: an MQTT packet can
        # span multiple WS messages, so we carry the unparsed tail forward.
        self._ws_buf: dict[tuple[str, bool], bytearray] = {}

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
        """Log MQTT-over-WS frames (iot.*) and KVS WebRTC signaling (kinesisvideo).

        Processes every message that arrived since the last call (not just the
        latest) and every MQTT packet coalesced within each binary frame, so
        outbound command PUBLISHes sharing a frame with other packets aren't lost.
        """
        if not flow.websocket or not flow.websocket.messages:
            return
        host = flow.request.pretty_host
        seen = self._ws_seen.get(flow.id, 0)
        messages = flow.websocket.messages
        for msg in messages[seen:]:
            arrow = "→" if msg.from_client else "←"
            if "kinesisvideo" in host:
                body = msg.text if msg.is_text else (msg.content.decode("utf-8", "replace") if msg.content else "")
                _write(f"\n[{_ts()}] KVS-WSS {arrow} ({len(body)}B)\n  {body[:1400]}")
                continue
            if "iot." not in host or msg.is_text or not isinstance(msg.content, bytes):
                continue
            # Append to the per-direction byte stream, parse complete packets,
            # and keep any partial packet for the next message.
            key = (flow.id, msg.from_client)
            buf = self._ws_buf.setdefault(key, bytearray())
            buf += msg.content
            packets, consumed = _parse_mqtt_packets(bytes(buf))
            del buf[:consumed]
            if len(buf) > _WS_BUF_MAX:
                _write(f"[{_ts()}] MQTT {arrow} (dropped {len(buf)}B unparsed tail — resyncing)")
                buf.clear()
            for ctrl, qos, var, packet_len in packets:
                if ctrl == 3:  # PUBLISH
                    parsed = _decode_publish(qos, var)
                    if parsed:
                        topic, body = parsed
                        _write(f"\n[{_ts()}] MQTT {arrow} {topic} ({len(body)}B)\n{_pretty_mqtt_payload(body)}")
                    continue
                name = _CTRL_NAMES.get(ctrl, f"type{ctrl}")
                if name not in ("PINGREQ", "PINGRESP"):
                    _write(f"[{_ts()}] MQTT {arrow} {name} ({packet_len}B)")
        self._ws_seen[flow.id] = len(messages)

    def websocket_end(self, flow: http.HTTPFlow) -> None:
        """Drop the per-flow cursors/buffers so they don't grow unbounded."""
        self._ws_seen.pop(flow.id, None)
        self._ws_buf.pop((flow.id, True), None)
        self._ws_buf.pop((flow.id, False), None)


addons = [LymowCapture()]
