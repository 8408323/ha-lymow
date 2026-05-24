"""Pin protobuf enum maps to the values the APK uses."""

from __future__ import annotations

from lymow.const import (
    AE_GEARS,
    ALGO_NODE_STATES,
    BAT_STATUSES,
    CLEAN_MODES,
    LED_LEVELS,
    MOW_DIRS,
    OBS_DEC_MODES,
    OUTPUT_CTRLS,
    RTK_SIGNAL_QUALITY,
    START_MODES,
    WIRELESS_STATES,
    WORK_STATUS_AGING_TEST,
)


def test_work_status_aging_test_present() -> None:
    """Robot can report factory aging-test state (=16)."""
    assert WORK_STATUS_AGING_TEST == 16


def test_clean_modes_pinned() -> None:
    # PbTaskConfig.cleanMode — verified against the APK CLEAN_MODE enum.
    assert CLEAN_MODES == {
        0: "NONE",
        1: "ZIGZAG",
        2: "ADAPTIVE_ZIGZAG",
        3: "CHESS_BOARD",
        4: "PERIMETER_LAPS_ONLY",
    }


def test_obs_dec_modes_pinned() -> None:
    # PbTaskConfig.obsDecMode (obstacle-detection sensitivity)
    assert OBS_DEC_MODES == {
        0: "NONE",
        1: "TOUCH_ONLY",
        2: "SMART_DEC",
        3: "SMART_DEC_MEDIUM_SENS",
        4: "SMART_DEC_LOW_SENS",
    }


def test_mow_dirs_pinned() -> None:
    # PbTaskConfig.perimeterMowDir
    assert MOW_DIRS == {0: "CLOCKWISE", 1: "COUNTERCLOCKWISE", 2: "SHUFFLE"}


def test_start_modes_pinned() -> None:
    # PbRobotInfo.startMode — how the current task was started
    assert START_MODES == {
        0: "NONE",
        1: "APP_SELECT",
        2: "APP_ALL",
        3: "ROBOT_KEY",
        4: "APP_SCHEDULES",
    }


def test_bat_statuses_pinned() -> None:
    assert BAT_STATUSES == {0: "NONE", 1: "NO_CHARGING", 2: "CHARGING", 3: "CHARGING_FULL"}


def test_led_levels_pinned() -> None:
    # PbRobotConfig.vehLedStatus / camLedStatus brightness levels
    assert LED_LEVELS == {0: "NONE", 1: "LOW", 2: "MEDIUM", 3: "HIGH", 4: "OFF"}


def test_wireless_states_pinned() -> None:
    assert WIRELESS_STATES == {0: "NONE", 1: "CONNECTED", 2: "DISCONNECTED", 3: "BROADCASTING"}


def test_rtk_signal_quality_pinned() -> None:
    # PbOutput rtkStatus — drives LymowRtkSensor + RTK auto-pause threshold range (0..3)
    assert RTK_SIGNAL_QUALITY == {0: "NO_SIGNAL", 1: "SINGLE_POINT", 2: "FLOAT_FIXED", 3: "FIXED"}


def test_ae_gears_pinned() -> None:
    assert AE_GEARS == {
        0: "NONE",
        1: "GEAR_1",
        2: "GEAR_2",
        3: "GEAR_3",
        4: "GEAR_4",
        5: "GEAR_5",
        6: "GEAR_6",
        7: "MAX",
    }


def test_algo_node_states_pinned() -> None:
    assert ALGO_NODE_STATES == {0: "NONE", 1: "WAITING", 2: "INITIALIZING", 3: "RUNNING"}


def test_output_ctrls_known_values() -> None:
    # OUTPUT_CTRL_* are the pboutput-side opcodes — distinct numbering from
    # the input-side USER_CTRL_* (e.g. SYNC_MAP is 25 inbound, 8 outbound).
    assert OUTPUT_CTRLS[1] == "QUERY_MAP"
    assert OUTPUT_CTRLS[8] == "SYNC_MAP"
    assert OUTPUT_CTRLS[11] == "SET_RUN_TIME_CONFIG"
    assert OUTPUT_CTRLS[12] == "QUERY_RUN_TIME_CONFIG"
    assert OUTPUT_CTRLS[13] == "DELETE_ADD_CHANNEL"


def test_signal_constants_match_protocol_module() -> None:
    """Vehicle-LED signal constants live in `protocol.py`; const mirrors the rest."""
    from lymow.const import SIGNAL_ROBOT_SHUTDOWN, SIGNAL_TURN_OFF_CAMERA_LIGHT, SIGNAL_TURN_ON_CAMERA_LIGHT
    from lymow.protocol import SIGNAL_TURN_OFF_VEHICLE_LIGHT, SIGNAL_TURN_ON_VEHICLE_LIGHT

    # Adjacent values; camera-light is 6/7 (note the off is 7, not 11 like vehicle)
    assert SIGNAL_TURN_ON_CAMERA_LIGHT == 6
    assert SIGNAL_TURN_OFF_CAMERA_LIGHT == 7
    # Vehicle light uses different signal codes
    assert SIGNAL_TURN_ON_VEHICLE_LIGHT == 10
    assert SIGNAL_TURN_OFF_VEHICLE_LIGHT == 11
    assert SIGNAL_ROBOT_SHUTDOWN == 28
