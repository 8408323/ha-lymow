"""Tiny BTSnoop parser that walks every record and prints ATT WRITE_CMD to handle 0x0014.

Tshark stops on the first malformed packet; this just skips them.
"""

from __future__ import annotations

import base64
import struct
import sys
from datetime import datetime, timezone

BTSNOOP_HEADER = b"btsnoop\x00\x00\x00\x00\x01"
SNOOP_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


def parse(path: str, after: datetime | None = None, before: datetime | None = None) -> None:
    data = open(path, "rb").read()
    if not data.startswith(BTSNOOP_HEADER[:8]):
        print(f"unexpected header: {data[:16].hex()}")
    # btsnoop header: 8-byte magic + 4-byte version + 4-byte datalink = 16 total
    datalink = struct.unpack(">I", data[12:16])[0]
    off = 16
    print(f"datalink={datalink}, records start at offset {off}")
    # Standard btsnoop epoch offset (Unix epoch - btsnoop epoch in seconds)
    OFFSET_S = 62_168_256_000

    n_records = 0
    n_att_writes = 0
    while off + 24 <= len(data):
        try:
            orig_len, incl_len, flags, drops, ts_usec_hi, ts_usec_lo = struct.unpack(">IIIIII", data[off : off + 24])
        except struct.error:
            break
        off += 24
        if incl_len > len(data) - off or incl_len > 2048:
            # malformed; try resyncing — skip 1 byte and retry
            off -= 23
            continue
        pkt = data[off : off + incl_len]
        off += incl_len
        n_records += 1

        ts_usec = (ts_usec_hi << 32) | ts_usec_lo
        # Apply phone-specific epoch offset → Unix usec
        unix_us = ts_usec - OFFSET_S * 1_000_000
        ts = datetime.fromtimestamp(unix_us / 1_000_000, tz=timezone.utc)

        if after and ts < after:
            continue
        if before and ts > before:
            continue

        # H4: type byte, then HCI ACL header (4 bytes), then L2CAP (4 bytes), then ATT
        # Direction: flags bit0 == 0 → sent (host→controller), == 1 → received
        # We want app→robot writes which are direction=sent (flag bit0=0).
        # Type byte 0x02 = ACL data
        if len(pkt) < 1 + 4 + 4 + 1:
            continue
        # On Android snoop: no H4 type byte — packet starts with HCI ACL header (4B)
        # then L2CAP (4B) then ATT.
        # Try with H4 first; if doesn't look right, fall back to no-H4.
        hci_off = 0
        # heuristic: first byte is 0x02 (ACL) or 0x01/0x04 (Cmd/Event) → has H4 byte
        if pkt[0] in (0x01, 0x02, 0x04):
            hci_off = 1
        if len(pkt) < hci_off + 4 + 4 + 1:
            continue
        # HCI ACL: 4 bytes; L2CAP: 4 bytes (length + cid)
        att_off = hci_off + 4 + 4
        att_op = pkt[att_off]
        if att_op != 0x52:  # WRITE_CMD
            continue
        if len(pkt) < att_off + 1 + 2:
            continue
        handle = struct.unpack("<H", pkt[att_off + 1 : att_off + 3])[0]
        if handle != 0x0014:
            continue
        value = pkt[att_off + 3 :]
        # Sent direction only (flag bit0 == 0)
        if flags & 1:
            continue  # incoming
        # Decode b64 ASCII → raw protobuf
        try:
            ascii_b64 = value.decode("ascii")
            raw = base64.b64decode(ascii_b64)
        except Exception:
            ascii_b64 = repr(value[:30])
            raw = b""
        n_att_writes += 1
        print(f"  {ts.strftime('%H:%M:%S.%f')[:-3]}  ascii={ascii_b64:<60s}  pb_hex={raw.hex()}")

    print(f"\nparsed {n_records} records, {n_att_writes} ATT WRITE_CMDs to handle 0x14 (sent)")


if __name__ == "__main__":
    path = sys.argv[1]
    after = datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
    before = datetime.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
    parse(path, after, before)
