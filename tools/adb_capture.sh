#!/usr/bin/env bash
# Capture Lymow MQTT traffic via ADB + logcat/tcpdump.
#
# Requires: phone connected via USB with USB debugging enabled.
#
# Usage: bash tools/adb_capture.sh [iot_host]
#   iot_host  AWS IoT endpoint to filter tcpdump on. Defaults to
#             $LYMOW_IOT_HOST, then the eu-west-1 endpoint. Override for
#             other regions (e.g. ...iot.us-east-2.amazonaws.com) without
#             editing this file.
#
# Two capture modes run in parallel:
#   1. logcat — Android app logs (may include decoded payloads if app logs verbosely)
#   2. tcpdump on the phone — raw TCP to/from the AWS IoT host (port 443)
#      Saved to tools/capture-adb.pcap (gitignored), readable with wireshark/tshark.

ADB=$(which adb 2>/dev/null || find "$HOME/android-sdk" "$HOME/Android/Sdk" /opt/android-sdk -name adb -type f 2>/dev/null | head -1)
if [ -z "$ADB" ]; then
    echo "Error: adb not found. Add Android SDK platform-tools to PATH."
    exit 1
fi
IOT_HOST="${1:-${LYMOW_IOT_HOST:-a3j5zqqo5iuph9-ats.iot.eu-west-1.amazonaws.com}}"
OUT_DIR="$(dirname "$0")"
mkdir -p "$OUT_DIR"

echo "=== Waiting for device ==="
$ADB wait-for-device
echo "Device: $($ADB devices | tail -2 | head -1)"

# Clear logcat first
$ADB logcat -c

echo ""
echo "=== Starting logcat (Lymow / MQTT / AWS tags) ==="
$ADB logcat -v time | grep -iE "lymow|mqtt|iot|aws|bitv|message|pbout|map|zone|area|boundary" \
    > "$OUT_DIR/logcat-lymow.txt" &
LOGCAT_PID=$!
echo "  logcat → tools/logcat-lymow.txt (pid $LOGCAT_PID)"

echo ""
echo "=== Checking for tcpdump on device ==="
if $ADB shell which tcpdump > /dev/null 2>&1; then
    echo "  tcpdump found — starting capture of port 443 traffic"
    $ADB shell "tcpdump -i any -w /sdcard/lymow.pcap 'host $IOT_HOST or port 443'" &
    TCPDUMP_PID=$!
    echo "  tcpdump pid $TCPDUMP_PID → /sdcard/lymow.pcap on device"
    HAVE_TCPDUMP=1
else
    echo "  tcpdump not found — logcat only"
    echo "  (install via: adb push /path/to/tcpdump /data/local/tmp/tcpdump && adb shell chmod +x /data/local/tmp/tcpdump)"
    HAVE_TCPDUMP=0
fi

echo ""
echo "=== NOW: Open the Lymow app and navigate to the Map screen ==="
echo "    Press Ctrl-C when done."
echo ""

# Tail logcat live too
$ADB logcat -v time | grep -iE "lymow|mqtt|iot|aws|bitv|message|pbout|map|zone" &

trap 'echo ""; echo "Stopping..."; kill $LOGCAT_PID 2>/dev/null; kill $! 2>/dev/null
if [ "$HAVE_TCPDUMP" = "1" ]; then
    kill $TCPDUMP_PID 2>/dev/null
    sleep 1
    echo "Pulling pcap from device..."
    '"$ADB"' pull /sdcard/lymow.pcap '"$OUT_DIR"'/capture-adb.pcap 2>/dev/null && echo "  Saved to tools/capture-adb.pcap"
fi
echo "Done."' INT TERM

wait
