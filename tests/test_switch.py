"""Tests for switch.py — ZoneEnabledSwitch, device-feature switches, and async_setup_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lymow.switch import (
    FindRobotSwitch,
    TheftDetectionSwitch,
    TheftLockSwitch,
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
    assert HASH_ID[:4] in e._attr_name
    assert "Mower 1" in e._attr_name


def test_name_fallback_sn() -> None:
    coord = _make_coord({"mapData": {"goZones": [_ZONE_ON]}})
    e = ZoneEnabledSwitch(coord, {"deviceThingName": THING, "sn": "SN1"}, HASH_ID)
    assert "SN1" in e._attr_name


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


def test_mobile_notification_switch_is_off_when_value_is_unknown_int() -> None:
    """Anything that isn't the observed ON value (2) is treated as off, since
    we haven't seen any other state on the wire."""
    from lymow.switch import MobileNotificationSwitch

    coord = _make_feature_coord({"mobileNotificationSwitch": 1})
    e = MobileNotificationSwitch(coord, DEVICE)
    assert e.is_on is False


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
