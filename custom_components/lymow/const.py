from __future__ import annotations

DOMAIN = "lymow"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_REGION = "region"

REGION_AUTO = "auto"
REGION_CHOICES = [REGION_AUTO, "eu-west-1", "us-east-2", "ap-southeast-2", "ap-east-1"]

# How often to poll REST device state (MQTT keeps live state between polls)
POLLING_INTERVAL = 30  # seconds

# The robot exposes its onboard camera as a local RTSP h264 stream (640x480)
# on the LAN. Confirmed by capture + a live frame pull from the device:
#   rtsp://<robot_ip>:10022/h264ESVideoTest
# (The AWS KVS WebRTC path the app uses is for *remote* viewing.)
RTSP_PORT = 10022
RTSP_PATH = "h264ESVideoTest"

# Per-region AWS configuration — all values extracted from traffic capture and APK analysis
REGION_CONFIG: dict[str, dict[str, str | None]] = {
    "eu-west-1": {
        "client_id": "3h1sqv3hishjiofbv8giskjgb0",
        "user_pool_id": "eu-west-1_6qNPbnrrd",
        "identity_pool_id": "eu-west-1:c905a69c-0153-401a-a879-0c50b892015b",
        "iot_host": "a3j5zqqo5iuph9-ats.iot.eu-west-1.amazonaws.com",
        "api_device_list": "asjqh5wbtj",
        "api_device_info": "6ghz1zkccg",
        "api_ota_check": "eigc6a2ds9",
        "api_ota_job": "io4nsakkt8",  # from APK strings; create-ota-job + get-ota-job-summary
        "api_map": "3q1zxz98l2",
        "api_user_account": "l3hazobjk0",
        "api_kvs": "frgai1jfwg",  # confirmed from live capture 2026-05-19
        "s3_bucket": None,  # not yet confirmed from capture
    },
    "us-east-2": {
        "client_id": None,  # not yet confirmed from capture
        "user_pool_id": None,
        "identity_pool_id": "us-east-2:037db699-5df0-4ed2-92b8-0dd0f1843918",
        "iot_host": "a3j5zqqo5iuph9-ats.iot.us-east-2.amazonaws.com",
        "api_device_list": "453ahng0z4",
        "api_device_info": "xuw7gtx113",
        "api_ota_check": "6at3p6r6ce",
        "api_ota_job": "tvdfyh81d1",  # from APK strings; us-east-2 OTA job gateway
        "api_map": "suk4e76xe5",
        "api_user_account": "6r8m5rxeth",
        "api_kvs": "xuw7gtx113",  # per API.md table; unverified live
        "s3_bucket": None,  # not yet confirmed from capture
    },
    "ap-southeast-2": {
        "client_id": None,  # not yet confirmed from capture
        "user_pool_id": "ap-southeast-2_vNriuUNeQ",
        "identity_pool_id": "ap-southeast-2:87d0fe24-16af-4189-b02f-984a7ed14ee0",
        "iot_host": "a3j5zqqo5iuph9-ats.iot.ap-southeast-2.amazonaws.com",
        "api_device_list": "1sfa49lnl8",
        "api_device_info": "7k2iuc99h7",
        "api_ota_check": "v7tlj1gnw7",
        "api_ota_job": None,  # not present in APK strings or capture
        "api_map": "2xipi98nw3",
        "api_user_account": "l2gobpcoqc",
        "api_kvs": None,  # not present in API.md; unknown
        "s3_bucket": None,  # not yet confirmed from capture
    },
    "ap-east-1": {
        "client_id": None,  # not yet confirmed from capture
        "user_pool_id": "ap-east-1_23Lf1WZer",
        "identity_pool_id": "ap-east-1:3e9265aa-f564-4083-8e1e-988e6cfdc446",
        "iot_host": "a3j5zqqo5iuph9-ats.iot.ap-east-1.amazonaws.com",
        "api_device_list": "08ydw34dfj",
        "api_device_info": "i1pbnu30si",
        "api_ota_check": "kdueg6qcwl",
        "api_ota_job": None,  # not present in APK strings or capture
        "api_map": "m35t3px95i",
        "api_user_account": "1h2q9awtqd",
        "api_kvs": "t0da44vtxf",  # per API.md table; unverified live
        "s3_bucket": None,  # not yet confirmed from capture
    },
}

