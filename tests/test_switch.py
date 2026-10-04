"""Tests for switch.py — ZoneEnabledSwitch, device-feature switches, and async_setup_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lymow.switch import (
    AppPresenceSwitch,
    FindRobotSwitch,
    RtkDiagnosticsPollSwitch,
    TheftDetectionSwitch,
    TheftLockSwitch,
    VehicleLedSwitch,
    ZoneEnabledSwitch,
    async_setup_entry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}
HASH_ID = "aabbccdd"
HASH_ID2 = "11223344"

_ZONE_ON = {"hashId": HASH_ID, "isEnabled": True, "area": 10.0}
_ZONE_OFF = {"hashId": HASH_ID, "isEnabled": False, "area": 10.0}
_ZONE2 = {"hashId": HASH_ID2, "isEnabled": True, "area": 8.0}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: state or {}}
    coord.devices = [DEVICE]
    coord.async_update_zone_enabled = AsyncMock()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def _make_entity(zone: dict | None = None) -> ZoneEnabledSwitch:
    state = {"mapData": {"goZones": [zone or _ZONE_ON]}}
    coord = _make_coord(state)
    return ZoneEnabledSwitch(coord, DEVICE, HASH_ID)


# ---------------------------------------------------------------------------
# ZoneEnabledSwitch init
# ---------------------------------------------------------------------------


def test_unique_id() -> None:
    e = _make_entity()
    assert e._attr_unique_id == f"{THING}_{HASH_ID}_enabled"


def test_name() -> None:
    e = _make_entity()
    assert e._attr_has_entity_name is True
    assert HASH_ID[:4] in e._attr_name
    assert e._attr_device_info["name"] == "Mower 1"


def test_device_name_fallback_sn() -> None:
    coord = _make_coord({"mapData": {"goZones": [_ZONE_ON]}})
    e = ZoneEnabledSwitch(coord, {"deviceThingName": THING, "sn": "SN1"}, HASH_ID)
    assert e._attr_device_info["name"] == "SN1"


# ---------------------------------------------------------------------------
# _zone property
# ---------------------------------------------------------------------------


def test_zone_found() -> None:
    e = _make_entity()
    assert e._zone is not None
    assert e._zone["hashId"] == HASH_ID


def test_zone_not_found_no_mapdata() -> None:
    coord = _make_coord({})
    e = ZoneEnabledSwitch(coord, DEVICE, HASH_ID)
    assert e._zone is None


def test_zone_not_found_wrong_hash() -> None:
    state = {"mapData": {"goZones": [_ZONE2]}}
    coord = _make_coord(state)
    e = ZoneEnabledSwitch(coord, DEVICE, HASH_ID)  # HASH_ID not in zones
    assert e._zone is None


# ---------------------------------------------------------------------------
# available
# ---------------------------------------------------------------------------


def test_available_when_zone_found() -> None:
    e = _make_entity()
    assert e.available is True


def test_not_available_when_zone_missing() -> None:
    coord = _make_coord({})
    e = ZoneEnabledSwitch(coord, DEVICE, HASH_ID)
    assert e.available is False


# ---------------------------------------------------------------------------
# is_on
# ---------------------------------------------------------------------------


def test_is_on_true() -> None:
    e = _make_entity(_ZONE_ON)
    assert e.is_on is True


def test_is_on_false() -> None:
    e = _make_entity(_ZONE_OFF)
    assert e.is_on is False


def test_is_on_default_true_when_key_absent() -> None:
    e = _make_entity({"hashId": HASH_ID})  # no isEnabled
    assert e.is_on is True


def test_is_on_none_when_no_zone() -> None:
    coord = _make_coord({})
    e = ZoneEnabledSwitch(coord, DEVICE, HASH_ID)
    assert e.is_on is None


# ---------------------------------------------------------------------------
# async_turn_on / async_turn_off
# ---------------------------------------------------------------------------


async def test_turn_on_calls_coordinator() -> None:
    e = _make_entity()
    await e.async_turn_on()
    e.coordinator.async_update_zone_enabled.assert_called_once_with(THING, HASH_ID, True)


async def test_turn_off_calls_coordinator() -> None:
    e = _make_entity()
    await e.async_turn_off()
    e.coordinator.async_update_zone_enabled.assert_called_once_with(THING, HASH_ID, False)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_skips_async_add_entities_when_no_devices() -> None:
    """Empty devices list must not call async_add_entities with an empty list."""
    from lymow.const import DOMAIN

    coord = MagicMock()
    coord.devices = []
    coord.data = {}
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    add_calls: list[list] = []
    await async_setup_entry(hass, entry, lambda entities: add_calls.append(list(entities)))
    assert all(batch for batch in add_calls), f"empty batch passed to async_add_entities: {add_calls}"


async def test_async_setup_entry_no_zones_initially() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord({})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    # No zones, but device-feature switches still register once per device.
    zone_entities = [e for e in added if isinstance(e, ZoneEnabledSwitch)]
    assert zone_entities == []


async def test_async_setup_entry_creates_entities_for_zones() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord({"mapData": {"goZones": [_ZONE_ON, _ZONE2]}})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    zone_entities = [e for e in added if isinstance(e, ZoneEnabledSwitch)]
    assert len(zone_entities) == 2


async def test_async_setup_entry_registers_listener() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord({})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    await async_setup_entry(hass, entry, lambda entities: None)
    coord.async_add_listener.assert_called_once()


async def test_async_setup_entry_listener_callback_adds_new_zones() -> None:
    """Listener callback dynamically adds zone entities when coordinator data updates."""
    from lymow.const import DOMAIN

    coord = _make_coord({})  # no zones initially
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    captured_callback = None

    def _register_listener(cb):
        nonlocal captured_callback
        captured_callback = cb
        return lambda: None

    coord.async_add_listener.side_effect = _register_listener

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    zone_entities = [e for e in added if isinstance(e, ZoneEnabledSwitch)]
    assert zone_entities == []

    coord.data = {THING: {"mapData": {"goZones": [_ZONE_ON]}}}
    captured_callback()

    zone_entities = [e for e in added if isinstance(e, ZoneEnabledSwitch)]
    assert len(zone_entities) == 1


async def test_async_setup_entry_does_not_duplicate_zones() -> None:
    """Listener callback should not re-add zones already tracked."""
    from lymow.const import DOMAIN

    coord = _make_coord({"mapData": {"goZones": [_ZONE_ON]}})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    captured_callback = None

    def _register_listener(cb):
        nonlocal captured_callback
        captured_callback = cb
        return lambda: None

    coord.async_add_listener.side_effect = _register_listener

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    zone_entities = [e for e in added if isinstance(e, ZoneEnabledSwitch)]
    assert len(zone_entities) == 1

    # Fire callback again with same zone — should not add again
    captured_callback()
    zone_entities = [e for e in added if isinstance(e, ZoneEnabledSwitch)]
    assert len(zone_entities) == 1


# ---------------------------------------------------------------------------
# Device-feature switches
# ---------------------------------------------------------------------------


def _make_feature_coord(feature_state: dict[str, object] | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: dict(feature_state or {})}
    coord.devices = [DEVICE]
    coord.async_set_device_feature = AsyncMock()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def test_theft_detection_switch_unique_id_and_name() -> None:
    coord = _make_feature_coord({"theftDetectionSwitch": True})
    e = TheftDetectionSwitch(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_theftDetectionSwitch"
    assert "Theft detection" in e._attr_name


def test_theft_detection_switch_is_on_true() -> None:
    coord = _make_feature_coord({"theftDetectionSwitch": True})
    e = TheftDetectionSwitch(coord, DEVICE)
    assert e.is_on is True


def test_theft_detection_switch_is_on_false() -> None:
    coord = _make_feature_coord({"theftDetectionSwitch": False})
    e = TheftDetectionSwitch(coord, DEVICE)
    assert e.is_on is False


def test_theft_detection_switch_is_on_none_when_missing() -> None:
    coord = _make_feature_coord({})
    e = TheftDetectionSwitch(coord, DEVICE)
    assert e.is_on is None


async def test_theft_detection_switch_turn_on_calls_coordinator() -> None:
    coord = _make_feature_coord({})
    e = TheftDetectionSwitch(coord, DEVICE)
    await e.async_turn_on()
    coord.async_set_device_feature.assert_awaited_once_with(THING, theftDetectionSwitch=True)


async def test_theft_detection_switch_turn_off_calls_coordinator() -> None:
    coord = _make_feature_coord({"theftDetectionSwitch": True})
    e = TheftDetectionSwitch(coord, DEVICE)
    await e.async_turn_off()
    coord.async_set_device_feature.assert_awaited_once_with(THING, theftDetectionSwitch=False)


async def test_theft_lock_switch_turn_on() -> None:
    coord = _make_feature_coord({})
    e = TheftLockSwitch(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_theftLock"
    await e.async_turn_on()
    coord.async_set_device_feature.assert_awaited_once_with(THING, theftLock=True)


async def test_theft_lock_switch_turn_on_skips_when_already_on() -> None:
    coord = _make_feature_coord({"theftLock": True})
    e = TheftLockSwitch(coord, DEVICE)
    await e.async_turn_on()
    coord.async_set_device_feature.assert_not_called()


async def test_theft_lock_switch_turn_off_skips_when_already_off() -> None:
    coord = _make_feature_coord({"theftLock": False})
    e = TheftLockSwitch(coord, DEVICE)
    await e.async_turn_off()
    coord.async_set_device_feature.assert_not_called()


async def test_find_robot_switch_turn_on() -> None:
    coord = _make_feature_coord({})
    e = FindRobotSwitch(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_findRobotSwitch"
    await e.async_turn_on()
    coord.async_set_device_feature.assert_awaited_once_with(THING, findRobotSwitch=True)


async def test_async_setup_entry_creates_feature_switches() -> None:
    """async_setup_entry should add 4 feature switches per device on first call."""
    from lymow.const import DOMAIN

    coord = _make_feature_coord({"theftDetectionSwitch": True})
    coord.async_update_zone_enabled = AsyncMock()

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    feature_types = {type(e).__name__ for e in added}
    assert "TheftDetectionSwitch" in feature_types
    assert "TheftLockSwitch" in feature_types
    assert "FindRobotSwitch" in feature_types
    assert "MobileNotificationSwitch" in feature_types
    assert "AlertsOnlySwitch" in feature_types
    assert "VehicleLedSwitch" in feature_types
    assert "Prefer4gSwitch" in feature_types
    assert "DockOnErrorSwitch" in feature_types


# ---------------------------------------------------------------------------
# VehicleLedSwitch — robotConfig.isOpenLed (bool)
# ---------------------------------------------------------------------------


def _make_robot_config_coord(robot_config: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: {"robotConfig": dict(robot_config)} if robot_config is not None else {}}
    coord.devices = [DEVICE]
    coord.async_set_robot_config = AsyncMock()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def test_vehicle_led_switch_metadata_and_unknown_when_missing() -> None:
    coord = _make_robot_config_coord(None)
    e = VehicleLedSwitch(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_isOpenLed"
    assert "Vehicle LED" in e._attr_name
    # No robotConfig yet → proto3 default (off): an absent bool is false, so an
    # interactive off-toggle is correct, not the ⚡ that None renders as.
    assert e.is_on is False


def test_vehicle_led_switch_reads_state_from_robot_config() -> None:
    on = VehicleLedSwitch(_make_robot_config_coord({"isOpenLed": True}), DEVICE)
    off = VehicleLedSwitch(_make_robot_config_coord({"isOpenLed": False}), DEVICE)
    assert on.is_on is True
    assert off.is_on is False


def test_vehicle_led_switch_off_when_dock_on_error_known_and_led_absent() -> None:
    # dockOnError present, isOpenLed absent → proto3 default → LED is off
    e = VehicleLedSwitch(_make_robot_config_coord({"dockOnError": True}), DEVICE)
    assert e.is_on is False


def test_vehicle_led_switch_defaults_off_when_no_robot_config() -> None:
    e = VehicleLedSwitch(_make_robot_config_coord({}), DEVICE)
    assert e.is_on is False


async def test_vehicle_led_switch_writes_via_signal_not_isOpenLed() -> None:
    """Match the app's switchVehicleLed: write signal=10 (on) / 11 (off), not isOpenLed."""
    from lymow.protocol import SIGNAL_TURN_OFF_VEHICLE_LIGHT, SIGNAL_TURN_ON_VEHICLE_LIGHT

    coord_on = _make_robot_config_coord({"isOpenLed": False})
    await VehicleLedSwitch(coord_on, DEVICE).async_turn_on()
    coord_on.async_set_robot_config.assert_awaited_once_with(THING, signal=SIGNAL_TURN_ON_VEHICLE_LIGHT)

    coord_off = _make_robot_config_coord({"isOpenLed": True})
    await VehicleLedSwitch(coord_off, DEVICE).async_turn_off()
    coord_off.async_set_robot_config.assert_awaited_once_with(THING, signal=SIGNAL_TURN_OFF_VEHICLE_LIGHT)


