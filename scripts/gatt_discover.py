"""GATT service/characteristic discovery via raw L2CAP ATT socket.

Prints the full handle-UUID map for the robot's GATT database so we can
identify the correct CCCD handle (UUID 0x2902) for status notifications.

Usage:
    echo PASSWORD | sudo -S python3 scripts/gatt_discover.py [MAC]
"""

from __future__ import annotations

import ctypes
import os
import pathlib
import select
import socket
import struct
import subprocess
import sys
import time

# ---- BT/L2CAP constants ----
AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0
SOCK_SEQPACKET = 5
SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1
ATT_CID = 4
BDADDR_LE_PUBLIC = 0x01

# ---- ATT opcodes ----
ATT_ERROR_RSP = 0x01
ATT_FIND_INFO_REQ = 0x04
ATT_FIND_INFO_RSP = 0x05
ATT_READ_BY_TYPE_REQ = 0x08
ATT_READ_BY_TYPE_RSP = 0x09
ATT_READ_BY_GROUP_TYPE_REQ = 0x10
ATT_READ_BY_GROUP_TYPE_RSP = 0x11

# ---- Known UUIDs ----
UUID_PRIMARY_SERVICE = 0x2800
UUID_CHARACTERISTIC = 0x2803
UUID_CCCD = 0x2902

# ---- libc for raw connect ----
libc = ctypes.CDLL("libc.so.6", use_errno=True)


class _SockAddrL2(ctypes.Structure):
    _fields_ = [
        ("l2_family", ctypes.c_uint16),
        ("l2_psm", ctypes.c_uint16),
        ("l2_bdaddr", ctypes.c_uint8 * 6),
        ("l2_cid", ctypes.c_uint16),
        ("l2_bdaddr_type", ctypes.c_uint8),
    ]


def _load_dotenv() -> None:
    candidates = [
        pathlib.Path(__file__).parent / ".env",
        pathlib.Path(__file__).parent.parent / ".env",
    ]
    for p in candidates:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        break


def _mac_to_bytes(mac: str) -> bytes:
    return bytes(reversed(bytes.fromhex(mac.replace(":", ""))))


def _run(cmd: str, *, ignore_errors: bool = False) -> None:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0 and not ignore_errors:
        print(f"[warn] {cmd!r}: {r.stderr.strip()}", file=sys.stderr)


def _connect_le_att(mac: str, timeout: float = 30.0) -> socket.socket:
    mac_bytes = _mac_to_bytes(mac)
    sock = socket.socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)
    sec = struct.pack("BB", BT_SECURITY_LOW, 0)
    sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, sec)

    local = _SockAddrL2(
        l2_family=AF_BLUETOOTH,
        l2_psm=0,
        l2_bdaddr=(ctypes.c_uint8 * 6)(0, 0, 0, 0, 0, 0),
        l2_cid=ATT_CID,
        l2_bdaddr_type=BDADDR_LE_PUBLIC,
    )
    ret = libc.bind(ctypes.c_int(sock.fileno()), ctypes.byref(local), ctypes.c_int(ctypes.sizeof(local)))
    if ret != 0:
        err = ctypes.get_errno()
        sock.close()
        raise OSError(err, f"bind() failed: {os.strerror(err)}")

    sock.setblocking(False)

    peer = _SockAddrL2(
        l2_family=AF_BLUETOOTH,
        l2_psm=0,
        l2_bdaddr=(ctypes.c_uint8 * 6)(*mac_bytes),
        l2_cid=ATT_CID,
        l2_bdaddr_type=BDADDR_LE_PUBLIC,
    )
    ret = libc.connect(ctypes.c_int(sock.fileno()), ctypes.byref(peer), ctypes.c_int(ctypes.sizeof(peer)))
    err = ctypes.get_errno()
    if ret != 0 and err != 115:
        sock.close()
        raise OSError(err, f"connect() failed: {os.strerror(err)}")

    print(f"Connecting to {mac}…", flush=True)
    start = time.monotonic()
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            sock.close()
            raise TimeoutError(f"LE connection timed out after {timeout:.0f}s")
        _, writable, _ = select.select([], [sock], [sock], 1.0)
        if writable:
            so_err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if so_err == 0:
                sock.setblocking(True)
                print(f"Connected in {elapsed:.1f}s")
                return sock
            sock.close()
            raise OSError(so_err, f"LE connection failed: {os.strerror(so_err)}")


