#!/usr/bin/env bash
# Capture the BLE ATT bytes the Lymow app sends during a joystick turn.
#
# Usage:
#   bash tools/adb_capture_turn.sh [phone_ip] [adb_port]
#     phone_ip  phone's LAN IP for ADB-over-WiFi. Defaults to
#               $LYMOW_PHONE_IP, then 192.168.1.101 — set to your phone's IP.
#     adb_port  ADB-over-WiFi port. Defaults to $LYMOW_PHONE_PORT, then 5555.
#   No file edits needed; pass as args or export the env vars.
#
# Steps:
#   1. Connects ADB over WiFi
#   2. Clears the existing HCI btsnoop log on the phone
#   3. Prompts you to open the Lymow app joystick and do ONE pure turn
#   4. Pulls the snoop log
#   5. Decodes every ATT Write Command (opcode 0x52) to handle 0x0014
#      and prints the linear / angular float32 values

set -euo pipefail

PHONE_IP="${1:-${LYMOW_PHONE_IP:-192.168.1.101}}"
PHONE_PORT="${2:-${LYMOW_PHONE_PORT:-5555}}"
SNOOP_PATH="/data/misc/bluetooth/logs/btsnoop_hci.log"
OUT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_CFA="$OUT_DIR/capture_turn_$(date +%Y%m%d_%H%M%S).cfa"
DECODE_PY="$(cd "$(dirname "$0")" && pwd)/_decode_turn.py"

# ── ADB ──────────────────────────────────────────────────────────────────────
ADB=$(command -v adb 2>/dev/null || true)
if [[ -z "$ADB" ]]; then
    echo "Error: adb not found in PATH." >&2; exit 1
fi

echo "==> Connecting to phone at $PHONE_IP:$PHONE_PORT …"
"$ADB" connect "$PHONE_IP:$PHONE_PORT" 2>&1 | grep -v "already connected" || true
"$ADB" wait-for-device

echo "==> Device: $("$ADB" devices | grep "$PHONE_IP" | head -1)"

# ── Clear existing snoop log ──────────────────────────────────────────────────
echo "==> Clearing existing btsnoop log on phone …"
# Truncate (requires root/shell write permission on most Androids with BT debug)
"$ADB" shell "su -c 'echo > $SNOOP_PATH'" 2>/dev/null \
    || "$ADB" shell "echo > $SNOOP_PATH" 2>/dev/null \
    || echo "  (Could not clear — will capture from current position; newer frames will still appear at end)"

# ── User action ───────────────────────────────────────────────────────────────
echo ""
echo "┌─────────────────────────────────────────────────────────────────────┐"
echo "│  NOW: Open the Lymow app → joystick screen                         │"
echo "│  Do ONE slow pure turn (left or right) for ~3 seconds then release │"
echo "│  Then press ENTER here to pull the log.                            │"
echo "└─────────────────────────────────────────────────────────────────────┘"
read -r -p "Press ENTER when done turning … "

# ── Pull log ──────────────────────────────────────────────────────────────────
echo "==> Pulling snoop log → $OUT_CFA"
mkdir -p "$OUT_DIR"
"$ADB" pull "$SNOOP_PATH" "$OUT_CFA" || {
    echo "ERROR: Could not pull $SNOOP_PATH" >&2
    echo "  Try: adb shell su -c 'cp $SNOOP_PATH /sdcard/btsnoop.cfa' && adb pull /sdcard/btsnoop.cfa $OUT_CFA"
    exit 1
}

# ── Decode ────────────────────────────────────────────────────────────────────
echo ""
echo "==> Decoding ATT Write Commands to drive characteristic (handle 0x0014) …"
python3 "$DECODE_PY" "$OUT_CFA"