async def test_vehicle_led_switch_optimistic_update_on_toggle() -> None:
    coord = _make_robot_config_coord({"isOpenLed": False})
    coord.data = {THING: {"robotConfig": {"isOpenLed": False}}}
    # Make async_set_updated_data actually persist to coord.data
    coord.async_set_updated_data.side_effect = lambda d: coord.__setattr__("data", d)
    e = VehicleLedSwitch(coord, DEVICE)
    await e.async_turn_on()
    assert coord.data[THING]["robotConfig"]["isOpenLed"] is True
    await e.async_turn_off()
    assert coord.data[THING]["robotConfig"]["isOpenLed"] is False


def test_vehicle_led_switch_optimistic_skipped_when_no_data() -> None:
    coord = _make_robot_config_coord({})
    coord.data = None
    e = VehicleLedSwitch(coord, DEVICE)
    e._set_led_optimistic(True)  # must not raise when coord.data is None


# ---------------------------------------------------------------------------
# Prefer4gSwitch — robotConfig.metric_4g (bool, on=4G/off=Wi-Fi)
# ---------------------------------------------------------------------------


def test_prefer_4g_switch_metadata_and_reads_state() -> None:
    from lymow.switch import Prefer4gSwitch

    e = Prefer4gSwitch(_make_robot_config_coord({"metric_4g": True}), DEVICE)
    assert e._attr_unique_id == f"{THING}_metric_4g"
    assert "4G" in e._attr_name
    assert e.is_on is True
    assert Prefer4gSwitch(_make_robot_config_coord({"metric_4g": False}), DEVICE).is_on is False
    assert Prefer4gSwitch(_make_robot_config_coord(None), DEVICE).is_on is False
    # Present-but-malformed (hostile decode) → unknown, not coerced.
    assert Prefer4gSwitch(_make_robot_config_coord({"metric_4g": 1}), DEVICE).is_on is None