# ---------------------------------------------------------------------------
# Work status codes (from pboutput PbRobotInfo.workStatus field)
# ---------------------------------------------------------------------------
WORK_STATUS_NONE = 0  # idle at station
WORK_STATUS_WAITING = 1  # ready, awaiting command
WORK_STATUS_MOWING = 2  # actively cutting
WORK_STATUS_PAUSE = 3  # paused mid-mow
WORK_STATUS_DOCKING = 4  # returning to base
WORK_STATUS_CHARGING = 5  # charging at station
WORK_STATUS_REMOTE_CONTROL = 6  # manual remote control
WORK_STATUS_ERROR = 7  # error state
WORK_STATUS_RESUME = 8  # resuming after pause
WORK_STATUS_ZONE_PARTITION = 9  # zone-specific cutting
WORK_STATUS_PAUSE_DOCKING = 10  # paused while returning
WORK_STATUS_UPDATING = 11  # OTA firmware update in progress
WORK_STATUS_CHARGING_FULL = 12  # fully charged
WORK_STATUS_EMERGENCY_STOP = 13  # emergency stop triggered
WORK_STATUS_ESCAPING = 14  # escaping obstacle
WORK_STATUS_RTT = 15  # factory RTT test
WORK_STATUS_AGING_TEST = 16  # factory aging test
WORK_STATUS_OFFLINE = -1  # virtual — no MQTT shadow

# Groups used for LawnMowerActivity mapping
WORK_STATUS_MOWING_GROUP = frozenset({WORK_STATUS_MOWING, WORK_STATUS_RESUME, WORK_STATUS_ZONE_PARTITION})
WORK_STATUS_RETURNING_GROUP = frozenset({WORK_STATUS_DOCKING, WORK_STATUS_PAUSE_DOCKING, WORK_STATUS_ESCAPING})
WORK_STATUS_DOCKED_GROUP = frozenset(
    {WORK_STATUS_NONE, WORK_STATUS_WAITING, WORK_STATUS_CHARGING, WORK_STATUS_CHARGING_FULL, WORK_STATUS_UPDATING}
)
WORK_STATUS_PAUSED_GROUP = frozenset({WORK_STATUS_PAUSE, WORK_STATUS_REMOTE_CONTROL})
WORK_STATUS_ERROR_GROUP = frozenset({WORK_STATUS_ERROR, WORK_STATUS_EMERGENCY_STOP})

