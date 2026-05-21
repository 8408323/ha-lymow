#!/usr/bin/env python3
"""Decode ATT Write Commands from a BTSnoop CFA file.

Prints the linear / angular float32 values from every frame written to
the Lymow drive characteristic (handle 0x0014).

Usage:
    python3 tools/_decode_turn.py tools/capture_turn_*.cfa
"""

from __future__ import annotations

import base64
import os
import struct
import subprocess
import sys


def _tshark(*args: str) -> str:
    result = subprocess.run(["tshark", *args], capture_output=True, text=True)
    return result.stdout


def decode_cfa(path: str) -> None:
    print(f"File: {path}")
    print(f"Size: {os.path.getsize(path):,} bytes\n")

    # ── All ATT frames (overview) ─────────────────────────────────────────────
    overview = _tshark(
        "-r",
        path,
        "-Y",
        "btatt",
        "-T",
        "fields",
        "-e",
        "frame.number",
        "-e",
        "frame.time_relative",
        "-e",
        "btatt.opcode",
        "-e",
        "btatt.handle",
        "-e",
        "btatt.value",
    )
    print("=== All ATT frames ===")
    for line in overview.strip().splitlines():
        print(" ", line)

    # ── Drive characteristic writes (handle 0x0014, opcode 0x52 or 0x12) ─────
    print("\n=== Drive characteristic writes (handle 0x0014) ===")
    drive_frames: list[tuple[str, str, str]] = []  # (time, linear_s, angular_s)

    raw = _tshark(
        "-r",
        path,
        "-Y",
        "btatt.handle == 0x0014",
        "-T",
        "fields",
        "-e",
        "frame.number",
        "-e",
        "frame.time_relative",
        "-e",
        "btatt.opcode",
        "-e",
        "btatt.value",
    )
    lines = [line for line in raw.strip().splitlines() if line.strip()]
    if not lines:
        print("  (no frames to handle 0x0014 found)")
        # Also try looking for any write commands
        print("\n=== All ATT write opcodes in file ===")
        all_writes = _tshark(
            "-r",
            path,
            "-Y",
            "btatt.opcode == 0x52 or btatt.opcode == 0x12",
            "-T",
            "fields",
            "-e",
            "frame.number",
            "-e",
            "btatt.opcode",
            "-e",
            "btatt.handle",
            "-e",
            "btatt.value",
        )
        for line in all_writes.strip().splitlines():
            print(" ", line)
        return

    for line in lines:
        parts = line.split("\t")
        fnum = parts[0] if len(parts) > 0 else "?"
        ftime = parts[1] if len(parts) > 1 else "?"
        opcode = parts[2] if len(parts) > 2 else "?"
        hexval = parts[3] if len(parts) > 3 else ""

        if not hexval:
            print(f"  frame {fnum:>6} t={ftime}  opcode=0x{opcode}  (no value)")
            continue

        raw_bytes = bytes.fromhex(hexval.replace(":", ""))

        # Value is ASCII base64 of the protobuf. Drive = 24 ASCII bytes
        # (16-byte pb: 1031 3802 52 0a0d <lin f32> 15 <ang f32>);
        # heartbeat = 56 ASCII bytes (42-byte pb 3802da0125 <device-id>).
        linear_s = "?"
        angular_s = "?"
        note = ""
        if len(raw_bytes) == 24:
            try:
                pb = base64.b64decode(raw_bytes)
                if len(pb) >= 16:
                    linear = struct.unpack("<f", pb[7:11])[0]
                    angular = struct.unpack("<f", pb[12:16])[0]
                    linear_s = f"{linear:+.4f}"
                    angular_s = f"{angular:+.4f}"
            except Exception as e:
                note = f"  decode_err={e}"
        elif len(raw_bytes) == 56:
            # Heartbeat / device-ID payload — skip
            note = "  [heartbeat/device-id, skipped]"
        else:
            note = f"  [unexpected len={len(raw_bytes)}, hex={hexval[:40]}]"

        print(
            f"  frame {fnum:>6}  t={ftime:>10}  opcode=0x{opcode}  linear={linear_s:>10}  angular={angular_s:>10}{note}"
        )
        if linear_s != "?" and angular_s != "?":
            drive_frames.append((ftime, linear_s, angular_s))

    if drive_frames:
        print(f"\n  {len(drive_frames)} drive frame(s) decoded.")
        linears = [float(f[1]) for f in drive_frames]
        angulars = [float(f[2]) for f in drive_frames]
        print(f"  linear  range: [{min(linears):+.4f}, {max(linears):+.4f}]")
        print(f"  angular range: [{min(angulars):+.4f}, {max(angulars):+.4f}]")
        # Show unique value pairs
        pairs = sorted({(round(lin, 4), round(ang, 4)) for lin, ang in zip(linears, angulars)})
        print("\n  Unique (linear, angular) pairs sent:")
        for lin, ang in pairs:
            print(f"    linear={lin:+.4f}  angular={ang:+.4f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 _decode_turn.py <file.cfa>", file=sys.stderr)
        sys.exit(1)
    decode_cfa(sys.argv[1])