async def test_prefer_4g_switch_turn_on_off_publishes_robot_config() -> None:
    from lymow.switch import Prefer4gSwitch

    coord_on = _make_robot_config_coord({"metric_4g": False})
    await Prefer4gSwitch(coord_on, DEVICE).async_turn_on()
    coord_on.async_set_robot_config.assert_awaited_once_with(THING, metric_4g=True)

    coord_off = _make_robot_config_coord({"metric_4g": True})
    await Prefer4gSwitch(coord_off, DEVICE).async_turn_off()
    coord_off.async_set_robot_config.assert_awaited_once_with(THING, metric_4g=False)


# ---------------------------------------------------------------------------
# DockOnErrorSwitch — robotConfig.dockOnError (bool)
# ---------------------------------------------------------------------------


def test_dock_on_error_switch_reads_state_and_unique_id() -> None:
    from lymow.switch import DockOnErrorSwitch

    on = DockOnErrorSwitch(_make_robot_config_coord({"dockOnError": True}), DEVICE)
    assert on._attr_unique_id == f"{THING}_dockOnError"
    assert on.is_on is True
    assert DockOnErrorSwitch(_make_robot_config_coord({"dockOnError": False}), DEVICE).is_on is False
    assert DockOnErrorSwitch(_make_robot_config_coord(None), DEVICE).is_on is False


