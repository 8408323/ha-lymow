"""Smoke test: every switch/number/select entity reads from a populated coordinator state."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Conftest doesn't pre-load lymow.select when HA isn't installed (the no-HA
# branch was missing the call). The other test files that need select.py
# already call _load_lymow_module manually — mirror that pattern.
from tests.conftest import _load_lymow_module

_load_lymow_module("select")

from lymow.number import (  # noqa: E402
    GeofenceRadiusNumber,
    LiveCutHeightNumber,
    LiveCutSpeedNumber,
    LiveMoveSpeedNumber,
    MowerVolumeNumber,
    RechargeBatteryThresholdNumber,
    ResumeBatteryThresholdNumber,
    RtkPauseThresholdNumber,
    ZoneCutHeightNumber,
)
from lymow.select import CameraLightSelect, ChargingModeSelect, ZoneOrderSelect  # noqa: E402
from lymow.switch import (  # noqa: E402
    AlertsOnlySwitch,
    ChargingHandbrakeSwitch,
    DockOnErrorSwitch,
    FindRobotSwitch,
    MobileNotificationSwitch,
    Prefer4gSwitch,
    RainCleaningSwitch,
    RechargeResumeSwitch,
    RtkAutoPauseSwitch,
    TheftDetectionSwitch,
    TheftLockSwitch,
    VehicleLedSwitch,
    ZoneEnabledSwitch,
)

THING = "thing-1"
DEVICE = {"deviceThingName": THING, "deviceName": "Test Mower", "sn": "SN-TEST"}
ZONE_HASH = "ABCD0001"


# ---------------------------------------------------------------------------
# Ground-truth populated state — mirrors what producers actually write.
# Each key here must match what a producer (protocol.py, coordinator.py,
# api.py, etc.) emits — if you rename a producer's key, the corresponding
# entry below has to change too. That's the whole point.
# ---------------------------------------------------------------------------


def _populated_state() -> dict:
    return {
        # REST /device-feature response — drives device-feature switches.
        "theftDetectionSwitch": True,
        "theftLock": False,
        "findRobotSwitch": True,
        "mobileNotificationSwitch": 1,  # MobileNotificationSwitch._ALERTS_ONLY_VALUE
        # PbOutput-decoded robotConfig — drives _RobotConfigBoolSwitch family
        # plus MowerVolumeNumber, RechargeResumeSwitch, RR threshold numbers.
        "robotConfig": {
            "isOpenLed": True,
            "metric_4g": False,
            "dockOnError": True,
            "audioVolume": 60,
            "rrConfig": {
                "enable": True,
                "rechargeBat": 20,
                "resumeBat": 80,
                "periodStart": {"hour": 9, "minute": 0},
                "periodEnd": {"hour": 18, "minute": 0},
            },
        },
        # Map response — drives ZoneEnabledSwitch, ZoneCutHeightNumber and
        # the PbTaskConfig selects/switches.
        "mapData": {
            "goZones": [
                {"hashId": ZONE_HASH, "isEnabled": True, "cutHeight": 40, "polygon": []},
            ],
            "taskConfig": {
                "chargingMode": 0,
                "zoneOrder": 1,
                "rainCleaning": True,
                "disableChargingPark": False,
            },
        },
        # /device-feature → geofence list (one entry minimum).
        "geoFence": [{"name": "home", "latitude": 0.0, "longitude": 0.0, "radius": 175}],
        # Coordinator optimistic write mirror (async_set_run_time_config) for
        # the Live* numbers; QUERY_RUN_TIME_CONFIG decode is not implemented yet.
        "runTimeConfig": {"cutHeight": 50, "moveSpeed": 0.6, "cutSpeed": 100},
    }


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: state} if state is not None else {}
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    # The few methods entities call eagerly during __init__:
    coord.is_rtk_guard_enabled = MagicMock(return_value=True)
    coord.get_rtk_guard_threshold = MagicMock(return_value=2)
    # Async setters (used by turn_on/turn_off + set_native_value) — the read
    # tests below never trigger writes but make the stubs awaitable for safety.
    coord.async_set_device_feature = AsyncMock()
    coord.async_set_robot_config = AsyncMock()
    coord.async_set_device_settings = AsyncMock()
    coord.async_set_recharge_resume = AsyncMock()
    coord.set_rtk_guard_enabled = MagicMock()
    coord.set_rtk_guard_threshold = MagicMock()
    coord.async_set_run_time_config = AsyncMock()
    coord.async_set_geofence_radius = AsyncMock()
    coord.async_update_zone_enabled = AsyncMock()
    coord.async_update_zone_cut_height = AsyncMock()
    return coord


# ---------------------------------------------------------------------------
# Switch entities — value property is ``is_on``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory,expected",
    [
        (lambda c: TheftDetectionSwitch(c, DEVICE), True),
        (lambda c: TheftLockSwitch(c, DEVICE), False),
        (lambda c: FindRobotSwitch(c, DEVICE), True),
        (lambda c: MobileNotificationSwitch(c, DEVICE), True),
        # Fixture sets mobileNotificationSwitch = 1 = _ALERTS_ONLY_VALUE, so both
        # the master tristate switch and the alerts-only sub-toggle read True.
        (lambda c: AlertsOnlySwitch(c, DEVICE), True),
        (lambda c: VehicleLedSwitch(c, DEVICE), True),
        (lambda c: Prefer4gSwitch(c, DEVICE), False),
        (lambda c: DockOnErrorSwitch(c, DEVICE), True),
        (lambda c: RainCleaningSwitch(c, DEVICE), True),
        # ChargingHandbrakeSwitch inverts disableChargingPark → on = handbrake engaged.
        (lambda c: ChargingHandbrakeSwitch(c, DEVICE), True),
        (lambda c: RechargeResumeSwitch(c, DEVICE), True),
        (lambda c: ZoneEnabledSwitch(c, DEVICE, ZONE_HASH), True),
        (lambda c: RtkAutoPauseSwitch(c, DEVICE), True),
    ],
)
def test_switch_is_on_reads_populated_state(factory, expected) -> None:
    """Every switch must return a bool when producer-written keys are present."""
    coord = _make_coord(_populated_state())
    entity = factory(coord)
    assert entity.is_on is expected, f"{type(entity).__name__}.is_on = {entity.is_on}, expected {expected}"


@pytest.mark.parametrize(
    "factory",
    [
        lambda c: TheftDetectionSwitch(c, DEVICE),
        lambda c: TheftLockSwitch(c, DEVICE),
        lambda c: FindRobotSwitch(c, DEVICE),
        lambda c: MobileNotificationSwitch(c, DEVICE),
        lambda c: AlertsOnlySwitch(c, DEVICE),
        lambda c: VehicleLedSwitch(c, DEVICE),
        lambda c: Prefer4gSwitch(c, DEVICE),
        lambda c: DockOnErrorSwitch(c, DEVICE),
        lambda c: RainCleaningSwitch(c, DEVICE),
        lambda c: ChargingHandbrakeSwitch(c, DEVICE),
        lambda c: RechargeResumeSwitch(c, DEVICE),
        lambda c: ZoneEnabledSwitch(c, DEVICE, ZONE_HASH),
        lambda c: RtkAutoPauseSwitch(c, DEVICE),
    ],
)
def test_switch_is_on_handles_empty_state_without_raising(factory) -> None:
    """Pre-first-poll empty state must yield None or sane default, never raise."""
    coord = _make_coord(state={})
    entity = factory(coord)
    # Calling is_on must not raise. Result can be None (unknown) or a default.
    _ = entity.is_on

    # Also cover pre-first-poll shape where coordinator.data has no thing key.
    missing_coord = _make_coord()
    missing_entity = factory(missing_coord)
    _ = missing_entity.is_on


# ---------------------------------------------------------------------------
# Number entities — value property is ``native_value``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory,expected_value",
    [
        (lambda c: GeofenceRadiusNumber(c, DEVICE), 175.0),
        (lambda c: ZoneCutHeightNumber(c, DEVICE, ZONE_HASH), 40.0),
        (lambda c: RtkPauseThresholdNumber(c, DEVICE), 2),
        (lambda c: MowerVolumeNumber(c, DEVICE), 60),
        (lambda c: RechargeBatteryThresholdNumber(c, DEVICE), 20),
        (lambda c: ResumeBatteryThresholdNumber(c, DEVICE), 80),
        (lambda c: LiveCutHeightNumber(c, DEVICE), 50),
        (lambda c: LiveMoveSpeedNumber(c, DEVICE), pytest.approx(0.6)),
        (lambda c: LiveCutSpeedNumber(c, DEVICE), 100),
    ],
)
def test_number_native_value_reads_populated_state(factory, expected_value) -> None:
    coord = _make_coord(_populated_state())
    entity = factory(coord)
    assert entity.native_value == expected_value, (
        f"{type(entity).__name__}.native_value = {entity.native_value}, expected {expected_value}"
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda c: GeofenceRadiusNumber(c, DEVICE),
        lambda c: ZoneCutHeightNumber(c, DEVICE, ZONE_HASH),
        lambda c: RtkPauseThresholdNumber(c, DEVICE),
        lambda c: MowerVolumeNumber(c, DEVICE),
        lambda c: RechargeBatteryThresholdNumber(c, DEVICE),
        lambda c: ResumeBatteryThresholdNumber(c, DEVICE),
        lambda c: LiveCutHeightNumber(c, DEVICE),
        lambda c: LiveMoveSpeedNumber(c, DEVICE),
        lambda c: LiveCutSpeedNumber(c, DEVICE),
    ],
)
def test_number_native_value_handles_empty_state_without_raising(factory) -> None:
    """Same first-poll-not-arrived contract as switches."""
    coord = _make_coord(state={})
    entity = factory(coord)
    _ = entity.native_value

    missing_coord = _make_coord()
    missing_entity = factory(missing_coord)
    _ = missing_entity.native_value


# ---------------------------------------------------------------------------
# Select entities — value property is ``current_option``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory,expected_label",
    [
        # _populated_state seeds chargingMode=0 → "Follow perimeter"; a value-to-label
        # inversion bug would render "Direct route" here and break the assertion.
        (lambda c: ChargingModeSelect(c, DEVICE), "Follow perimeter"),
        # _populated_state seeds zoneOrder=1 → "Custom".
        (lambda c: ZoneOrderSelect(c, DEVICE), "Custom"),
    ],
)
def test_select_current_option_matches_seeded_wire_value(factory, expected_label) -> None:
    """Selects map each wire int to its exact label — catches reversed mappings."""
    coord = _make_coord(_populated_state())
    entity = factory(coord)
    assert entity.current_option == expected_label, (
        f"{type(entity).__name__}.current_option = {entity.current_option!r}, expected {expected_label!r}"
    )


@pytest.mark.parametrize(
    "factory",
    [
        lambda c: ChargingModeSelect(c, DEVICE),
        lambda c: ZoneOrderSelect(c, DEVICE),
        lambda c: CameraLightSelect(c, DEVICE),
    ],
)
def test_select_handles_empty_state_without_raising(factory) -> None:
    coord = _make_coord(state={})
    entity = factory(coord)
    _ = entity.current_option

    missing_coord = _make_coord()
    missing_entity = factory(missing_coord)
    _ = missing_entity.current_option


def test_camera_light_select_is_write_optimistic() -> None:
    """CameraLightSelect has no state read-back; current_option is None until user selects."""
    coord = _make_coord(_populated_state())
    entity = CameraLightSelect(coord, DEVICE)
    assert entity.current_option is None


# ---------------------------------------------------------------------------
# Cross-platform: every write-entity has a unique unique_id within its
# platform when instantiated against the same device. Catches the within-
# platform suffix collision that HA's entity registry would silently drop.
# ---------------------------------------------------------------------------


def _all_switches(coord):
    return [
        TheftDetectionSwitch(coord, DEVICE),
        TheftLockSwitch(coord, DEVICE),
        FindRobotSwitch(coord, DEVICE),
        MobileNotificationSwitch(coord, DEVICE),
        AlertsOnlySwitch(coord, DEVICE),
        VehicleLedSwitch(coord, DEVICE),
        Prefer4gSwitch(coord, DEVICE),
        DockOnErrorSwitch(coord, DEVICE),
        RainCleaningSwitch(coord, DEVICE),
        ChargingHandbrakeSwitch(coord, DEVICE),
        RechargeResumeSwitch(coord, DEVICE),
        ZoneEnabledSwitch(coord, DEVICE, ZONE_HASH),
        RtkAutoPauseSwitch(coord, DEVICE),
    ]


def _all_numbers(coord):
    return [
        GeofenceRadiusNumber(coord, DEVICE),
        ZoneCutHeightNumber(coord, DEVICE, ZONE_HASH),
        RtkPauseThresholdNumber(coord, DEVICE),
        MowerVolumeNumber(coord, DEVICE),
        RechargeBatteryThresholdNumber(coord, DEVICE),
        ResumeBatteryThresholdNumber(coord, DEVICE),
        LiveCutHeightNumber(coord, DEVICE),
        LiveMoveSpeedNumber(coord, DEVICE),
        LiveCutSpeedNumber(coord, DEVICE),
    ]


def _all_selects(coord):
    return [
        ChargingModeSelect(coord, DEVICE),
        ZoneOrderSelect(coord, DEVICE),
        CameraLightSelect(coord, DEVICE),
    ]


@pytest.mark.parametrize(
    "platform,factory",
    [("switch", _all_switches), ("number", _all_numbers), ("select", _all_selects)],
)
def test_unique_ids_are_unique_within_platform(platform, factory) -> None:
    coord = _make_coord(_populated_state())
    entities = factory(coord)
    seen: dict[str, str] = {}
    dupes: list[tuple[str, str, str]] = []
    for e in entities:
        uid = e._attr_unique_id
        if uid in seen:
            dupes.append((uid, seen[uid], type(e).__name__))
        else:
            seen[uid] = type(e).__name__
    assert not dupes, f"{platform} unique_id collisions: {dupes}"