# ---------------------------------------------------------------------------
# MQTT command codes (published to pbinput topic)
# ---------------------------------------------------------------------------
USER_CTRL_CLEAN = 1  # start fresh mow
USER_CTRL_DOCK = 2  # dock + cancel task (destructive)
USER_CTRL_PAUSE = 3  # pause in place
USER_CTRL_RESUME = 4  # resume from pause
USER_CTRL_GO_ZONE_PARTITION = 5  # enter single-zone mow mode
USER_CTRL_NO_GO_ZONE_PARTITION = 6  # enter no-go zone recording mode
USER_CTRL_EXIT_ZONE_PARTITION = 7  # exit zone recording mode
USER_CTRL_CLEAR_ZONE = 8  # delete a zone by hashId (confirmed from Hermes bytecode fn 8972 + fn 10144)
USER_CTRL_MODIFY_ZONE_INFO = 9  # rename / update zone metadata
USER_CTRL_MODIFY_ZONE_EDGE_START = 10  # start modifying a zone boundary
USER_CTRL_MODIFY_ZONE_EDGE_STOP = 11  # stop modifying a zone boundary
USER_CTRL_CHANNEL_START = 12  # start recording a channel
USER_CTRL_CHANNEL_FINISH = 13  # finish recording a channel
USER_CTRL_DELETE_CHANNEL = 14  # delete a channel by hashId (formula: 102 - reg88 = 14)
USER_CTRL_CLEAR_ALL_ZONES_CHANNELS = 15  # delete all zones and channels
USER_CTRL_SELF_CHECKING = 16  # run self-check routine
USER_CTRL_CHARGING_STATION_RESET = 17  # reset charging station location
USER_CTRL_LOCK = 18  # lock robot
USER_CTRL_QUERY_MAP = 19  # query full map (confirmed from logcat)
USER_CTRL_QUERY_SCHEDULES = 20  # query mowing schedules
USER_CTRL_PAUSE_DOCK = 21  # pause while returning to dock
USER_CTRL_RESUME_DOCK = 22  # resume docking return
USER_CTRL_QUERY_PATH = 23  # query robot’s historical path
USER_CTRL_QUERY_CLEANING_INFO = 24  # query current session cleaning info
USER_CTRL_SYNC_MAP = 25  # push edited map to robot (confirmed from Hermes bytecode analysis)
USER_CTRL_OTA = 26  # start OTA firmware update
USER_CTRL_ABORT_OTA = 27  # abort OTA update
USER_CTRL_FORCE_REINIT = 28  # stop in place, reset to waiting
USER_CTRL_COMPLETE_ZONE_PARTITION = 29  # complete zone recording
USER_CTRL_START_RECORDING = 30  # start perimeter / boundary recording
USER_CTRL_STOP_RECORDING = 31  # stop perimeter / boundary recording
USER_CTRL_EXIT_REMOTE = 32  # exit remote control mode
USER_CTRL_RECHARGE_DOCK = 33  # dock + keep task progress
USER_CTRL_QUERY_CLEANING_SUMMARY = 34  # query historical cleaning summary
USER_CTRL_QUERY_ROBOT_CONFIG = 35  # query robot configuration
USER_CTRL_SET_TASK_CONFIG = 36  # set task config (cut height, path spacing, etc.)
USER_CTRL_RESTORE_FACTORY = 37  # factory reset
USER_CTRL_MODIFY_STATION = 38  # modify charging station info
USER_CTRL_QUERY_CHANNELS = 39  # query all channels
USER_CTRL_FLOOR_SWITCH = 40  # switch active floor (multi-floor)
USER_CTRL_FLOOR_ADD = 41  # add a floor
USER_CTRL_FLOOR_DELETE = 42  # delete a floor
USER_CTRL_FLOOR_MODIFY = 43  # modify floor info
USER_CTRL_FLOOR_BACKUP = 44  # backup floor data
USER_CTRL_FLOOR_RESTORE = 45  # restore floor data from backup
USER_CTRL_START_MOW_SCHEDULE = 46  # activate a mowing schedule
USER_CTRL_RESET_INIT = 47  # reinitialise robot
USER_CTRL_GLOBAL_SETTING_Y = 48  # accept global setting change
USER_CTRL_GLOBAL_SETTING_N = 49  # reject global setting change
USER_CTRL_SET_RUN_TIME_CONFIG = 50  # set runtime configuration
USER_CTRL_QUERY_RUN_TIME_CONFIG = 51  # query runtime configuration
USER_CTRL_QUERY_WIFI_4G = 52  # query Wi-Fi / 4G status
USER_CTRL_QUERY_NET_DETAIL = 53  # query detailed network info
USER_CTRL_SWITCH_LTE_AIRPLANE = 54  # toggle LTE airplane mode
USER_CTRL_MERGE_ZONE = 55  # merge two zones into one
USER_CTRL_CUT_ZONE = 56  # split (cut) a zone
USER_CTRL_QUERY_RTK_DIAGNOSTIC_L1 = 57  # RTK level-1 diagnostic
USER_CTRL_QUERY_RTK_DIAGNOSTIC_L2 = 58  # RTK level-2 diagnostic
USER_CTRL_MAX = 59  # sentinel — max valid command value

