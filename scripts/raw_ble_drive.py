"""Drive the Lymow robot via a raw L2CAP ATT socket, bypassing BlueZ.

ROOT CAUSE of bleak TimeoutError
---------------------------------
The robot (TD5322A_V3.1.2BLE) advertises with Flags=0x02 (LE General
Discoverable Mode) which is missing bit 2 (BR/EDR Not Supported = 0x04).
BlueZ therefore classifies the device as potentially dual-mode and issues a
BR/EDR *Create Connection* (HCI opcode 0x01|0x0005) instead of an
*LE Create Connection* (0x08|0x000D).  The BR/EDR page times out (~5 s),
BlueZ never retries over LE, and BleakClient raises TimeoutError.

Fix
---
Open a raw L2CAP SEQPACKET socket with the ATT fixed channel (CID 4) and
*explicitly* mark the peer address type as BDADDR_LE_PUBLIC (2).  The kernel
then issues LE Create Connection, completely bypassing BlueZ's BR/EDR
classification logic.

Requirements
------------
- Run as root  (sudo) to stop/start bluetoothd and access HCI sockets.
- The `hci0` adapter must be available.

Usage
-----
    echo PASSWORD | sudo -S uv run python scripts/raw_ble_drive.py
    # or
    sudo python3 scripts/raw_ble_drive.py [MAC]
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import pathlib
import select
import socket
import struct
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Bluetooth / L2CAP constants (from <bluetooth/l2cap.h>)
# ---------------------------------------------------------------------------
AF_BLUETOOTH = 31  # sa_family for Bluetooth sockets
BTPROTO_L2CAP = 0  # L2CAP protocol
SOCK_SEQPACKET = 5  # connection-oriented, message-based

SOL_BLUETOOTH = 274  # socket-level option for BT
BT_SECURITY = 4  # set connection security level
BT_SECURITY_LOW = 1  # no authentication / encryption required

ATT_CID = 4  # fixed L2CAP channel for BLE ATT protocol

# bdaddr_type values for sockaddr_l2.l2_bdaddr_type  (linux/bluetooth.h)
# NOTE: These differ from HCI address-type field values!
BDADDR_BREDR = 0x00
BDADDR_LE_PUBLIC = 0x01  # peer advertises with public address
BDADDR_LE_RANDOM = 0x02  # peer advertises with random address

# ATT PDU opcodes
ATT_WRITE_CMD = 0x52  # Write Command       (no response)
ATT_WRITE_REQ = 0x12  # Write Request       (expects Write Response 0x13)
ATT_WRITE_RSP = 0x13  # Write Response      (confirms ATT_WRITE_REQ)
ATT_READ_REQ = 0x0A  # Read Request        (from robot reading phone GATT server)
ATT_READ_RSP = 0x0B  # Read Response       (our reply to robot's Read Request)
ATT_HANDLE_VAL_NOTIF = 0x1B  # Handle Value Notification (robot status updates)

# ATT handles (robot GATT database)
#
# Confirmed via GATT discovery (scripts/gatt_discover.py):
#   Service 0x0012..0x0016  UUID=12345678-...-56789abcdef0 (proprietary)
#     h=0x0012  Service Declaration          (read-only; Write Not Permitted)
#     h=0x0013  Characteristic Declaration
#     h=0x0014  Drive/Status char value      props=Read|WriteNoRsp|Notify
#     h=0x0015  CCCD for h=0x0014            (write 0x0001 to subscribe)
#     h=0x0016  User Description
#
# The same characteristic (0x0014) is used bidirectionally:
#   • We WRITE drive commands to it (ATT Write Command, opcode 0x52)
#   • Robot sends status/feedback notifications FROM it (Handle Value Notif)
#
# h=0x0011 is the CCCD for Battery Level (service 0x000e..0x0011, UUID 0x180F) —
# NOT a robot status characteristic.
DRIVE_HANDLE = 0x0014  # drive commands written here; robot status notified here
CCCD_HANDLE = 0x0015  # CCCD for DRIVE_HANDLE — write 0x0001 to enable notifications
STATUS_HANDLE = DRIVE_HANDLE  # notifications arrive on the drive char itself

# ---------------------------------------------------------------------------
# Motion parameters
# ---------------------------------------------------------------------------
DRIVE_VEL: float = 0.3  # linear velocity  (max ±0.5; positive = forward)
TURN_VEL: float = (
    0.546  # angular velocity — measured app plateau (ADB swipe: right≈-0.546, left≈+0.545); theoretical max ±0.6
)
DRIVE_SECS: float = 0.5  # seconds to drive backward / forward
TURN_SECS: float = 10.0  # seconds for arc turns
HZ: int = 10  # command send rate (Hz)

# (DRIVE_HANDLE is defined above with CCCD_HANDLE and STATUS_HANDLE)

# ---------------------------------------------------------------------------
# Thread-safety for shared socket
# ---------------------------------------------------------------------------
_send_lock = threading.Lock()

# ---------------------------------------------------------------------------
# libc for raw connect() with full sockaddr_l2 struct
# ---------------------------------------------------------------------------
libc = ctypes.CDLL("libc.so.6", use_errno=True)


class _SockAddrL2(ctypes.Structure):
    """struct sockaddr_l2 (linux/bluetooth/l2cap.h).

    Using a ctypes.Structure avoids the c_char_p null-termination trap that
    silently truncates the sockaddr when PSM (bytes 2-3) is 0x0000.
    """

    _fields_ = [
        ("l2_family", ctypes.c_uint16),  # AF_BLUETOOTH = 31
        ("l2_psm", ctypes.c_uint16),  # 0 when using fixed CID
        ("l2_bdaddr", ctypes.c_uint8 * 6),  # peer addr (little-endian)
        ("l2_cid", ctypes.c_uint16),  # ATT_CID = 4
        ("l2_bdaddr_type", ctypes.c_uint8),  # BDADDR_LE_PUBLIC/RANDOM
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Load scripts/.env (or parent .env) into os.environ."""
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


