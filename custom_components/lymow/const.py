from __future__ import annotations

DOMAIN = "lymow"

# Bus event fired once when a mow session finishes (mowing/returning -> docked),
# carrying the session summary. Mirrored by the Last-mow-session event entity.
EVENT_SESSION_COMPLETED = f"{DOMAIN}_session_completed"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_REGION = "region"

REGION_AUTO = "auto"
REGION_CHOICES = [REGION_AUTO, "eu-west-1", "us-east-2", "ap-southeast-2", "ap-east-1"]

# How often to poll REST device state (MQTT keeps live state between polls)
POLLING_INTERVAL = 30  # seconds
# How often to send the app-presence heartbeat + RTK diagnostic queries while the
# RTK diagnostics switch is on. The robot only streams RTK detail to a client that
# keeps registering presence; ~5s sustains it without the Lymow app open.
RTK_DIAGNOSTIC_POLL_SECONDS = 5

# Refresh Cognito tokens / AWS credentials this many seconds before they expire.
# Without refresh the access token lapses (~24 h) and every REST poll 401s, taking
# all entities unavailable until HA restarts.
AUTH_REFRESH_MARGIN_SECONDS = 600

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
# USER_CTRL_RESET_INIT (47): defined in the proto enum but NOT called by any
# RobotCommands method in the app (Hermes #9067 + the verify-loop table). Likely
# reserved / firmware-internal. The intuitive "reinitialise robot" semantic from
# the name is best matched by USER_CTRL_FORCE_REINIT (28) which the app DOES
# use — don't ship a 47 button without a confirmed live capture of what it does.
USER_CTRL_RESET_INIT = 47  # reserved — no app-side caller; do NOT expose as a button (see comment above)
# USER_CTRL_GLOBAL_SETTING_Y/N (48/49) are sent by ``RobotCommands.globalConfig``
# (Hermes fn #9012). Payload: ``PbInput { userCtrl: 48 or 49, map: PbMap {
# globalZoneConfig: PbZoneConfig, globalChannelConfig: PbChannelConfig } }``.
# 48 = "apply as default AND overwrite existing per-zone customizations";
# 49 = "apply as default only; leave existing customizations alone".
# Used to bulk-set defaults like cutHeight across every zone/channel. To ship
# as a service we'd need the PbChannelConfig wire layout (not yet extracted).
USER_CTRL_GLOBAL_SETTING_Y = 48  # set global zone/channel defaults + overwrite existing
USER_CTRL_GLOBAL_SETTING_N = 49  # set global zone/channel defaults, preserve existing
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