async def test_dock_on_error_switch_writes_robot_config() -> None:
    from lymow.switch import DockOnErrorSwitch

    coord_on = _make_robot_config_coord({"dockOnError": False})
    await DockOnErrorSwitch(coord_on, DEVICE).async_turn_on()
    coord_on.async_set_robot_config.assert_awaited_once_with(THING, dockOnError=True)

    coord_off = _make_robot_config_coord({"dockOnError": True})
    await DockOnErrorSwitch(coord_off, DEVICE).async_turn_off()
    coord_off.async_set_robot_config.assert_awaited_once_with(THING, dockOnError=False)


# ---------------------------------------------------------------------------
# MobileNotificationSwitch (integer 0/2 instead of bool)
# ---------------------------------------------------------------------------


def test_mobile_notification_switch_unique_id() -> None:
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 2})
    e = MobileNotificationSwitch(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_mobileNotificationSwitch"
    assert "Mobile notifications" in e._attr_name


def test_mobile_notification_switch_is_on_when_value_is_two() -> None:
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 2})
    e = MobileNotificationSwitch(coord, DEVICE)
    assert e.is_on is True


def test_mobile_notification_switch_is_off_when_value_is_zero() -> None:
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 0})
    e = MobileNotificationSwitch(coord, DEVICE)
    assert e.is_on is False