def _load_protocol():
    """Load lymow.protocol from the custom_components source tree."""
    repo_root = pathlib.Path(__file__).parent.parent
    for name, path in (
        ("lymow.const", repo_root / "custom_components" / "lymow" / "const.py"),
        ("lymow.protocol", repo_root / "custom_components" / "lymow" / "protocol.py"),
    ):
        spec = importlib.util.spec_from_file_location(name, str(path))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules["lymow.protocol"]


def _mac_to_bytes(mac: str) -> bytes:
    """Convert 'AA:BB:CC:DD:EE:FF' → 6 bytes, little-endian (reversed for BT)."""
    return bytes(reversed(bytes.fromhex(mac.replace(":", ""))))


def _run(cmd: str, *, ignore_errors: bool = False) -> None:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0 and not ignore_errors:
        print(f"[warn] {cmd!r}: {r.stderr.strip()}", file=sys.stderr)


# ---------------------------------------------------------------------------
# LE connection via raw L2CAP ATT socket
# ---------------------------------------------------------------------------


def _connect_le_att(mac: str, timeout: float = 45.0) -> socket.socket:
    """Return a connected L2CAP ATT socket over LE to *mac* (public address).

    Uses ctypes to call libc.connect() directly with a fully populated
    sockaddr_l2 struct including l2_bdaddr_type=BDADDR_LE_PUBLIC.  This
    makes the kernel issue LE Create Connection (opcode 0x200D) regardless
    of how BlueZ has classified the device.

    struct sockaddr_l2 layout (linux/bluetooth/l2cap.h):
        sa_family_t  l2_family      2 B  (= AF_BLUETOOTH = 31)
        __le16       l2_psm         2 B  (= 0 when using fixed CID)
        bdaddr_t     l2_bdaddr      6 B  (peer address, little-endian)
        __le16       l2_cid         2 B  (= ATT_CID = 4)
        __u8         l2_bdaddr_type 1 B  (= BDADDR_LE_PUBLIC = 2)
    """
    mac_bytes = _mac_to_bytes(mac)

    sock = socket.socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)

    # No authentication / encryption required for drive writes
    sec = struct.pack("BB", BT_SECURITY_LOW, 0)
    sock.setsockopt(SOL_BLUETOOTH, BT_SECURITY, sec)

    # BIND the socket to local any-address with l2_cid=ATT_CID.
    # This is *required* before connecting with a fixed CID: the kernel's
    # l2cap_add_scid() call in bind() changes chan->chan_type from
    # L2CAP_CHAN_CONN_ORIENTED to L2CAP_CHAN_FIXED.  Without this bind,
    # connect() with l2_cid!=0 returns EINVAL.
    local = _SockAddrL2(
        l2_family=AF_BLUETOOTH,
        l2_psm=0,
        l2_bdaddr=(ctypes.c_uint8 * 6)(0, 0, 0, 0, 0, 0),  # any
        l2_cid=ATT_CID,
        l2_bdaddr_type=BDADDR_LE_PUBLIC,
    )
    ret = libc.bind(
        ctypes.c_int(sock.fileno()),
        ctypes.byref(local),
        ctypes.c_int(ctypes.sizeof(local)),
    )
    err = ctypes.get_errno()
    if ret != 0:
        sock.close()
        raise OSError(err, f"bind() failed: {os.strerror(err)}")

    # Non-blocking so select() can be used for timeout
    sock.setblocking(False)

    # Build peer sockaddr_l2 using ctypes.Structure (avoids c_char_p
    # null-truncation which would silently truncate at the first 0x00 byte).
    peer = _SockAddrL2(
        l2_family=AF_BLUETOOTH,
        l2_psm=0,
        l2_bdaddr=(ctypes.c_uint8 * 6)(*mac_bytes),
        l2_cid=ATT_CID,
        l2_bdaddr_type=BDADDR_LE_PUBLIC,  # robot uses public address type
    )

    # Initiate connection via libc.connect() with raw sockaddr_l2
    ret = libc.connect(
        ctypes.c_int(sock.fileno()),
        ctypes.byref(peer),
        ctypes.c_int(ctypes.sizeof(peer)),
    )
    err = ctypes.get_errno()
    if ret != 0 and err != 115:  # 115 = EINPROGRESS (expected for non-blocking)
        sock.close()
        raise OSError(err, f"connect() failed: {os.strerror(err)}")

    # Wait for connection using select()
    print(f"  Waiting for LE connection to {mac} (up to {timeout:.0f}s)…", flush=True)
    start = time.monotonic()
    last_dot = 0.0

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            sock.close()
            raise TimeoutError(f"LE connection timed out after {timeout:.0f}s")

        # Print a progress dot every 5 s
        if elapsed - last_dot >= 5.0:
            print(f"  …{elapsed:.0f}s", flush=True)
            last_dot = elapsed

        _, writable, exceptional = select.select([], [sock], [sock], 1.0)
        if writable or exceptional:
            so_err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if so_err == 0:
                sock.setblocking(True)
                print(f"  Connected in {elapsed:.1f}s!")
                return sock
            sock.close()
            raise OSError(so_err, f"LE connection failed: {os.strerror(so_err)}")