# Error-code -> user-facing description. For the 54 codes the app surfaces, the text
# is the OFFICIAL English string bundled in the app's i18n resource (Hermes-v96
# bytecode, `errors` namespace, keyed by PbErrorCode value; grouped keys like
# code_50_53_67_68_69_70_71 expanded). Remaining (internal) codes fall back to a
# description humanized from the PbErrorCode enum symbolic name. See ERROR_REMEDIATION
# for the app's step-by-step fix text.
ERROR_DESCRIPTIONS: dict[int, str] = {
    0: "No error",
    1: "Wheel Motor Error",
    2: "Motor Overheat",
    3: "Wheel Motor Error",
    4: "Battery temperature abnormal",
    5: "Battery charging abnormal",
    6: "Battery voltage abnormal",
    7: "Lifting Motor Jammed",
    8: "Second lift blocked",
    9: "Soc comm. lost",
    10: "Blade Motor Error",
    11: "Blade RPM abnormal",
    12: "Localization no calibration config",
    13: "Navigation Internal Error",
    14: "Localization EKF failed",
    15: "Weak RTK Signal",
    16: "Location Service Init Timeout",
    17: "Unsafe Drop Detected",
    18: "Excessive Tilt Detected",
    19: "Slipping Detected",
    20: "Out of Bounds",
    21: "Mower Stuck",
    22: "Segmentation model failed",
    23: "Map not exist",
    24: "Map incorrect",
    25: "Charging Station not Detected",
    26: "Map no channel to dock",
    27: "No Available Mowing Zone",
    28: "Zone Not Reachable",
    29: "Charging Station Tag Not Detected",
    30: "Docking Failed",
    31: "Battery Low",
    32: "Camera Signal Lost",
    33: "IMU Signal Lost",
    34: "GPS Signal Lost",
    35: "Sensor bluetooth init failed",
    36: "Sensor bluetooth broadcast failed",
    37: "MCU comm. lost",
    38: "Wifi ssid not found",
    39: "Wifi connect failed",
    40: "OTA battery low",
    41: "OTA robot not in wait",
    42: "OTA download failed",
    43: "OTA upgrade failed",
    44: "Bumper Jammed",
    45: "Blade Jammed",
    46: "Location Service Unstable",
    47: "Segmentation comm. lost",
    48: "Path-planning back timeout",
    49: "Path-planning channel broken",
    50: "Navigation Internal Error",
    51: "Charging Not Detected",
    52: "Charging Station Not Reachable",
    53: "Navigation Internal Error",
    54: "Map base station moved",
    55: "Charge station not found",
    56: "Not in ODD",
    57: "No pose out",
    58: "Charging Station Placement Issue",
    59: "Sensor front ultrasonic",
    60: "Sensor rear ultrasonic",
    61: "No ENU Base Point from RTK Base Station",
    62: "Map not match",
    63: "Charge station invalid",
    64: "Out of Bounds",
    65: "Out of Bounds",
    66: "Mower Stuck",
    67: "Navigation Internal Error",
    68: "Navigation Internal Error",
    69: "Navigation Internal Error",
    70: "Navigation Internal Error",
    71: "Navigation Internal Error",
    72: "Wheel Motor Control Fault",
    73: "Navigation Internal Error",
    74: "Channel Obstacle Detected",
    75: "Channel Obstacle Detected",
    76: "Perimeter Obstacle Detected",
    77: "Perimeter Obstacle Detected",
    78: "Blade over current",
    79: "Navigation Internal Error",
    80: "Charging Station Tag Not Detected",
    81: "Weak RTK Signal",
    82: "Weak RTK Signal",
    83: "Slipping on the Channel",
    84: "Slipping Detected",
    85: "Init failed count",
    86: "Out of Bounds",
    87: "Out of Bounds",
    88: "Path-planning out of where",
    89: "Blade Jammed",
}


