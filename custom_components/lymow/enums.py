"""Enum mappings extracted from the Lymow app's APK (Hermes bytecode).

The app's protobuf message classes carry ``fromObject`` functions that map
between string enum names and their wire int values. Walking those tables in
the disassembled Hermes bytecode gives us the *exact* set of values each
"int32" field actually accepts — so we can present proper labelled selectors
in HA instead of arbitrary 0-1000 number boxes, and validate that values we
write back are ones the robot would have produced itself.

Each constant is annotated with the APK identifier name (left of ``=``) and
the integer wire value (right). The Python-side dicts pair the wire value
with a human-readable label suitable for selector ``options`` lists.

Provenance: PbTaskConfig.fromObject (fn #9595), PbZoneConfig.fromObject (fn
#9438), PbRobotInfo.fromObject (fn #9739) — Hermes v96 bundle from the
Android app's base.apk.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# PbTaskConfig.chargingMode — Device Settings → Charging Mode (selector)
# ---------------------------------------------------------------------------
CHARGING_MODE_NORMAL: Final = 0
CHARGING_MODE_QUICK: Final = 1

CHARGING_MODE_LABELS: Final[dict[int, str]] = {
    CHARGING_MODE_NORMAL: "Normal",
    CHARGING_MODE_QUICK: "Quick",
}

# ---------------------------------------------------------------------------
# PbTaskConfig.zoneOrder — Device Settings → Zone Order
# ---------------------------------------------------------------------------
ZONE_ORDER_OPTIMIZE: Final = 0
ZONE_ORDER_CUSTOM: Final = 1

ZONE_ORDER_LABELS: Final[dict[int, str]] = {
    ZONE_ORDER_OPTIMIZE: "Optimized (auto)",
    ZONE_ORDER_CUSTOM: "Custom (manual)",
}

# ---------------------------------------------------------------------------
# PbZoneConfig.cleanMode — Mowing pattern
# ---------------------------------------------------------------------------
CLEAN_MODE_NONE: Final = 0
CLEAN_MODE_ZIGZAG: Final = 1
CLEAN_MODE_ADAPTIVE_ZIGZAG: Final = 2
CLEAN_MODE_CHESS_BOARD: Final = 3
CLEAN_MODE_PERIMETER_LAPS_ONLY: Final = 4

CLEAN_MODE_LABELS: Final[dict[int, str]] = {
    CLEAN_MODE_NONE: "None (default)",
    CLEAN_MODE_ZIGZAG: "Zigzag",
    CLEAN_MODE_ADAPTIVE_ZIGZAG: "Adaptive zigzag",
    CLEAN_MODE_CHESS_BOARD: "Chessboard",
    CLEAN_MODE_PERIMETER_LAPS_ONLY: "Perimeter laps only",
}

# ---------------------------------------------------------------------------
# PbZoneConfig.perimeterMowDir — MOW_DIR enum
# (also used by other Mow-Dir-shaped fields)
# ---------------------------------------------------------------------------
MOW_DIR_CLOCKWISE: Final = 0
MOW_DIR_COUNTERCLOCKWISE: Final = 1
MOW_DIR_SHUFFLE: Final = 2

MOW_DIR_LABELS: Final[dict[int, str]] = {
    MOW_DIR_CLOCKWISE: "Clockwise",
    MOW_DIR_COUNTERCLOCKWISE: "Counter-clockwise",
    MOW_DIR_SHUFFLE: "Shuffle",
}

# ---------------------------------------------------------------------------
# PbZoneConfig.obsDecMode / followDetectMode — OBS_DEC_MODE enum
# (Obstacle-detection sensitivity, five levels)
# ---------------------------------------------------------------------------
OBS_DEC_MODE_NONE: Final = 0
OBS_DEC_MODE_TOUCH_ONLY: Final = 1
OBS_DEC_MODE_SMART_HIGH: Final = 2
OBS_DEC_MODE_SMART_MEDIUM: Final = 3
OBS_DEC_MODE_SMART_LOW: Final = 4

OBS_DEC_MODE_LABELS: Final[dict[int, str]] = {
    OBS_DEC_MODE_NONE: "Off",
    OBS_DEC_MODE_TOUCH_ONLY: "Touch only",
    OBS_DEC_MODE_SMART_HIGH: "Smart (high sensitivity)",
    OBS_DEC_MODE_SMART_MEDIUM: "Smart (medium sensitivity)",
    OBS_DEC_MODE_SMART_LOW: "Smart (low sensitivity)",
}

# ---------------------------------------------------------------------------
# PbRobotInfo.robotStatus — what we surface as workStatus in coordinator data.
# Same value layout as the existing WORK_STATUS_* constants in const.py; this
# dict lets selectors and binary-sensor descriptions share one label source.
# Extracted from PbRobotInfo.fromObject (fn #9739 at offset 0x004b8b68).
# ---------------------------------------------------------------------------
ROBOT_STATUS_LABELS: Final[dict[int, str]] = {
    0: "Idle (none)",
    1: "Waiting",
    2: "Mowing",
    3: "Paused",
    4: "Docking",
    5: "Charging",
    6: "Remote control",
    7: "Error",
    8: "Resuming",
    9: "Zone partition",
    10: "Pause docking",
    11: "Updating firmware",
    12: "Charging full",
    13: "Emergency stop",
    14: "Escaping",
    15: "RTT test",
    16: "Aging test",
}