# ---------------------------------------------------------------------------
# ATT helpers
# ---------------------------------------------------------------------------


def _att_write_cmd(sock: socket.socket, handle: int, data: bytes) -> None:
    """Send ATT Write Command (opcode 0x52) — no response expected."""
    with _send_lock:
        sock.send(struct.pack("<BH", ATT_WRITE_CMD, handle) + data)


def _att_read_rsp(sock: socket.socket, value: bytes = b"") -> None:
    """Reply to an ATT Read Request with a Read Response.

    BTSnoop shows the robot sends READ_REQ to the phone's GATT server at
    handles 0x0600 and 0x1000 after CCCD is enabled.  The ATT spec requires
    a READ_RSP or ERROR_RSP; if we send nothing the server-side timeout
    stalls the ATT bearer and subsequent drive commands are never processed.
    We respond with empty data; the robot appears to accept this and proceeds.
    """
    with _send_lock:
        sock.send(bytes([ATT_READ_RSP]) + value)


def _robot_receiver(
    sock: socket.socket,
    stop_event: threading.Event,
    status_log: list,
) -> None:
    """Background thread: drain incoming ATT PDUs from the robot.

    - ATT Read Request  → reply with empty Read Response so the robot's ATT
      state machine advances (otherwise it stalls waiting for our answer).
    - Handle Value Notification on STATUS_HANDLE → log the 2-byte status
      value so we can see when the robot enters drive mode.
    - Everything else → silently ignored.
    """
    while not stop_event.is_set():
        readable, _, _ = select.select([sock], [], [], 0.1)
        if not readable:
            continue
        try:
            pkt = sock.recv(128)
        except OSError:
            break
        if not pkt:
            break
        op = pkt[0]
        if op == ATT_READ_REQ and len(pkt) >= 3:
            handle = struct.unpack("<H", pkt[1:3])[0]
            print(f"  [recv] Robot READ_REQ h=0x{handle:04x} → READ_RSP (empty)", flush=True)
            _att_read_rsp(sock)
        elif op == ATT_HANDLE_VAL_NOTIF and len(pkt) >= 3:
            handle = struct.unpack("<H", pkt[1:3])[0]
            val_hex = pkt[3:].hex()
            print(f"  [recv] Notification h=0x{handle:04x} val=0x{val_hex}", flush=True)
            if handle == STATUS_HANDLE:
                status_log.append(pkt[3:])
        # ATT_WRITE_RSP, ATT_ERROR_RSP etc. are already handled by _enable_cccd;
        # ignore any late-arriving ones here.