def _att_request(sock: socket.socket, pdu: bytes, timeout: float = 3.0) -> bytes:
    """Send ATT PDU and receive response (with timeout)."""
    sock.settimeout(timeout)
    sock.send(pdu)
    try:
        return sock.recv(512)
    except socket.timeout:
        return b""
    finally:
        sock.settimeout(None)


def discover_primary_services(sock: socket.socket) -> list[tuple[int, int, bytes]]:
    """Return list of (start_handle, end_handle, service_uuid_bytes)."""
    services = []
    start = 0x0001
    while start <= 0xFFFF:
        req = struct.pack("<BHHH", ATT_READ_BY_GROUP_TYPE_REQ, start, 0xFFFF, UUID_PRIMARY_SERVICE)
        resp = _att_request(sock, req)
        if not resp or resp[0] == ATT_ERROR_RSP:
            break
        if resp[0] != ATT_READ_BY_GROUP_TYPE_RSP:
            print(f"  Unexpected opcode 0x{resp[0]:02x} for READ_BY_GROUP_TYPE_RSP")
            break
        length = resp[1]  # attribute data length per entry
        data = resp[2:]
        for i in range(0, len(data), length):
            chunk = data[i : i + length]
            if len(chunk) < 4:
                break
            s, e = struct.unpack_from("<HH", chunk, 0)
            uuid_bytes = chunk[4:]
            services.append((s, e, uuid_bytes))
            start = e + 1
        if start > 0xFFFF:
            break
    return services


def discover_characteristics(sock: socket.socket, start: int, end: int) -> list[tuple[int, int, int, bytes]]:
    """Return list of (decl_handle, properties, value_handle, char_uuid_bytes)."""
    chars = []
    cur = start
    while cur <= end:
        req = struct.pack("<BHHH", ATT_READ_BY_TYPE_REQ, cur, end, UUID_CHARACTERISTIC)
        resp = _att_request(sock, req)
        if not resp or resp[0] == ATT_ERROR_RSP:
            break
        if resp[0] != ATT_READ_BY_TYPE_RSP:
            break
        length = resp[1]
        data = resp[2:]
        for i in range(0, len(data), length):
            chunk = data[i : i + length]
            if len(chunk) < 5:
                break
            decl_h = struct.unpack_from("<H", chunk, 0)[0]
            props = chunk[2]
            val_h = struct.unpack_from("<H", chunk, 3)[0]
            uuid_b = chunk[5:]
            chars.append((decl_h, props, val_h, uuid_b))
            cur = val_h + 1
        else:
            break
    return chars


def discover_descriptors(sock: socket.socket, start: int, end: int) -> list[tuple[int, int]]:
    """Return list of (handle, uuid_16bit) for all 16-bit descriptors in range."""
    descriptors = []
    cur = start
    while cur <= end:
        req = struct.pack("<BHH", ATT_FIND_INFO_REQ, cur, end)
        resp = _att_request(sock, req)
        if not resp or resp[0] == ATT_ERROR_RSP:
            break
        if resp[0] != ATT_FIND_INFO_RSP:
            break
        fmt = resp[1]
        data = resp[2:]
        if fmt == 0x01:  # 16-bit UUIDs
            for i in range(0, len(data), 4):
                if len(data) < i + 4:
                    break
                h = struct.unpack_from("<H", data, i)[0]
                u = struct.unpack_from("<H", data, i + 2)[0]
                descriptors.append((h, u))
                cur = h + 1
        elif fmt == 0x02:  # 128-bit UUIDs
            for i in range(0, len(data), 18):
                if len(data) < i + 18:
                    break
                h = struct.unpack_from("<H", data, i)[0]
                descriptors.append((h, 0))
                cur = h + 1
        else:
            break
    return descriptors


def _prop_str(props: int) -> str:
    names = {0x01: "Bcast", 0x02: "R", 0x04: "WwR", 0x08: "W", 0x10: "N", 0x20: "I", 0x40: "Auth", 0x80: "Ext"}
    return "|".join(v for k, v in names.items() if props & k) or "?"