# Error-code -> official remediation steps (app i18n `errors` namespace, *_detail keys).
# Only the 54 user-surfaced codes have remediation text.
ERROR_REMEDIATION: dict[int, str] = {
    1: "1. Clear error and resume operation.\n2. If unresolved: Power cycle and retry.\n3. Still failing? Contact official support.",
    2: "1. Clear error and resume operation\n2. If unresolved: Power off for 5 minutes and retry\n3. Still failing? Contact official support",
    3: "1. Clear error and resume operation.\n2. If unresolved: Power cycle and retry.\n3. Still failing? Contact official support.",
    7: "1. Remove debris around the motor and retry.\n2. Press and hold the “–” button to reset if needed.\n3. Restart the mower if the issue persists.",
    10: "1. Clear error and resume operation\n2. If unresolved: Power cycle and retry\n3. Still failing? Contact official support",
    13: "1. Please drive the mower into the zone, at least 3 meters (approximately 9.8 feet) away from the boundary or obstacle, then clear error and resume operation.\n2. If unresolved: Please restart the mower and resume operation.",
    15: "Help your mower navigate better:\n1. Move robot to open area\n2. Reposition RTK reference station (clear sky view)",
    16: "Location service not initialized. Please drive the mower to an open area, then drive it forward or backward about 2 meters (approximately 6.6 feet) to activate it.",
    17: "Unsafe drop detected. Please move the robot to a different spot.",
    18: "Please move the mower to flat ground, press STOP, then press HOME button to resume operation.",
    19: "Slipping detected. Please move the robot to a different spot.",
    20: "Mower out of the work area. Please return it to the mapped zone.",
    21: "1. Inspect the tracks for any obstructions.\n2. Move the mower to open area and resume operation.",
    25: "Please add a charging station in the app and ensure a channel to the work zone.",
    27: "Please create a go-zone before mowing.",
    28: "Please ensure all target mowing zones are connected by channels.",
    29: "1. Please ensure the charging station area is well-lit, and both the camera lens and tag surface are clean and unobstructed.\n2. Please update the charging station location in the app if it has been moved.",
    30: "Please clear the error and retry. If unsuccessful, manually assist the mower to dock.",
    31: "Please charge the mower above 20% before mowing.",
    32: "Please restart the mower. If the issue persists after a few times of restart, contact official support",
    33: "Please restart the mower. If the issue persists after a few times of restart, contact official support",
    34: "Please restart the mower. If the issue persists after a few times of restart, contact official support",
    44: "Press the bumper to check movement. Clear any debris if it's stuck.",
    45: "Please power off the mower and remove debris from the blade.",
    46: "Location service unstable. Please power cycle the mower.",
    50: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    51: "1. Please check the charging station's power supply and clean the charging contacts.\n2. Please check if the immersion sensor under the charging station was triggered by water.\n3. Restart the mower and the charging station if needed.",
    52: "The mower cannot return to the charging station. Please ensure all zones are connected by channels, with one zone linked directly to the charging station.",
    53: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    58: "Invalid charging station location. Please move it completely outside the zone.",
    61: "1. Please check the RTK reference station’s power supply.\n2. GNSS acquisition takes up to 3 mins on startup. Please wait.\n3. Still failing? Contact official support.",
    64: "Mower out of the work area. Please return it to the mapped zone.",
    65: "Mower out of the work area. Please return it to the mapped zone.",
    66: "1. Inspect the tracks for any obstructions.\n2. Move the mower to open area and resume operation.",
    67: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    68: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    69: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    70: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    71: "Please move the mower to a new position and retry. If the error persists, cancel the task.",
    72: "Please restart the mower. If the issue persists after a few times of restart, contact official support",
    73: "1. Please restart the mower and resume operation.\n2. Still failing? Please cancel the task.",
    74: "Please check if there are obstacles in the channel.",
    75: "Please check if there are obstacles in the channel.",
    76: "Please check for obstacles on the perimeter.",
    77: "Please check for obstacles on the perimeter.",
    79: "1. Please drive the mower into the zone, at least 3 meters (approximately 9.8 feet) away from the boundary or obstacle, then clear error and resume operation.\n2. If unresolved: Please restart the mower and resume operation.",
    80: "1. Please ensure the charging station area is well-lit, and both the camera lens and tag surface are clean and unobstructed.\n2. Please update the charging station location in the app if it has been moved.",
    81: "Help your mower navigate better:\n1. Move robot to open area.\n2. Reposition RTK reference station (clear sky view).",
    82: "Help your mower navigate better:\n1. Move robot to open area.\n2. Reposition RTK reference station (clear sky view).",
    83: "Please first confirm whether the channel slips frequently. If it does, it is recommended to change the channel's position.",
    84: "Slipping detected. Please move the robot to a different spot.",
    86: "Mower out of the work area. Please return it to the mapped zone.",
    87: "Mower out of the work area. Please return it to the mapped zone.",
    89: "Please power off the mower and remove debris from the blade.",
}