def _exchange_mtu(sock: socket.socket, mtu: int = 512) -> None:
    """Negotiate a larger ATT MTU (ATT Exchange MTU Request, opcode 0x02).

    CRITICAL: the default ATT MTU is 23, which caps a Write Command's value at
    MTU-3 = 20 bytes.  The drive command value is 24 bytes (base64 of the 16-byte
    protobuf), so on the default MTU the robot truncates it to 20 bytes — which
    preserves the linear float (offset 7-10) but corrupts the *angular* float
    (offset 12-15).  Result: linear drive works, but rotation only jerks.
    The app negotiates MTU 512; doing the same here makes angular/rotation work.
    """
    old_timeout = sock.gettimeout()
    sock.settimeout(3.0)
    try:
        with _send_lock:
            sock.send(struct.pack("<BH", 0x02, mtu))  # ATT_EXCHANGE_MTU_REQ
        resp = sock.recv(64)
        if resp and len(resp) >= 3 and resp[0] == 0x03:  # ATT_EXCHANGE_MTU_RSP
            server_mtu = struct.unpack_from("<H", resp, 1)[0]
            print(f"  ATT MTU negotiated: client={mtu} server={server_mtu} -> {min(mtu, server_mtu)}")
        else:
            print(f"  MTU exchange unexpected response: {resp.hex() if resp else 'empty'} — continuing.")
    except socket.timeout:
        print("  MTU exchange timed out — continuing (angular/rotation may not work).")
    finally:
        sock.settimeout(old_timeout)


def _enable_cccd(sock: socket.socket) -> None:
    """Subscribe to notifications from the drive/status characteristic (CCCD at 0x0015).

    GATT discovery (scripts/gatt_discover.py) confirmed:
      h=0x0014  Drive char  props=Read|WriteNoRsp|Notify  (UUID 12345678-...)
      h=0x0015  CCCD for h=0x0014

    Writing 0x0001 here tells the robot to send Handle Value Notifications on
    h=0x0014.  Without this the robot stays in a limited state and angular
    commands may have no effect.

    Previous attempts used h=0x0012 (Service Declaration = read-only → error 0x03)
    and before that h=0x0015 was believed absent (BTSnoop inference was wrong).
    """
    req = struct.pack("<BH", ATT_WRITE_REQ, CCCD_HANDLE) + b"\x01\x00"
    sock.settimeout(5.0)
    try:
        with _send_lock:
            sock.send(req)
        resp = sock.recv(64)
        if resp and resp[0] == ATT_WRITE_RSP:
            print(f"  CCCD enabled on handle 0x{CCCD_HANDLE:04x} — robot will send status notifications.")
        elif resp and resp[0] == 0x01:  # ATT Error Response
            err_code = resp[4] if len(resp) >= 5 else 0xFF
            print(f"  CCCD write error ATT 0x{err_code:02x} on h=0x{CCCD_HANDLE:04x} — continuing anyway.")
        else:
            print(f"  CCCD unexpected response: {resp.hex() if resp else 'empty'} — continuing.")
    except socket.timeout:
        print("  CCCD write timed out — continuing anyway.")
    finally:
        sock.settimeout(None)


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------


def _drive(
    sock: socket.socket,
    encode_fn,
    linear: float,
    angular: float,
    secs: float,
    label: str,
) -> None:
    print(f"  {label:<12} linear={linear:+.1f}  angular={angular:+.1f}  for {secs:.1f}s …")
    payload = encode_fn(linear, angular)
    interval = 1.0 / HZ
    deadline = time.monotonic() + secs
    while time.monotonic() < deadline:
        _att_write_cmd(sock, DRIVE_HANDLE, payload)
        time.sleep(interval)