# ---------------------------------------------------------------------------
# Error codes (from pboutput errorCodes field)
# ---------------------------------------------------------------------------
# Full table extracted from PbOutput.fromObject's verify-and-cast loop over
# errorCodes (Hermes fn #9067 at offset 0x0048f20c) — the input value is
# matched against both the string name and the int value with paired
# JStrictEqual jumps to the same target, so we know each name's exact int.
# Names are kept as the app's identifiers; the labels are user-facing.
ERROR_NAMES: dict[int, str] = {
    0: "ERROR_NONE",
    1: "ERROR_WHEEL_DRIVE_MALFUNCTION",
    2: "ERROR_WHEEL_TEMP_ABN",
    3: "ERROR_WHEEL_COMM_LOST",
    4: "ERROR_BAT_TEMP_ABN",
    5: "ERROR_BAT_CHARGING_ABN",
    6: "ERROR_BAT_VOLTAGE_ABN",
    7: "ERROR_FIRST_LIFT_BLOCKED",
    8: "ERROR_SECOND_LIFT_BLOCKED",
    9: "ERROR_SOC_COMM_LOST",
    10: "ERROR_BLADE_COMM_LOST",
    11: "ERROR_BLADE_RPM_ABN",
    12: "ERROR_LOC_NO_CALIBRATION_TOML",
    13: "ERROR_LOC_VIO_FAILED",
    14: "ERROR_LOC_EKF_FAILED",
    15: "ERROR_LOC_INIT_RTK_NOT_FIX",
    16: "ERROR_LOC_INIT_TIMEOUT",
    17: "ERROR_ROBOT_CLIFF",
    18: "ERROR_ROBOT_INCLINE",
    19: "ERROR_ROBOT_SLIP",
    20: "ERROR_ROBOT_OUT_OF_MAP",
    21: "ERROR_ROBOT_STUCK",
    22: "ERROR_SEG_MODEL_FAILED",
    23: "ERROR_MAP_NOT_EXIST",
    24: "ERROR_MAP_INCORRECT",
    25: "ERROR_MAP_NO_DOCK",
    26: "ERROR_MAP_NO_CHANNEL_TO_DOCK",
    27: "ERROR_MAP_ZERO_GO_ZONES",
    28: "ERROR_MAP_ZONE_UNREACHABLE",
    29: "ERROR_DOCK_NOT_FOUND",
    30: "ERROR_DOCK_ERROR",
    31: "ERROR_LOW_BATTERY",
    32: "ERROR_SENSOR_CAMERA",
    33: "ERROR_SENSOR_IMU0",
    34: "ERROR_SENSOR_GNSS",
    35: "ERROR_SENSOR_BT_INIT_FAILED",
    36: "ERROR_SENSOR_BT_BROADCAST_FAILED",
    37: "ERROR_MCU_COMM_LOST",
    38: "ERROR_WIFI_SSID_NOT_FOUND",
    39: "ERROR_WIFI_CONNECT_FAILED",
    40: "ERROR_OTA_BATTERY_LOW",
    41: "ERROR_OTA_ROBOT_NOT_IN_WAIT",
    42: "ERROR_OTA_DOWNLOAD_FAILED",
    43: "ERROR_OTA_UPGRADE_FAILED",
    44: "ERROR_BUMPER_STUCK",
    45: "ERROR_BLADE_STUCK",
    46: "ERROR_LOC_COMM_LOST",
    47: "ERROR_SEG_COMM_LOST",
    48: "ERROR_PP_BACK_TIMEOUT",
    49: "ERROR_PP_CHANNEL_BROKEN",
    50: "ERROR_PP_CHANNEL_ERROR",
    51: "ERROR_PP_DOCK_SIGNAL_LOST",
    52: "ERROR_PP_DOCK_PATH_NOT_FOUND",
    53: "ERROR_PP_EXECUTION_ERROR",
    54: "ERROR_MAP_BASE_STATION_MOVED",
    55: "ERROR_CHARGE_STATION_NOT_FOUND",
    56: "ERROR_NOT_IN_ODD",
    57: "ERROR_NO_POSE_OUT",
    58: "ERROR_BASE_STATION_INVALID",
    59: "ERROR_SENSOR_FRONT_ULTRA",
    60: "ERROR_SENSOR_REAR_ULTRA",
    61: "ERROR_LOC_RTK_BASE",
    62: "ERROR_MAP_NOT_MATCH",
    63: "ERROR_CHARGE_STATION_INVALID",
    64: "ERROR_ROBOT_IN_NOGO",
    65: "ERROR_ROBOT_IN_NOGO_WALL",
    66: "ERROR_ROBOT_STUCK_TRAPP",
    67: "ERROR_PP_SOLVER_FAIL",
    68: "ERROR_PP_SEARCH_FAIL",
    69: "ERROR_BD_FAIL",
    70: "ERROR_CHANNEL_OFFSET",
    71: "ERROR_ACTION_TIMEOUT",
    72: "ERROR_CMD_WHEEL_SPD_INCOMPATIBLE",
    73: "ERROR_COSTMAP_ERROR",
    74: "ERROR_CHANNEL_BUMPER",
    75: "ERROR_CHANNEL_OBS",
    76: "ERROR_EDGE_FOLLOW_OBS",
    77: "ERROR_EDGE_UNPASSABLE",
    78: "ERROR_BLADE_OVER_CURRENT",
    79: "ERROR_LOC_YAW_ABN",
    80: "ERROR_DOCK_TIMEOUT",
    81: "ERROR_LOC_EDGE_SCORE_LOW",
    82: "ERROR_LOC_BD_SCORE_LOW",
    83: "ERROR_CHANNEL_SLIP",
    84: "ERROR_SLOPE_SLIP",
    85: "ERROR_INIT_FAILED_COUNT",
    86: "ERROR_RESUME_OUT_OF_MAP",
    87: "ERROR_START_OUT_OF_MAP",
    88: "ERROR_PP_OUT_OF_WHERE",
    89: "ERROR_THICK_BLADE_STUCK",
    90: "ERROR_CODE_MAX",
}