# Warning-code -> description. For the 31 codes the app surfaces, the text is the
# OFFICIAL English string from the app's i18n `warnings` namespace (keyed by
# PbWarningCode value). Remaining codes fall back to the humanized enum name.
WARNING_DESCRIPTIONS: dict[int, str] = {
    0: "No warning",
    1: "Wheel over current",
    2: "Wheel over voltage",
    3: "Wheel under voltage",
    4: "Battery current abnormal",
    5: "First lift timeout",
    6: "Second lift timeout",
    7: "Front ultrasonic lost",
    8: "Back ultrasonic lost",
    9: "Battery soc communication abnormal",
    10: "Mcu thread schedule abnormal",
    11: "Blade over temperature",
    12: "Blade over current",
    13: "Blade communication abnormal",
    14: "Localization ignore cmd",
    15: "Location service not initialized. Please drive the mower to an open area, then drive it forward or backward about 2 meters (approximately 6.6 feet) to activate it.",
    16: "Localization invalid sensor data",
    17: "Camera view blocked",
    18: "Localization camera data unsync",
    19: "Please move the robot to open area and try again.",
    20: "Localization texture weak",
    21: "Please reset via Settings->Cancel Task, then drive the mower to an open area and move it forward or backward about 2 meters (approximately 6.6 feet) to activate it.",
    22: "Localization ekf abnormal",
    23: "Segmentation low light",
    24: "Robot stuck (escaping)",
    25: "Control system signal lost",
    26: "Sensor camera temperature abnormal",
    27: "Camera signal lost. Please restart the mower. If the issue persists after a few times of restart, contact official support",
    28: "IMU signal lost. Please restart the mower. If the issue persists after a few times of restart, contact official support",
    29: "GPS signal lost. Please restart the mower. If the issue persists after a few times of restart, contact official support",
    30: "Robot slipping",
    31: "Location system signal lost",
    32: "Blade stuck",
    33: "Segmentation communication abnormal",
    35: "Localization low light",
    37: "Zone not connected",
    38: "Zone cannot be closed. Start and end points are too far apart.",
    39: "Zone area is too small",
    40: "No go zone must be inside a go zone",
    41: "Channel must start and end within go zones.",
    42: "Only one docking channel is allowed",
    43: "Zone shape is invalid",
    44: "New boundary is too far from the original boundary",
    45: "Adjustment too small. Start point and end point are too close.",
    46: "Edit failed. This change would make the connected channel invalid.",
    47: "Internal error",
    48: "No target found in the robot",
    49: "Please add a docking channel for the robot",
    50: "No need to add the docking channel",
    51: "RTK signal not detected. Please drive the mower near the RTK base station and try again. If the warning persists, check the base station’s power or rebind it.",
    52: "RTK base station pairing failed",
    53: "Charging station location unavailable. Please move the charging station completely out of the zone.",
    54: "Localization yaw abnormal",
    55: "No-go zone illegal",
    56: "Schedules linked to zones have been updated. Please review and adjust your schedules if needed.",
    57: "Divide the zone line segment to have at least two intersection points with the zone.",
    58: "Merge or split failed. Internal error.",
    59: "After splitting, an excessively narrow area was generated. The minimum width of the zone must be greater than 1.5 meters (approximately 5 feet).",
    60: "After splitting, excessively small areas exist; at least larger than 2 m² (approximately 21.5 ft²).",
    61: "After the merger, the charging station is wrapped within the zone.",
    62: "The two zones do not overlap and cannot be merged.",
}