def _stop(sock: socket.socket, encode_fn) -> None:
    _att_write_cmd(sock, DRIVE_HANDLE, encode_fn(0.0, 0.0))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if os.geteuid() != 0:
        print(
            "ERROR: This script must be run as root.\n"
            "  sudo python3 scripts/raw_ble_drive.py\n"
            "  — or —\n"
            "  echo PASSWORD | sudo -S uv run python scripts/raw_ble_drive.py",
            file=sys.stderr,
        )
        sys.exit(1)

    _load_dotenv()
    proto = _load_protocol()
    encode = proto.encode_ble_drive

    mac = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("LYMOW_BLE_MAC", "")
    if not mac:
        print(
            "ERROR: robot MAC address required.\n  Set LYMOW_BLE_MAC in scripts/.env or pass as argument.",
            file=sys.stderr,
        )
        sys.exit(1)
    mac = mac.upper()

    print(f"Target robot: {mac}")
    print("Stopping BlueZ daemon (prevents BR/EDR interference)…")
    _run("systemctl stop bluetooth")
    time.sleep(1.0)

    # Bring hci0 up if needed, but do NOT cycle it down first.
    # BlueZ initialises LE event masks (HCI_LE_ENABLED) when it runs; if we
    # reset the adapter with `hciconfig hci0 down` that state is lost and
    # l2cap_connect() returns ENOSYS.  Just ensure the adapter is UP so the
    # kernel's already-initialised LE state is preserved.
    _run("hciconfig hci0 up")
    time.sleep(0.5)

    # Countdown
    print("Press Ctrl-C NOW to abort before the robot moves.")
    for remaining in range(3, 0, -1):
        print(f"  Starting in {remaining} …")
        time.sleep(1)

    sock = None
    stop_event = threading.Event()
    status_log: list = []
    receiver_thread = None

    try:
        sock = _connect_le_att(mac, timeout=45.0)

        # Negotiate a large ATT MTU FIRST — without this the 24-byte drive value is
        # truncated to 20 bytes on the default MTU=23, corrupting the angular float
        # (rotation fails while linear still works).
        _exchange_mtu(sock, 512)

        # Subscribe to status notifications.  This MUST happen before starting
        # the receiver thread (the receiver's settimeout(0.1) would compete with
        # _enable_cccd's settimeout(5.0) for the WRITE_RSP packet).
        _enable_cccd(sock)

        # Start background receiver.  It handles:
        #   • ATT Read Requests from the robot (reads our "phone GATT server") →
        #     responds with empty READ_RSP so the robot's ATT state machine can
        #     proceed.  Without this the robot stalls and angular commands are
        #     effectively ignored.
        #   • Handle Value Notifications on STATUS_HANDLE (0x0011) → logged so
        #     we can monitor the robot entering drive mode.
        receiver_thread = threading.Thread(
            target=_robot_receiver,
            args=(sock, stop_event, status_log),
            daemon=True,
        )
        receiver_thread.start()

        # Give the robot time to send its READ_REQs and receive our READ_RSPs
        # (BTSnoop shows robot sends ×3 READ_REQs shortly after CCCD write).
        print("  Waiting 3 s for robot ATT handshake…")
        time.sleep(3.0)
        if status_log:
            last_status = status_log[-1].hex()
            print(f"  Robot status: 0x{last_status} (last of {len(status_log)} notifications)")

        # TUNING HISTORY:
        #   CCCD was incorrectly written to 0x0015 (returns ATT error 0x0a).
        #   Robot also sends READ_REQ to phone GATT server at handles 0x0600 and
        #   0x1000; without responding those the ATT bearer stalls → angular ≈ 0.
        #   Fix: CCCD to 0x0012, receiver thread replies to READ_REQs.
        #
        #   Steps below: first confirm angular works (pure spin), then re-add
        #   linear once angular is confirmed.
        # TURN_VEL=0.546 is the measured app plateau (ADB swipe to screen edge;
        # right joystick max = -0.546, left joystick max = +0.545).
        steps = [
            ("Spin right", 0.0, -TURN_VEL, TURN_SECS),  # pure CW spin — test angular first
            ("Spin left", 0.0, +TURN_VEL, TURN_SECS),  # pure CCW spin
            ("Backward", -DRIVE_VEL, 0.0, DRIVE_SECS),
            ("Forward", +DRIVE_VEL, 0.0, DRIVE_SECS),
        ]

        for label, lin, ang, secs in steps:
            _drive(sock, encode, lin, ang, secs, label)
            _stop(sock, encode)
            time.sleep(0.3)

        print("Done — robot stopped.")

    except KeyboardInterrupt:
        print("\nAborted.")
        if sock:
            try:
                _stop(sock, encode)
            except Exception:
                pass

    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    finally:
        stop_event.set()
        if receiver_thread is not None:
            receiver_thread.join(timeout=1.0)
        if sock:
            sock.close()
        print("Restarting BlueZ daemon…")
        _run("systemctl start bluetooth", ignore_errors=True)
        time.sleep(1.0)
        print("BlueZ restarted.")


if __name__ == "__main__":
    main()