# User-facing labels — concise plain English derived from the APK identifier
# above. Used by LymowErrorSensor as the "description" attribute. The sensor's
# fallback for a missing-from-this-dict code is the literal string
# ``"Unknown ({code})"`` (see :meth:`LymowErrorSensor.extra_state_attributes`);
# extend this dict whenever a new ERROR_NAMES entry is added.
ERROR_DESCRIPTIONS: dict[int, str] = {
    0: "No error",
    1: "Wheel drive malfunction",
    2: "Wheel temperature",
    3: "Wheel comm lost",
    4: "Battery temperature",
    5: "Battery charging fault",
    6: "Battery voltage",
    7: "First lift sensor blocked",
    8: "Second lift sensor blocked",
    9: "SoC comm lost",
    10: "Blade comm lost",
    11: "Blade RPM abnormal",
    12: "Localisation: missing calibration",
    13: "Localisation: VIO failed",
    14: "Localisation: EKF failed",
    15: "Localisation init: RTK not fixed",
    16: "Localisation init: timeout",
    17: "Robot at cliff",
    18: "Robot incline too steep",
    19: "Robot wheel slip",
    20: "Robot outside map",
    21: "Robot stuck",
    22: "Segmentation model failed",
    23: "Map missing",
    24: "Map incorrect",
    25: "Map: no dock recorded",
    26: "Map: no channel to dock",
    27: "Map: zero go-zones",
    28: "Map: zone unreachable",
    29: "Charging dock not found",
    30: "Dock error",
    31: "Low battery",
    32: "Camera sensor error",
    33: "IMU sensor error",
    34: "GNSS sensor error",
    35: "Bluetooth init failed",
    36: "Bluetooth broadcast failed",
    37: "MCU comm lost",
    38: "Wi-Fi SSID not found",
    39: "Wi-Fi connect failed",
    40: "OTA: battery too low",
    41: "OTA: robot not in wait state",
    42: "OTA: download failed",
    43: "OTA: upgrade failed",
    44: "Bumper stuck",
    45: "Blade stuck",
    46: "Localisation comm lost",
    47: "Segmentation comm lost",
    48: "Path-planning: back timeout",
    49: "Path-planning: channel broken",
    50: "Path-planning: channel error",
    51: "Path-planning: dock signal lost",
    52: "Path-planning: dock path not found",
    53: "Path-planning: execution error",
    54: "Map: base station moved",
    55: "Charging station not found",
    56: "Robot outside operating domain",
    57: "No pose output",
    58: "Base station invalid",
    59: "Front ultrasonic sensor error",
    60: "Rear ultrasonic sensor error",
    61: "Localisation: RTK base error",
    62: "Map does not match",
    63: "Charging station invalid",
    64: "Robot inside no-go zone",
    65: "Robot at no-go wall",
    66: "Robot stuck (trapped)",
    67: "Path-planning solver failed",
    68: "Path-planning search failed",
    69: "Boundary-driving failed",
    70: "Channel offset too large",
    71: "Action timeout",
    72: "Command/wheel-speed incompatible",
    73: "Costmap error",
    74: "Channel bumper triggered",
    75: "Channel obstacle",
    76: "Edge-follow: obstacle",
    77: "Edge unpassable",
    78: "Blade over-current",
    79: "Localisation: yaw abnormal",
    80: "Dock timeout",
    81: "Localisation: low edge score",
    82: "Localisation: low boundary-driving score",
    83: "Channel slip",
    84: "Slope slip",
    85: "Init failed (repeat count)",
    86: "Resume: out of map",
    87: "Start: out of map",
    88: "Path-planning: out of where",
    89: "Thick blade stuck",
    90: "Max error code (sentinel)",
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

# Warnings reported alongside errors (pboutput field 4, packed int32).
# Extracted from the same PbOutput.fromObject loop as ERROR_NAMES; value 57
# is intentionally absent from the wire (likely deprecated upstream).
WARNING_NAMES: dict[int, str] = {
    0: "WARNING_NONE",
    1: "WARNING_WHEEL_OVER_CURRENT",
    2: "WARNING_WHEEL_OVER_VOLTAGE",
    3: "WARNING_WHEEL_UNDER_VOLTAGE",
    4: "WARNING_BAT_CURRENT_ABN",
    5: "WARNING_FIRST_LIFT_TIMEOUT",
    6: "WARNING_SECOND_LIFT_TIMEOUT",
    7: "WARNING_FRONT_ULTRA_LOST",
    8: "WARNING_BACK_ULTRA_LOST",
    9: "WARNING_SOC_COMM_ABN",
    10: "WARNING_MCU_THREAD_SCHEDULE_ABN",
    11: "WARNING_BLADE_OVER_TEMP",
    12: "WARNING_BLADE_OVER_CURRENT",
    13: "WARNING_BLADE_COMM_ABN",
    14: "WARNING_LOC_IGNORE_CMD",
    15: "WARNING_LOC_INIT_FAILED",
    16: "WARNING_LOC_INVALID_SENSOR_DATA",
    17: "WARNING_LOC_CAMERA_BLOCK",
    18: "WARNING_LOC_CAMERA_DATA_UNSYNC",
    19: "WARNING_LOC_RTK_SIGNAL_BAD",
    20: "WARNING_LOC_TEXTURE_WEAK",
    21: "WARNING_LOC_VIO_ABN",
    22: "WARNING_LOC_EKF_ABN",
    23: "WARNING_SEG_LOW_LIGHT",
    24: "WARNING_ROBOT_ESCAPING",
    25: "WARNING_MCU_COMM_ABN",
    26: "WARNING_SENSOR_CAMERA_TEMP_ABN",
    27: "WARNING_SENSOR_CAMERA_ABN",
    28: "WARNING_SENSOR_IMU0_ABN",
    29: "WARNING_SENSOR_GNSS_ABN",
    30: "WARNING_ROBOT_SLIP",
    31: "WARNING_LOC_COMM_ABN",
    32: "WARNING_BLADE_STUCK",
    33: "WARNING_SEG_COMM_ABN",
    34: "WARING_PP_LATERAL_ERROR_LARGE",  # APK typo "WARING" — kept verbatim
    35: "WARNING_LOC_LOW_LIGHT",
    36: "WARING_PP_EXECUTION",  # APK typo "WARING" — kept verbatim
    37: "WARNING_ZONE_NOT_CONNECTED",
    38: "WARNING_ZONE_END_FAR_FROM_START",
    39: "WARNING_ZONE_AREA_TOO_SMALL",
    40: "WARNING_NO_GO_NOT_IN_ZONE",
    41: "WARNING_CHANNEL_START_NOT_IN_ZONE",
    42: "WARNING_ONLY_ONE_DOCKING_CHANNEL_ALLOWED",
    43: "WARNING_ZONE_EIGHT_PATH",
    44: "WARNING_MODIFY_ZONE_FAR_FROM_EDGE",
    45: "WARNING_MODIFY_ZONE_START_CLOSE_END",
    46: "WARNING_MODIFY_ZONE_CHANGE_CHANNEL_POINT",
    47: "WARNING_MODIFY_ZONE_INTERNAL_FAIL",
    48: "WARNING_CAN_NOT_FIND_OBJECTS",
    49: "WARNING_ADD_DOCKING_CHANNEL",
    50: "WARNING_DOCKING_CHANNEL_UNNECESSARY",
    51: "WARNING_LOC_NO_RTK_BASE",
    52: "WARNING_RTK_BIND_FAIL",
    53: "WARNING_BASE_STATION_INVALID",
    54: "WARNING_LOC_YAW_ABN",
    55: "WARNING_NOGO_ZONE_ILLEGAL",
    56: "WARNING_SCHEDULE_MODIFY",
    58: "WARNING_MAP_OPERATE_FAIL",
    59: "WARNING_DIVIDE_NARROW_PART",
    60: "WARNING_DIVIDE_AREA_SMALL",
    61: "WARNING_CHARGE_STATION_INVALID",
    62: "WARNING_ZONE_NOT_OVERLAPPED",
    63: "WARNING_CODE_MAX",
}

WARNING_DESCRIPTIONS: dict[int, str] = {
    0: "No warning",
    1: "Wheel over-current",
    2: "Wheel over-voltage",
    3: "Wheel under-voltage",
    4: "Battery current abnormal",
    5: "First lift timeout",
    6: "Second lift timeout",
    7: "Front ultrasonic lost",
    8: "Rear ultrasonic lost",
    9: "SoC comm abnormal",
    10: "MCU thread schedule abnormal",
    11: "Blade over-temperature",
    12: "Blade over-current",
    13: "Blade comm abnormal",
    14: "Localisation ignored command",
    15: "Localisation init failed",
    16: "Localisation: invalid sensor data",
    17: "Localisation: camera blocked",
    18: "Localisation: camera data unsynced",
    19: "Localisation: RTK signal bad",
    20: "Localisation: weak texture",
    21: "Localisation: VIO abnormal",
    22: "Localisation: EKF abnormal",
    23: "Segmentation: low light",
    24: "Robot escaping obstacle",
    25: "MCU comm abnormal",
    26: "Camera sensor temperature abnormal",
    27: "Camera sensor abnormal",
    28: "IMU sensor abnormal",
    29: "GNSS sensor abnormal",
    30: "Robot slipping",
    31: "Localisation comm abnormal",
    32: "Blade stuck",
    33: "Segmentation comm abnormal",
    34: "Path-planning: large lateral error",
    35: "Localisation: low light",
    36: "Path-planning: execution warning",
    37: "Zone not connected",
    38: "Zone: end far from start",
    39: "Zone area too small",
    40: "No-go zone not inside go-zone",
    41: "Channel start not inside zone",
    42: "Only one docking channel allowed",
    43: "Zone has figure-8 path",
    44: "Modified zone too far from edge",
    45: "Modified zone start too close to end",
    46: "Modified zone changed channel point",
    47: "Modify-zone internal failure",
    48: "Cannot find objects",
    49: "Add docking channel",
    50: "Docking channel unnecessary",
    51: "Localisation: no RTK base",
    52: "RTK bind failed",
    53: "Base station invalid",
    54: "Localisation: yaw abnormal",
    55: "No-go zone illegal",
    56: "Schedule modified",
    58: "Map operate failed",
    59: "Divide: narrow part",
    60: "Divide: area too small",
    61: "Charging station invalid",
    62: "Zone not overlapped",
    63: "Max warning code (sentinel)",
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

# PbCleanReport.mowEndType (int): how the last mowing session ended.
# From PbCleanReport.fromObject (Hermes fn #9799) — name-and-value pairs in the
# verify loop. MOW_END_100 = task completed normally (the "100" refers to 100% progress).
MOW_END_TYPES = {
    0: "NONE",
    1: "COMPLETED",
    2: "USER_CANCELLED",
}

# PbOutput.aeRangeLevel (int enum, field 38): the camera's auto-exposure
# "gear" setting — controls the AE algorithm's exposure-time range. From
# PbOutput.fromObject (Hermes fn #9067) name-and-value pairs around the
# ``aeRangeLevel`` branch. MAX is the brightest; NONE means AE auto-controlled.
AE_RANGE_LEVELS = {
    0: "NONE",
    1: "1",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
    7: "MAX",
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
SIGNAL_TURN_ON_CAMERA_LIGHT = 6  # camera headlight, full brightness
SIGNAL_TURN_OFF_CAMERA_LIGHT = 7  # camera headlight off (also: setNightMode disable)
SIGNAL_ONE_CLICK_LIFT = 8
SIGNAL_ONE_CLICK_LOWER = 9
# (SIGNAL_TURN_ON/OFF_VEHICLE_LIGHT live in protocol.py since the codec uses them.)
SIGNAL_TURN_ON_BT_BROADCAST = 12
SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE = 15  # camera headlight, mid brightness
SIGNAL_TURN_ON_CAMERA_LIGHT_LOW = 16  # camera headlight, low brightness
SIGNAL_RELEASE_BRAKE = 25
SIGNAL_ROBOT_SHUTDOWN = 28
