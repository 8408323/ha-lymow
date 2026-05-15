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


addons = [LymowCapture()]