def test_mobile_notification_switch_is_on_when_alerts_only() -> None:
    """Value 1 = "alerts only" (app's sub-mode) still counts as on."""
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 1})
    e = MobileNotificationSwitch(coord, DEVICE)
    assert e.is_on is True


def test_mobile_notification_switch_is_none_when_missing() -> None:
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({})
    e = MobileNotificationSwitch(coord, DEVICE)
    assert e.is_on is None


async def test_mobile_notification_switch_turn_on_sends_int_two() -> None:
    """The wire format expects the integer 2, NOT Python True."""
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({})
    e = MobileNotificationSwitch(coord, DEVICE)
    await e.async_turn_on()
    coord.async_set_device_feature.assert_awaited_once_with(THING, mobileNotificationSwitch=2)


async def test_mobile_notification_switch_turn_off_sends_int_zero() -> None:
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 2})
    e = MobileNotificationSwitch(coord, DEVICE)
    await e.async_turn_off()
    coord.async_set_device_feature.assert_awaited_once_with(THING, mobileNotificationSwitch=0)


# ---------------------------------------------------------------------------
# AlertsOnlySwitch — the app's "Alerts only" sub-toggle (mobileNotificationSwitch 1/2)
# ---------------------------------------------------------------------------


def test_alerts_only_unique_id_distinct_from_master() -> None:
    """Both back the same mobileNotificationSwitch field; their unique_ids must
    differ or HA would drop one entity on a registry collision."""
    from lymow.switch import AlertsOnlySwitch, MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 2})
    master = MobileNotificationSwitch(coord, DEVICE)
    alerts = AlertsOnlySwitch(coord, DEVICE)
    assert alerts._attr_unique_id == f"{THING}_alerts_only"
    assert alerts._attr_unique_id != master._attr_unique_id


def test_mobile_notification_unknown_value_is_none() -> None:
    """Untrusted wire data: an unexpected int reports unknown, not off."""
    from lymow.switch import MobileNotificationSwitch

    e = MobileNotificationSwitch(_make_feature_coord({"mobileNotificationSwitch": 9}), DEVICE)
    assert e.is_on is None


def test_alerts_only_available_when_value_missing() -> None:
    """Before the first poll (value None) the sub-toggle stays available, not flickering out."""
    from lymow.switch import AlertsOnlySwitch

    e = AlertsOnlySwitch(_make_feature_coord({}), DEVICE)
    assert e.available is True


def test_alerts_only_on_when_value_is_one() -> None:
    from lymow.switch import AlertsOnlySwitch

    e = AlertsOnlySwitch(_make_feature_coord({"mobileNotificationSwitch": 1}), DEVICE)
    assert e.is_on is True
    assert e.available is True


def test_alerts_only_off_when_value_is_two() -> None:
    from lymow.switch import AlertsOnlySwitch

    e = AlertsOnlySwitch(_make_feature_coord({"mobileNotificationSwitch": 2}), DEVICE)
    assert e.is_on is False
    assert e.available is True


def test_alerts_only_unavailable_when_notifications_off() -> None:
    from lymow.switch import AlertsOnlySwitch

    e = AlertsOnlySwitch(_make_feature_coord({"mobileNotificationSwitch": 0}), DEVICE)
    assert e.available is False
    assert e.is_on is False


def test_alerts_only_is_none_when_missing() -> None:
    from lymow.switch import AlertsOnlySwitch

    e = AlertsOnlySwitch(_make_feature_coord({}), DEVICE)
    assert e.is_on is None


