"""Watch a capture file for pboutput frames — flag novel protobuf fields and track mow progress.

A reverse-engineering aid: tail ``tools/capture-lymow.txt`` (the gitignored ADB/mitmproxy
capture), decode each ``pb bytes hex:`` frame's top-level fields, and print any field number
we don't already decode (``KNOWN_FIELDS``) plus the live mow progress. Handy for spotting a
new field the moment the robot emits it (e.g. a rain / lift state), and for knowing when a
mow is about to finish so you can catch the end-of-mow cleanReport.

Usage: ``uv run python scripts/_mow_monitor.py`` while a capture is being written.
"""

from __future__ import annotations

import re
import struct
import time

CAPTURE_PATH = "tools/capture-lymow.txt"
# Top-level PbOutput field numbers we already decode (the heartbeat set); anything
# outside this is worth a look.
KNOWN_FIELDS = {2, 3, 4, 5, 6, 9, 10, 12, 14, 15, 16, 17, 18, 21, 22, 23, 32, 34}
_HEX_RE = re.compile(r"pb bytes hex: ([0-9a-f]{30,})")
_POLL_SECONDS = 60
_MAX_LOOPS = 120  # ~2 h


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, i


def _top_level_fields(buf: bytes) -> dict[int, tuple]:
    """Return {field_number: (wire_kind, ...)} for the message's top-level fields."""
    i = 0
    out: dict[int, tuple] = {}
    try:
        while i < len(buf):
            tag, i = _read_varint(buf, i)
            field, wire = tag >> 3, tag & 7
            if wire == 0:
                value, i = _read_varint(buf, i)
                out.setdefault(field, ("varint", value))
            elif wire == 2:
                length, i = _read_varint(buf, i)
                out.setdefault(field, ("len", length, buf[i : i + length]))
                i += length
            elif wire == 5:
                out.setdefault(field, ("fixed32",))
                i += 4
            elif wire == 1:
                i += 8
            else:
                break
    except (IndexError, ValueError):
        pass  # truncated/garbled frame — return what we parsed
    return out


def _mow_progress(fields: dict[int, tuple]) -> float | None:
    """Mow progress % from PbCleanInfo (field 12, sub-field 5 = float 0..1)."""
    if 12 not in fields or fields[12][0] != "len":
        return None
    sub = fields[12][2]
    i = 0
    try:
        while i < len(sub):
            tag, i = _read_varint(sub, i)
            field, wire = tag >> 3, tag & 7
            if wire == 5:
                value = struct.unpack("<f", sub[i : i + 4])[0]
                i += 4
                if field == 5:
                    return round(value * 100, 1)
            elif wire == 0:
                _, i = _read_varint(sub, i)
            elif wire == 2:
                length, i = _read_varint(sub, i)
                i += length
            else:
                break
    except (IndexError, ValueError, struct.error):
        pass
    return None


def main() -> None:
    seen: set[str] = set()
    max_progress = 0.0
    start = time.time()
    for loop in range(_MAX_LOOPS):
        try:
            with open(CAPTURE_PATH, errors="ignore") as fh:
                lines = fh.read().splitlines()
        except OSError:
            time.sleep(_POLL_SECONDS)
            continue
        for line in lines[-400:]:  # only the tail — captures grow large
            match = _HEX_RE.search(line)
            if not match:
                continue
            hex_frame = match.group(1)
            if hex_frame in seen:
                continue
            seen.add(hex_frame)
            fields = _top_level_fields(bytes.fromhex(hex_frame))
            novel = sorted(f for f in fields if f not in KNOWN_FIELDS)
            if novel:
                print(f"NOVEL fields={sorted(fields)} novel={novel}\n  hex={hex_frame}", flush=True)
            progress = _mow_progress(fields)
            if progress is not None:
                max_progress = max(max_progress, progress)
        print(f"[loop {loop}] maxProgress={max_progress}% elapsed={int(time.time() - start)}s", flush=True)
        if max_progress >= 98:
            print("MOW NEAR COMPLETE (>=98%) — watch for cleanReport now", flush=True)
            break
        time.sleep(_POLL_SECONDS)
    print("monitor done", flush=True)


if __name__ == "__main__":
    main()