# RTK status codes
RTK_STATUS_NOT_READY = 0
RTK_STATUS_FLOAT_FIX = 1  # ~40 cm precision
RTK_STATUS_FIXED = 2  # ~2 cm precision

# ---------------------------------------------------------------------------
# BLE manual-drive characteristic (local, not via MQTT)
# ---------------------------------------------------------------------------
# UUID confirmed from GATT discovery (ReadByTypeRsp) in BTSnoop capture
BLE_DRIVE_CHARACTERISTIC_UUID = "12345678-1234-5678-1234-56789abcdef1"
# ATT handle of the drive characteristic value (handle from BLE connection)
BLE_DRIVE_CHARACTERISTIC_HANDLE = 0x0014
# Velocity ranges confirmed from ADB joystick swipe captures
BLE_DRIVE_LINEAR_MAX = 0.5  # m/s (forward: +, backward: -)
# Confirmed from capture (see encode_ble_drive): +0.6 = full left turn (CCW), -0.6 = right.
BLE_DRIVE_ANGULAR_MAX = 0.6  # rad/s (left: +, right: -)
# Proprietary GATT service that owns the drive characteristic (sibling ...def0)
BLE_DRIVE_SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
# The app refreshes the drive characteristic ~10 Hz while the joystick is held.
BLE_DRIVE_REFRESH_HZ = 10
# Safety cap: a single ble_drive service call may not move the robot longer than this.
BLE_DRIVE_MAX_DURATION_S = 5.0

# Config-entry option holding the robot's BLE MAC (manual-drive transport).
CONF_BLE_ADDRESS = "ble_address"

# Services
SERVICE_BLE_DRIVE = "ble_drive"
ATTR_LINEAR = "linear"
ATTR_ANGULAR = "angular"
ATTR_DURATION = "duration"

# ---------------------------------------------------------------------------
# Protobuf enum values (extracted from the APK Hermes bytecode 2026-05-24).
# Each map is {wire_value: APP_CONSTANT_NAME} so unknown values come back as
# the integer (caller's responsibility); use ``.get(v, v)`` for label lookups.
# Field-of-origin recorded next to each.
# ---------------------------------------------------------------------------

# PbRunTimeConfig.cleanMode (int) / PbTaskConfig.cleanMode (int).
CLEAN_MODES = {
    0: "NONE",
    1: "ZIGZAG",
    2: "ADAPTIVE_ZIGZAG",
    3: "CHESS_BOARD",
    4: "PERIMETER_LAPS_ONLY",
}

# PbTaskConfig.obsDecMode (int): obstacle-detection sensitivity.
OBS_DEC_MODES = {
    0: "NONE",
    1: "TOUCH_ONLY",
    2: "SMART_DEC",
    3: "SMART_DEC_MEDIUM_SENS",
    4: "SMART_DEC_LOW_SENS",
}

# PbTaskConfig.perimeterMowDir (int).
MOW_DIRS = {
    0: "CLOCKWISE",
    1: "COUNTERCLOCKWISE",
    2: "SHUFFLE",
}