async def test_alerts_only_turn_on_sends_one() -> None:
    from lymow.switch import AlertsOnlySwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 2})
    await AlertsOnlySwitch(coord, DEVICE).async_turn_on()
    coord.async_set_device_feature.assert_awaited_once_with(THING, mobileNotificationSwitch=1)


async def test_alerts_only_turn_off_sends_two() -> None:
    from lymow.switch import AlertsOnlySwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 1})
    await AlertsOnlySwitch(coord, DEVICE).async_turn_off()
    coord.async_set_device_feature.assert_awaited_once_with(THING, mobileNotificationSwitch=2)


# ---------------------------------------------------------------------------
# RtkAutoPauseSwitch — reads + writes a coordinator-side flag (no API call)
# ---------------------------------------------------------------------------


def test_rtk_auto_pause_switch_reflects_coordinator_state() -> None:
    from lymow.switch import RtkAutoPauseSwitch

    coord = MagicMock()
    coord.is_rtk_guard_enabled = MagicMock(return_value=False)
    e = RtkAutoPauseSwitch(coord, DEVICE)
    assert e.is_on is False
    coord.is_rtk_guard_enabled = MagicMock(return_value=True)
    assert e.is_on is True


def test_rtk_auto_pause_switch_unique_id_and_disabled_default() -> None:
    from lymow.switch import RtkAutoPauseSwitch

    coord = MagicMock()
    coord.is_rtk_guard_enabled = MagicMock(return_value=False)
    e = RtkAutoPauseSwitch(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_rtk_auto_pause"
    assert e._attr_entity_registry_enabled_default is False


async def test_rtk_auto_pause_switch_turn_on_toggles_coordinator() -> None:
    from lymow.switch import RtkAutoPauseSwitch

    coord = MagicMock()
    coord.is_rtk_guard_enabled = MagicMock(return_value=False)
    e = RtkAutoPauseSwitch(coord, DEVICE)
    e.async_write_ha_state = MagicMock()
    await e.async_turn_on()
    coord.set_rtk_guard_enabled.assert_called_once_with(THING, True)


async def test_rtk_auto_pause_switch_turn_off_toggles_coordinator() -> None:
    from lymow.switch import RtkAutoPauseSwitch

    coord = MagicMock()
    coord.is_rtk_guard_enabled = MagicMock(return_value=True)
    e = RtkAutoPauseSwitch(coord, DEVICE)
    e.async_write_ha_state = MagicMock()
    await e.async_turn_off()
    coord.set_rtk_guard_enabled.assert_called_once_with(THING, False)


# ---------------------------------------------------------------------------
# Device Settings boolean switches (PbTaskConfig f3/f4 — rainCleaning + the
# inverted disableChargingPark).
# ---------------------------------------------------------------------------


def _make_task_config_coord(task_config: dict | None = None) -> MagicMock:
    coord = MagicMock()
    state: dict = {"mapData": {}}
    if task_config is not None:
        state["mapData"]["taskConfig"] = task_config
    coord.data = {THING: state}
    coord.devices = [DEVICE]
    coord.async_set_device_settings = AsyncMock()
    return coord


def test_rain_cleaning_switch_metadata_and_reads_state() -> None:
    from lymow.switch import RainCleaningSwitch

    e = RainCleaningSwitch(_make_task_config_coord({"rainCleaning": True}), DEVICE)
    assert e._attr_unique_id == f"{THING}_rainy_mowing"
    assert e._attr_name == "Rainy mowing"
    assert e.is_on is True

    e_off = RainCleaningSwitch(_make_task_config_coord({"rainCleaning": False}), DEVICE)
    assert e_off.is_on is False


def test_rain_cleaning_default_when_missing_unknown_when_non_bool() -> None:
    from lymow.switch import RainCleaningSwitch

    # Absent taskConfig / absent field → proto3 default (off, not None).
    assert RainCleaningSwitch(_make_task_config_coord(), DEVICE).is_on is False
    assert RainCleaningSwitch(_make_task_config_coord({}), DEVICE).is_on is False
    # int 1 from a hostile decode must not be treated as bool — surfaces unknown.
    assert RainCleaningSwitch(_make_task_config_coord({"rainCleaning": 1}), DEVICE).is_on is None


async def test_rain_cleaning_turn_on_off_calls_coordinator() -> None:
    from lymow.switch import RainCleaningSwitch

    coord = _make_task_config_coord({"rainCleaning": False})
    await RainCleaningSwitch(coord, DEVICE).async_turn_on()
    coord.async_set_device_settings.assert_awaited_once_with(THING, rainy_mowing=True)

    coord2 = _make_task_config_coord({"rainCleaning": True})
    await RainCleaningSwitch(coord2, DEVICE).async_turn_off()
    coord2.async_set_device_settings.assert_awaited_once_with(THING, rainy_mowing=False)


def test_charging_handbrake_switch_inverts_wire_for_ui_sense() -> None:
    """UI ON = handbrake engaged = wire ``disableChargingPark`` False."""
    from lymow.switch import ChargingHandbrakeSwitch

    on = ChargingHandbrakeSwitch(_make_task_config_coord({"disableChargingPark": False}), DEVICE)
    assert on.is_on is True
    off = ChargingHandbrakeSwitch(_make_task_config_coord({"disableChargingPark": True}), DEVICE)
    assert off.is_on is False


def test_charging_handbrake_metadata_and_default_when_missing() -> None:
    from lymow.switch import ChargingHandbrakeSwitch

    e = ChargingHandbrakeSwitch(_make_task_config_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_charging_handbrake"
    assert e._attr_name == "Charging handbrake"
    # Inverted: absent disableChargingPark → proto3 default wire False →
    # UI "handbrake on" (True), not None.
    assert e.is_on is True


async def test_charging_handbrake_turn_on_off_passes_ui_bool_through() -> None:
    """The coordinator (encoder) is responsible for inversion — the entity
    forwards the UI sense unchanged."""
    from lymow.switch import ChargingHandbrakeSwitch

    coord = _make_task_config_coord({"disableChargingPark": True})
    await ChargingHandbrakeSwitch(coord, DEVICE).async_turn_on()
    coord.async_set_device_settings.assert_awaited_once_with(THING, charging_handbrake=True)

    coord2 = _make_task_config_coord({"disableChargingPark": False})
    await ChargingHandbrakeSwitch(coord2, DEVICE).async_turn_off()
    coord2.async_set_device_settings.assert_awaited_once_with(THING, charging_handbrake=False)


# ---------------------------------------------------------------------------
# RechargeResumeSwitch — PbRobotConfig.rrConfig.enable (f1)
# ---------------------------------------------------------------------------


def _make_rr_coord(rr_config: dict | None = None) -> MagicMock:
    coord = MagicMock()
    state: dict = {"robotConfig": {}}
    if rr_config is not None:
        state["robotConfig"]["rrConfig"] = rr_config
    coord.data = {THING: state}
    coord.devices = [DEVICE]
    coord.async_set_recharge_resume = AsyncMock()
    return coord


def test_recharge_resume_switch_metadata_and_default_when_missing() -> None:
    from lymow.switch import RechargeResumeSwitch

    e = RechargeResumeSwitch(_make_rr_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_recharge_resume"
    assert e._attr_name == "Recharge & resume"
    # Absent rrConfig → proto3 default (off), not None.
    assert e.is_on is False


def test_recharge_resume_switch_reads_state_and_period_attrs() -> None:
    from lymow.switch import RechargeResumeSwitch

    e = RechargeResumeSwitch(
        _make_rr_coord(
            {
                "enable": True,
                "periodStart": {"hour": 4, "minute": 0},
                "periodEnd": {"hour": 20, "minute": 30},
            }
        ),
        DEVICE,
    )
    assert e.is_on is True
    assert e.extra_state_attributes == {"period_start": "04:00", "period_end": "20:30"}


def test_recharge_resume_switch_no_attrs_when_periods_missing() -> None:
    from lymow.switch import RechargeResumeSwitch

    e = RechargeResumeSwitch(_make_rr_coord({"enable": False}), DEVICE)
    assert e.is_on is False
    assert e.extra_state_attributes is None


def test_recharge_resume_switch_unknown_when_enable_not_bool() -> None:
    """``decode_rr_config`` already drops non-0/1 enable values, but guard the
    entity too in case rrConfig is built from a different source someday."""
    from lymow.switch import RechargeResumeSwitch

    assert RechargeResumeSwitch(_make_rr_coord({"enable": 1}), DEVICE).is_on is None


async def test_recharge_resume_switch_turn_on_off_calls_coordinator() -> None:
    from lymow.switch import RechargeResumeSwitch

    coord = _make_rr_coord({"enable": False})
    await RechargeResumeSwitch(coord, DEVICE).async_turn_on()
    coord.async_set_recharge_resume.assert_awaited_once_with(THING, enable=True)

    coord2 = _make_rr_coord({"enable": True})
    await RechargeResumeSwitch(coord2, DEVICE).async_turn_off()
    coord2.async_set_recharge_resume.assert_awaited_once_with(THING, enable=False)


# ---------------------------------------------------------------------------
# RtkDiagnosticsPollSwitch
# ---------------------------------------------------------------------------


def _make_poll_coord(polling: bool = False, presence: bool = False) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: {}}
    coord.devices = [DEVICE]
    coord.is_rtk_polling = MagicMock(return_value=polling)
    coord.is_presence_on = MagicMock(return_value=presence)
    coord.set_rtk_polling = MagicMock(return_value=False)
    coord.set_presence = MagicMock()
    return coord


def test_rtk_diag_switch_is_on_reflects_coordinator() -> None:
    assert RtkDiagnosticsPollSwitch(_make_poll_coord(True), DEVICE).is_on is True
    assert RtkDiagnosticsPollSwitch(_make_poll_coord(False), DEVICE).is_on is False


@pytest.mark.asyncio
async def test_rtk_diag_switch_turn_on_off_toggles_polling() -> None:
    coord = _make_poll_coord()
    sw = RtkDiagnosticsPollSwitch(coord, DEVICE)
    sw.hass = MagicMock(_notifications=[])
    sw.async_write_ha_state = MagicMock()
    await sw.async_turn_on()
    coord.set_rtk_polling.assert_called_with(THING, True)
    assert sw.hass._notifications == []
    await sw.async_turn_off()
    coord.set_rtk_polling.assert_called_with(THING, False)


@pytest.mark.asyncio
async def test_rtk_diag_switch_notifies_when_it_enables_presence() -> None:
    coord = _make_poll_coord()
    coord.set_rtk_polling = MagicMock(return_value=True)
    sw = RtkDiagnosticsPollSwitch(coord, DEVICE)
    sw.hass = MagicMock(_notifications=[])
    sw.async_write_ha_state = MagicMock()
    await sw.async_turn_on()
    assert len(sw.hass._notifications) == 1


def test_app_presence_switch_is_on_reflects_coordinator() -> None:
    assert AppPresenceSwitch(_make_poll_coord(presence=True), DEVICE).is_on is True
    assert AppPresenceSwitch(_make_poll_coord(presence=False), DEVICE).is_on is False


@pytest.mark.asyncio
async def test_app_presence_switch_turn_on_off() -> None:
    coord = _make_poll_coord()
    sw = AppPresenceSwitch(coord, DEVICE)
    sw.async_write_ha_state = MagicMock()
    await sw.async_turn_on()
    coord.set_presence.assert_called_with(THING, True)
    await sw.async_turn_off()
    coord.set_presence.assert_called_with(THING, False)


@pytest.mark.asyncio
async def test_app_presence_switch_restores_on_state() -> None:
    coord = _make_poll_coord()
    sw = AppPresenceSwitch(coord, DEVICE)
    sw._test_last_state = MagicMock(state="on")
    await sw.async_added_to_hass()
    coord.set_presence.assert_called_once_with(THING, True)


@pytest.mark.asyncio
async def test_rtk_diag_switch_restores_on_state() -> None:
    coord = _make_poll_coord()
    sw = RtkDiagnosticsPollSwitch(coord, DEVICE)
    sw._test_last_state = MagicMock(state="on")
    await sw.async_added_to_hass()
    coord.set_rtk_polling.assert_called_once_with(THING, True)


@pytest.mark.asyncio
async def test_rtk_diag_switch_no_restore_when_absent() -> None:
    coord = _make_poll_coord()
    sw = RtkDiagnosticsPollSwitch(coord, DEVICE)
    sw._test_last_state = None
    await sw.async_added_to_hass()
    coord.set_rtk_polling.assert_not_called()
