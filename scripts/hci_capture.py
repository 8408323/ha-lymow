#!/usr/bin/env python3
"""
Read raw HCI events using a SOCK_RAW/BTPROTO_HCI socket to capture
the exact advertising PDU type and any connection attempt events for
the Lymow robot.

Reads LYMOW_BLE_MAC from scripts/.env (e.g. LYMOW_BLE_MAC=AA:BB:CC:DD:EE:FF).

Must run as root: sudo python3 scripts/hci_capture.py

Captures for 30 seconds while printing advertising events for the target MAC.
Also shows any LE Connection Complete events during a connect attempt.
"""

import os
import socket
import struct
import sys
import time


def _load_dotenv() -> None:
    candidates = [
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        break


_load_dotenv()

_mac_env = os.environ.get("LYMOW_BLE_MAC", "")
if not _mac_env:
    print("Error: LYMOW_BLE_MAC not set in scripts/.env", file=sys.stderr)
    sys.exit(1)
TARGET_MAC = _mac_env.upper()

# HCI socket constants
SOL_HCI = 0
HCI_FILTER = 2
HCI_EVENT_PKT = 0x04
HCI_LE_META_EVENT = 0x3E
HCI_LE_ADVERTISING_REPORT = 0x02
HCI_LE_EXTENDED_ADVERTISING_REPORT = 0x0D
HCI_LE_CONNECTION_COMPLETE = 0x01
HCI_LE_ENHANCED_CONNECTION_COMPLETE = 0x0A

# ADV PDU types
ADV_PDU = {
    0x00: "ADV_IND (connectable undirected)",
    0x01: "ADV_DIRECT_IND (connectable directed)",
    0x02: "ADV_SCAN_IND (scannable undirected, non-connectable)",
    0x03: "ADV_NONCONN_IND (non-connectable)",
    0x04: "SCAN_RSP",
}


def mac_bytes_to_str(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in reversed(b))


def parse_advertising_report(data: bytes) -> list[dict]:
    """Parse a standard LE Advertising Report sub-event."""
    reports = []
    if len(data) < 1:
        return reports
    num_reports = data[0]
    pos = 1
    for _ in range(num_reports):
        if pos + 9 > len(data):
            break
        evt_type = data[pos]  # ADV PDU type
        addr_type = data[pos + 1]  # 0=public, 1=random
        addr = mac_bytes_to_str(data[pos + 2 : pos + 8])
        data_len = data[pos + 8]
        adv_data = data[pos + 9 : pos + 9 + data_len]
        pos += 9 + data_len
        rssi = struct.unpack_from("b", data, pos)[0]
        pos += 1
        reports.append(
            {
                "type": evt_type,
                "type_str": ADV_PDU.get(evt_type, f"UNKNOWN(0x{evt_type:02x})"),
                "addr_type": addr_type,
                "addr": addr,
                "rssi": rssi,
                "data_len": data_len,
                "data": adv_data,
            }
        )
    return reports


def parse_ext_advertising_report(data: bytes) -> list[dict]:
    """Parse LE Extended Advertising Report sub-event."""
    reports = []
    if len(data) < 1:
        return reports
    num_reports = data[0]
    pos = 1
    for _ in range(num_reports):
        if pos + 24 > len(data):
            break
        evt_type = struct.unpack_from("<H", data, pos)[0]
        # Bit 0: Connectable, Bit 1: Scannable, Bit 2: Directed
        # Bit 4: Use legacy PDUs
        connectable = bool(evt_type & 0x0001)
        scannable = bool(evt_type & 0x0002)
        directed = bool(evt_type & 0x0004)
        legacy = bool(evt_type & 0x0010)

        addr_type = data[pos + 2]
        addr = mac_bytes_to_str(data[pos + 3 : pos + 9])
        data[pos + 9]
        data[pos + 10]
        data[pos + 11]
        struct.unpack_from("b", data, pos + 12)[0]
        rssi = struct.unpack_from("b", data, pos + 13)[0]
        struct.unpack_from("<H", data, pos + 14)[0]
        data[pos + 16]
        direct_addr = mac_bytes_to_str(data[pos + 17 : pos + 23])
        data_len = data[pos + 23]
        adv_data = data[pos + 24 : pos + 24 + data_len]
        pos += 24 + data_len

        pdu_str = f"ext evt_type=0x{evt_type:04x} conn={connectable} scan={scannable} dir={directed} legacy={legacy}"
        reports.append(
            {
                "type": evt_type,
                "type_str": pdu_str,
                "addr_type": addr_type,
                "addr": addr,
                "rssi": rssi,
                "connectable": connectable,
                "directed": directed,
                "direct_addr": direct_addr if directed else None,
                "data": adv_data,
            }
        )
    return reports


def open_hci_socket(dev_id: int = 0) -> socket.socket:
    """Open a raw HCI socket for device hci<dev_id>."""
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    sock.bind((dev_id,))

    # Set HCI filter: struct hci_filter { type_mask u32; event_mask [2]u32; opcode u16 }
    # type_mask: bit 4 = HCI_EVENT_PKT
    # event_mask: LE_META_EVENT is event 0x3e (62), so it sits in event_mask2 bit 30 (62-32)
    type_mask = 1 << HCI_EVENT_PKT  # bit 4
    event_mask1 = 0xFFFFFFFF  # allow all events in low 32
    event_mask2 = 0xFFFFFFFF  # allow all events in high 32
    opcode = 0
    hci_filter = struct.pack("<IIIh2x", type_mask, event_mask1, event_mask2, opcode)
    sock.setsockopt(SOL_HCI, HCI_FILTER, hci_filter)
    sock.setblocking(False)
    return sock


def main():
    if sys.platform != "linux":
        print("This script only works on Linux.")
        sys.exit(1)

    target = TARGET_MAC.upper()
    print(f"Listening for HCI events... looking for {target}")
    print("Also shows all connection complete events (to detect if connect attempt is seen by robot)")
    print("Run for 15 seconds. During this time, also run the BLE connect test in another terminal.")
    print("-" * 72)

    try:
        sock = open_hci_socket(0)
    except PermissionError:
        print("ERROR: Need root. Run as: sudo python3 scripts/hci_capture.py")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR opening HCI socket: {e}")
        sys.exit(1)

    start = time.monotonic()
    target_seen = set()

    while time.monotonic() - start < 30:
        try:
            data = sock.recv(512)
        except BlockingIOError:
            time.sleep(0.005)
            continue
        except Exception as e:
            print(f"recv error: {e}")
            break

        if len(data) < 3:
            continue

        pkt_type = data[0]
        if pkt_type != HCI_EVENT_PKT:
            continue

        evt_code = data[1]
        # plen = data[2]
        payload = data[3:]

        if evt_code != HCI_LE_META_EVENT:
            continue

        if len(payload) < 1:
            continue

        sub_evt = payload[0]
        sub_data = payload[1:]

        ts = time.strftime("%H:%M:%S")

        if sub_evt == HCI_LE_ADVERTISING_REPORT:
            reports = parse_advertising_report(sub_data)
            for r in reports:
                if r["addr"] == target:
                    key = (r["type"], r["addr"])
                    if key not in target_seen:
                        target_seen.add(key)
                        print(
                            f"[{ts}] TARGET FOUND: {r['addr']} PDU={r['type_str']} rssi={r['rssi']} addr_type={r['addr_type']}"
                        )
                        print(f"         adv_data={r['data'].hex()}")

        elif sub_evt == HCI_LE_EXTENDED_ADVERTISING_REPORT:
            reports = parse_ext_advertising_report(sub_data)
            for r in reports:
                if r["addr"] == target:
                    key = (r["type"], r["addr"])
                    if key not in target_seen:
                        target_seen.add(key)
                        print(f"[{ts}] TARGET FOUND (ext): {r['addr']} {r['type_str']} rssi={r['rssi']}")
                        if r.get("directed") and r.get("direct_addr"):
                            print(f"         DIRECTED to: {r['direct_addr']}")
                        print(f"         adv_data={r['data'].hex()}")

        elif sub_evt == HCI_LE_CONNECTION_COMPLETE:
            # struct: status(1), handle(2), role(1), addr_type(1), addr(6), interval(2), latency(2), timeout(2), master_clk_accuracy(1)
            if len(sub_data) >= 11:
                status = sub_data[0]
                handle = struct.unpack_from("<H", sub_data, 1)[0]
                role = sub_data[3]
                sub_data[4]
                addr = mac_bytes_to_str(sub_data[5:11])
                status_str = "SUCCESS" if status == 0 else f"ERROR(0x{status:02x})"
                print(
                    f"[{ts}] LE CONNECTION COMPLETE: status={status_str} handle=0x{handle:04x} addr={addr} role={role}"
                )

        elif sub_evt == HCI_LE_ENHANCED_CONNECTION_COMPLETE:
            if len(sub_data) >= 30:
                status = sub_data[0]
                handle = struct.unpack_from("<H", sub_data, 1)[0]
                addr = mac_bytes_to_str(sub_data[5:11])
                status_str = "SUCCESS" if status == 0 else f"ERROR(0x{status:02x})"
                print(f"[{ts}] LE ENHANCED CONNECTION COMPLETE: status={status_str} handle=0x{handle:04x} addr={addr}")

    sock.close()
    if target_seen:
        print(f"\nSummary: saw {TARGET_MAC} with {len(target_seen)} distinct PDU type(s)")
    else:
        print(f"\nSummary: {TARGET_MAC} was NOT seen in 30 seconds (is it advertising?)")


if __name__ == "__main__":
    main()