# PbRobotInfo.startMode (int): how the current task was started.
START_MODES = {
    0: "NONE",
    1: "APP_SELECT",  # user selected zone(s) in the app
    2: "APP_ALL",  # user pressed "mow all"
    3: "ROBOT_KEY",  # physical button on the robot
    4: "APP_SCHEDULES",  # auto-started by a schedule
}

# PbRobotInfo.isCharging/isRecharging map to this discrete bat status.
BAT_STATUSES = {
    0: "NONE",
    1: "NO_CHARGING",
    2: "CHARGING",
    3: "CHARGING_FULL",
}

# PbRobotConfig.vehLedStatus/camLedStatus brightness levels.
LED_LEVELS = {
    0: "NONE",
    1: "LOW",
    2: "MEDIUM",
    3: "HIGH",
    4: "OFF",
}

# Wireless link state (used by Wi-Fi / 4G / BT status fields).
WIRELESS_STATES = {
    0: "NONE",
    1: "CONNECTED",
    2: "DISCONNECTED",
    3: "BROADCASTING",
}

# rtkStatus values surfaced as the LymowRtkSensor state (PbOutput.rtkStatus,
# field 4 of the GPS/RTK sub-message). Matches the LymowRtkSensor label map.
RTK_SIGNAL_QUALITY = {
    0: "NO_SIGNAL",
    1: "SINGLE_POINT",
    2: "FLOAT_FIXED",
    3: "FIXED",
}

# PbTaskConfig.chargingMode (int): "Return to Dock" route on Device Settings.
# Note the (sic) APK enum prefix "CHARING_MODE" (missing G). Same wire field.
CHARGING_MODES = {
    0: "NORMAL",  # app label "Follow Perimeter"
    1: "QUICK",  # app label "Direct Route"
}

# PbTaskConfig.zoneOrder (int).
ZONE_ORDERS = {
    0: "OPTIMIZE",
    1: "CUSTOM",
}

# Camera auto-exposure gear (PbDebugSetting.aeGear).
AE_GEARS = {
    0: "NONE",
    1: "GEAR_1",
    2: "GEAR_2",
    3: "GEAR_3",
    4: "GEAR_4",
    5: "GEAR_5",
    6: "GEAR_6",
    7: "MAX",
}

# PbAlgoLocOutput.nodeStatus (algorithm runtime state).
ALGO_NODE_STATES = {
    0: "NONE",
    1: "WAITING",
    2: "INITIALIZING",
    3: "RUNNING",
}

# PbOutput.outputCtrl values — server-side reply opcodes (analogous to userCtrl).
OUTPUT_CTRLS = {
    0: "NONE",
    1: "QUERY_MAP",
    2: "UPLOAD_SCHEDULES",
    3: "SAVE_MAP",
    6: "QUERY_PATH",
    7: "MODIFY_ZONE_INFO",
    8: "SYNC_MAP",
    9: "GLOBAL_SETTING_Y",
    10: "GLOBAL_SETTING_N",
    11: "SET_RUN_TIME_CONFIG",
    12: "QUERY_RUN_TIME_CONFIG",
    13: "DELETE_ADD_CHANNEL",
}

# PbRobotConfig.signal one-shot codes (subset — only the ones we publish today
# need numeric constants; the rest are documented for reference). See the
# SocSignal enum in the APK (Hermes string-id 40889).
SIGNAL_POWER_OFF = 1
SIGNAL_BRAKE = 2
SIGNAL_STOP = 3
SIGNAL_TURN_ON_CAMERA_LIGHT = 6
SIGNAL_TURN_OFF_CAMERA_LIGHT = 7
SIGNAL_ONE_CLICK_LIFT = 8
SIGNAL_ONE_CLICK_LOWER = 9
# (SIGNAL_TURN_ON/OFF_VEHICLE_LIGHT live in protocol.py since the codec uses them.)
SIGNAL_TURN_ON_BT_BROADCAST = 12
SIGNAL_RELEASE_BRAKE = 25
SIGNAL_ROBOT_SHUTDOWN = 28
