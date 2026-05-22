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

# ---------------------------------------------------------------------------
# Error codes (from pboutput errorCodes field)
# ---------------------------------------------------------------------------
ERROR_DESCRIPTIONS: dict[int, str] = {
    0: "No error",
    1: "Wheel malfunction",
    4: "Battery temperature",
    5: "Battery charging fault",
    6: "Battery voltage",
    7: "Lift sensor blocked",
    31: "Low battery",
    51: "No RTK base station",
    52: "RTK bind failed",
    61: "RTK base error",
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
