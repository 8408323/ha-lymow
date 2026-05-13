from __future__ import annotations

DOMAIN = "lymow"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# How often to poll REST device state (MQTT keeps live state between polls)
POLLING_INTERVAL = 30  # seconds

# Per-region AWS configuration — all values extracted from traffic capture and APK analysis
REGION_CONFIG: dict[str, dict[str, str | None]] = {
    "eu-west-1": {
        "client_id":          "3h1sqv3hishjiofbv8giskjgb0",
        "user_pool_id":       "eu-west-1_6qNPbnrrd",
        "identity_pool_id":   "eu-west-1:c905a69c-0153-401a-a879-0c50b892015b",
        "iot_host":           "a3j5zqqo5iuph9-ats.iot.eu-west-1.amazonaws.com",
        "api_device_list":    "asjqh5wbtj",
        "api_device_info":    "6ghz1zkccg",
        "api_ota_check":      "eigc6a2ds9",
        "api_map":            "3q1zxz98l2",
        "api_user_account":   "l3hazobjk0",
    },
    "us-east-2": {
        "client_id":          None,  # not yet confirmed from capture
        "user_pool_id":       None,
        "identity_pool_id":   "us-east-2:037db699-5df0-4ed2-92b8-0dd0f1843918",
        "iot_host":           "a3j5zqqo5iuph9-ats.iot.us-east-2.amazonaws.com",
        "api_device_list":    "453ahng0z4",
        "api_device_info":    "xuw7gtx113",
        "api_ota_check":      "6at3p6r6ce",
        "api_map":            "suk4e76xe5",
        "api_user_account":   "6r8m5rxeth",
    },
    "ap-southeast-2": {
        "client_id":          None,  # not yet confirmed from capture
        "user_pool_id":       "ap-southeast-2_vNriuUNeQ",
        "identity_pool_id":   "ap-southeast-2:87d0fe24-16af-4189-b02f-984a7ed14ee0",
        "iot_host":           "a3j5zqqo5iuph9-ats.iot.ap-southeast-2.amazonaws.com",
        "api_device_list":    "1sfa49lnl8",
        "api_device_info":    "7k2iuc99h7",
        "api_ota_check":      "v7tlj1gnw7",
        "api_map":            "2xipi98nw3",
        "api_user_account":   "l2gobpcoqc",
    },
    "ap-east-1": {
        "client_id":          None,  # not yet confirmed from capture
        "user_pool_id":       "ap-east-1_23Lf1WZer",
        "identity_pool_id":   "ap-east-1:3e9265aa-f564-4083-8e1e-988e6cfdc446",
        "iot_host":           "a3j5zqqo5iuph9-ats.iot.ap-east-1.amazonaws.com",
        "api_device_list":    "08ydw34dfj",
        "api_device_info":    "i1pbnu30si",
        "api_ota_check":      "kdueg6qcwl",
        "api_map":            "m35t3px95i",
        "api_user_account":   "1h2q9awtqd",
    },
}

# ---------------------------------------------------------------------------
# Work status codes (from pboutput PbRobotInfo.workStatus field)
# ---------------------------------------------------------------------------
WORK_STATUS_NONE            = 0   # idle at station
WORK_STATUS_WAITING         = 1   # ready, awaiting command
WORK_STATUS_MOWING          = 2   # actively cutting
WORK_STATUS_PAUSE           = 3   # paused mid-mow
WORK_STATUS_DOCKING         = 4   # returning to base
WORK_STATUS_CHARGING        = 5   # charging at station
WORK_STATUS_REMOTE_CONTROL  = 6   # manual remote control
WORK_STATUS_ERROR           = 7   # error state
WORK_STATUS_RESUME          = 8   # resuming after pause
WORK_STATUS_ZONE_PARTITION  = 9   # zone-specific cutting
WORK_STATUS_PAUSE_DOCKING   = 10  # paused while returning
WORK_STATUS_UPDATING        = 11  # OTA firmware update in progress
WORK_STATUS_CHARGING_FULL   = 12  # fully charged
WORK_STATUS_EMERGENCY_STOP  = 13  # emergency stop triggered
WORK_STATUS_ESCAPING        = 14  # escaping obstacle
WORK_STATUS_RTT             = 15  # factory RTT test
WORK_STATUS_OFFLINE         = -1  # virtual — no MQTT shadow

# Groups used for LawnMowerActivity mapping
WORK_STATUS_MOWING_GROUP   = frozenset({WORK_STATUS_MOWING, WORK_STATUS_RESUME, WORK_STATUS_ZONE_PARTITION})
WORK_STATUS_RETURNING_GROUP = frozenset({WORK_STATUS_DOCKING, WORK_STATUS_PAUSE_DOCKING, WORK_STATUS_ESCAPING})
WORK_STATUS_DOCKED_GROUP   = frozenset({WORK_STATUS_NONE, WORK_STATUS_WAITING, WORK_STATUS_CHARGING,
                                         WORK_STATUS_CHARGING_FULL, WORK_STATUS_UPDATING})
WORK_STATUS_PAUSED_GROUP   = frozenset({WORK_STATUS_PAUSE, WORK_STATUS_REMOTE_CONTROL})
WORK_STATUS_ERROR_GROUP    = frozenset({WORK_STATUS_ERROR, WORK_STATUS_EMERGENCY_STOP})

# ---------------------------------------------------------------------------
# MQTT command codes (published to pbinput topic)
# ---------------------------------------------------------------------------
USER_CTRL_CLEAN                  = 1   # start fresh mow
USER_CTRL_DOCK                   = 2   # dock + cancel task (destructive)
USER_CTRL_PAUSE                  = 3   # pause in place
USER_CTRL_RESUME                 = 4   # resume from pause
USER_CTRL_PAUSE_DOCK             = 21  # pause while returning
USER_CTRL_RESUME_DOCK            = 22  # resume docking return
USER_CTRL_FORCE_REINIT           = 28  # stop in place, reset to waiting
USER_CTRL_RECHARGE_DOCK          = 33  # dock + keep task progress

# ---------------------------------------------------------------------------
# Error codes (from pboutput errorCodes field)
# ---------------------------------------------------------------------------
ERROR_DESCRIPTIONS: dict[int, str] = {
    0:  "No error",
    1:  "Wheel malfunction",
    4:  "Battery temperature",
    5:  "Battery charging fault",
    6:  "Battery voltage",
    7:  "Lift sensor blocked",
    31: "Low battery",
    51: "No RTK base station",
    52: "RTK bind failed",
    61: "RTK base error",
}

# RTK status codes
RTK_STATUS_NOT_READY = 0
RTK_STATUS_FLOAT_FIX = 1  # ~40 cm precision
RTK_STATUS_FIXED     = 2  # ~2 cm precision
