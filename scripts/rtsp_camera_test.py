"""Grab one frame from the robot's local RTSP camera — proves the feed works.

The robot serves its onboard camera as a local RTSP h264 stream on the LAN:

    rtsp://<robot_ip>:10022/h264ESVideoTest        (640x480 h264)

This is the path Home Assistant uses (see custom_components/lymow/camera.py).
Unlike the AWS KVS WebRTC flow (the app's *remote* path), the local RTSP feed
is reachable by any client on the same network — no cloud session needed.

Usage (needs ffmpeg on PATH and the HA host on the robot's LAN):

    python scripts/rtsp_camera_test.py 192.168.1.85
    # or let it read the IP from a recent capture / pass --url

Writes the frame to tools/camera_frame.jpg (gitignored).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _load_const() -> None:
    """Expose RTSP_PORT / RTSP_PATH from the integration without importing HA."""
    import importlib.util

    path = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow", "const.py")
    spec = importlib.util.spec_from_file_location("lymow_const", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lymow_const"] = mod
    spec.loader.exec_module(mod)


def main() -> int:
    _load_const()
    from lymow_const import RTSP_PATH, RTSP_PORT  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Grab a frame from the robot's local RTSP camera.")
    parser.add_argument("ip", nargs="?", help="robot LAN IP (e.g. 192.168.1.85)")
    parser.add_argument("--url", help="full RTSP URL (overrides ip)")
    args = parser.parse_args()

    if args.url:
        url = args.url
    elif args.ip:
        url = f"rtsp://{args.ip}:{RTSP_PORT}/{RTSP_PATH}"
    else:
        print("Error: pass the robot IP, e.g. python scripts/rtsp_camera_test.py 192.168.1.85", file=sys.stderr)
        return 1

    out = os.path.join(os.path.dirname(__file__), "..", "tools", "camera_frame.jpg")
    print(f"  grabbing one frame from {url}")
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["ffmpeg", "-rtsp_transport", "tcp", "-y", "-i", url, "-frames:v", "1", "-q:v", "2", out],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if proc.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
        print(f"  [PASS] live frame saved → {out} ({os.path.getsize(out)} bytes)")
        return 0
    print("  [FAIL] no frame — is the robot on this LAN and powered on?")
    print(proc.stderr.strip()[-400:], file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