def _uuid_str(uuid_bytes: bytes) -> str:
    if len(uuid_bytes) == 2:
        u = struct.unpack("<H", uuid_bytes)[0]
        known = {
            0x1800: "Generic Access",
            0x1801: "Generic Attribute",
            0x2800: "Primary Service",
            0x2803: "Characteristic",
            0x2902: "CCCD",
            0x2900: "Char Ext Props",
            0x2901: "User Description",
            0x2904: "Char Presentation Format",
        }
        return known.get(u, f"0x{u:04x}")
    elif len(uuid_bytes) == 16:
        # Display as standard UUID string
        b = uuid_bytes[::-1]  # reverse to big-endian
        return f"{b[0:4].hex()}-{b[4:6].hex()}-{b[6:8].hex()}-{b[8:10].hex()}-{b[10:16].hex()}"
    return uuid_bytes.hex()


def main() -> None:
    if os.geteuid() != 0:
        print("ERROR: Must run as root (sudo).", file=sys.stderr)
        sys.exit(1)

    _load_dotenv()
    mac = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LYMOW_BLE_MAC", "")
    if not mac:
        print("ERROR: No MAC. Set LYMOW_BLE_MAC in .env or pass as arg.", file=sys.stderr)
        sys.exit(1)

    print(f"Target: {mac}")
    _run("systemctl stop bluetooth", ignore_errors=True)
    time.sleep(0.5)
    _run("hciconfig hci0 up", ignore_errors=True)
    time.sleep(0.3)

    sock = None
    try:
        sock = _connect_le_att(mac)

        # --- Primary Services ---
        print("\n=== Primary Services ===")
        services = discover_primary_services(sock)
        if not services:
            print("  (none found; trying raw handle scan instead)")
        for s, e, uuid_b in services:
            print(f"  Service h=0x{s:04x}..0x{e:04x}  UUID={_uuid_str(uuid_b)}")

        # If no services discovered (robot may not support READ_BY_GROUP_TYPE),
        # fall back to FIND_INFORMATION over full range
        if not services:
            print("\n=== FIND_INFORMATION scan (h=0x0001..0x00FF) ===")
            req = struct.pack("<BHH", ATT_FIND_INFO_REQ, 0x0001, 0x00FF)
            resp = _att_request(sock, req)
            if resp and resp[0] == ATT_FIND_INFO_RSP:
                fmt = resp[1]
                data = resp[2:]
                if fmt == 0x01:
                    for i in range(0, len(data), 4):
                        if len(data) < i + 4:
                            break
                        h = struct.unpack_from("<H", data, i)[0]
                        u = struct.unpack_from("<H", data, i + 2)[0]
                        label = ""
                        if u == UUID_CCCD:
                            label = " ← CCCD (write 0x0001 here to enable notifications)"
                        print(f"  h=0x{h:04x}  UUID=0x{u:04x} ({_uuid_str(struct.pack('<H', u))}){label}")
            else:
                raw = resp.hex() if resp else "(empty)"
                print(f"  FIND_INFORMATION failed: {raw}")
            return

        # --- Characteristics per service ---
        cccd_handles = []
        for svc_start, svc_end, svc_uuid in services:
            print(f"\n  Service 0x{svc_start:04x}..0x{svc_end:04x}:")
            chars = discover_characteristics(sock, svc_start, svc_end)
            for idx, (decl_h, props, val_h, uuid_b) in enumerate(chars):
                # Descriptor range = val_h+1 .. next_decl-1 (or svc_end)
                if idx + 1 < len(chars):
                    desc_end = chars[idx + 1][0] - 1
                else:
                    desc_end = svc_end
                props_str = _prop_str(props)
                print(
                    f"    Char decl h=0x{decl_h:04x}  val h=0x{val_h:04x}  props={props_str}  UUID={_uuid_str(uuid_b)}"
                )
                # Descriptors
                if val_h < desc_end:
                    descs = discover_descriptors(sock, val_h + 1, desc_end)
                    for dh, du in descs:
                        label = ""
                        if du == UUID_CCCD:
                            label = " ← CCCD"
                            cccd_handles.append(dh)
                        print(f"      Desc  h=0x{dh:04x}  UUID=0x{du:04x} ({_uuid_str(struct.pack('<H', du))}){label}")

        if cccd_handles:
            print(f"\n=== CCCD handle(s) found: {[hex(h) for h in cccd_handles]} ===")
            print(f"    → Set CCCD_HANDLE = 0x{cccd_handles[0]:04x} in raw_ble_drive.py")
        else:
            print("\n=== No CCCD found via structured discovery ===")
            print("    → Try FIND_INFORMATION fallback scan")

    finally:
        if sock:
            sock.close()
        _run("systemctl start bluetooth", ignore_errors=True)
        time.sleep(1.0)
        print("BlueZ restarted.")


if __name__ == "__main__":
    main()
