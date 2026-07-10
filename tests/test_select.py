"""Tests for select.py — Device Settings dropdowns."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import _load_lymow_module

_load_lymow_module("select")

from lymow.const import CHARGING_MODES, CLEAN_MODES, DOMAIN, ZONE_ORDERS  # noqa: E402
from lymow.protocol import _encode_zone_config_submessage, decode_zone_config  # noqa: E402
from lymow.select import (  # noqa: E402
    _CHARGING_MODE_OPTIONS,
    _MOW_PATTERN_OPTIONS,
    _ZONE_ORDER_OPTIONS,
    ChargingModeSelect,
    MowPatternSelect,
    ZoneOrderSelect,
    async_setup_entry,
)

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Test Mower"}


def _make_coord(task_config: dict | None = None) -> MagicMock:
    coord = MagicMock()
    state: dict = {"mapData": {}}
    if task_config is not None:
        state["mapData"]["taskConfig"] = task_config
    coord.data = {THING: state}
    coord.devices = [DEVICE]
    coord.async_set_device_settings = AsyncMock()
    return coord


# ---------------------------------------------------------------------------
# ChargingModeSelect
# ---------------------------------------------------------------------------


def test_charging_mode_unique_id_and_options() -> None:
    e = ChargingModeSelect(_make_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_charging_mode"
    assert e._attr_name == "Return-to-dock route"
    assert e._attr_options == ["Follow perimeter", "Direct route"]


def test_charging_mode_reads_current_value() -> None:
    e = ChargingModeSelect(_make_coord({"chargingMode": 1}), DEVICE)
    assert e.current_option == "Direct route"
    e2 = ChargingModeSelect(_make_coord({"chargingMode": 0}), DEVICE)
    assert e2.current_option == "Follow perimeter"


def test_charging_mode_defaults_to_option0_when_missing_unknown_when_invalid() -> None:
    # Proto3: absent field == enum default 0 → option 0, not unknown.
    assert ChargingModeSelect(_make_coord(), DEVICE).current_option == "Follow perimeter"
    assert ChargingModeSelect(_make_coord({}), DEVICE).current_option == "Follow perimeter"
    # Unknown future enum value (present but unmapped) → unknown.
    assert ChargingModeSelect(_make_coord({"chargingMode": 99}), DEVICE).current_option is None
    # Non-int (hostile decode) → unknown.
    assert ChargingModeSelect(_make_coord({"chargingMode": "1"}), DEVICE).current_option is None


def test_charging_mode_defaults_to_option0_when_coordinator_data_none() -> None:
    coord = _make_coord({"chargingMode": 1})
    coord.data = None
    assert ChargingModeSelect(coord, DEVICE).current_option == "Follow perimeter"


async def test_charging_mode_select_option_calls_coordinator() -> None:
    coord = _make_coord({"chargingMode": 0})
    e = ChargingModeSelect(coord, DEVICE)
    await e.async_select_option("Direct route")
    coord.async_set_device_settings.assert_awaited_once_with(THING, charging_mode=1)


# ---------------------------------------------------------------------------
# ZoneOrderSelect
# ---------------------------------------------------------------------------


def test_zone_order_unique_id_and_options() -> None:
    e = ZoneOrderSelect(_make_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_zone_order"
    assert e._attr_name == "Zone order"
    assert e._attr_options == ["Optimize", "Custom"]


def test_zone_order_reads_current_value() -> None:
    assert ZoneOrderSelect(_make_coord({"zoneOrder": 0}), DEVICE).current_option == "Optimize"
    assert ZoneOrderSelect(_make_coord({"zoneOrder": 1}), DEVICE).current_option == "Custom"


async def test_zone_order_select_option_calls_coordinator() -> None:
    coord = _make_coord({"zoneOrder": 0})
    await ZoneOrderSelect(coord, DEVICE).async_select_option("Custom")
    coord.async_set_device_settings.assert_awaited_once_with(THING, zone_order=1)


# ---------------------------------------------------------------------------
# Label/wire-enum drift guards — labels must map 1:1 to the const tables that
# const.py + the protocol encoder use.
# ---------------------------------------------------------------------------


def test_charging_mode_option_values_match_const_table() -> None:
    assert set(_CHARGING_MODE_OPTIONS.values()) == set(CHARGING_MODES)


def test_zone_order_option_values_match_const_table() -> None:
    assert set(_ZONE_ORDER_OPTIONS.values()) == set(ZONE_ORDERS)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_adds_all_selects_per_device() -> None:

    coord = _make_coord({"chargingMode": 0, "zoneOrder": 0})
    coord.async_set_robot_config = AsyncMock()
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    types = {type(e).__name__ for e in added}
    assert types == {
        "ChargingModeSelect",
        "ZoneOrderSelect",
        "CameraLightSelect",
        "MowPatternSelect",
        "BackupMapRestoreSelect",
    }


async def test_async_setup_entry_skips_when_no_devices() -> None:
    coord = _make_coord()
    coord.devices = []
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    called = False

    def _add(entities):
        nonlocal called
        called = True

    await async_setup_entry(hass, entry, _add)
    assert called is False


# ---------------------------------------------------------------------------
# CameraLightSelect — write-optimistic, no decoded read-back available
# ---------------------------------------------------------------------------


def _make_camera_coord() -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: {}}
    coord.devices = [DEVICE]
    coord.async_set_robot_config = AsyncMock()
    return coord


def test_camera_light_select_metadata_and_unknown_initially() -> None:
    from lymow.select import CameraLightSelect

    e = CameraLightSelect(_make_camera_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_camera_light"
    assert e._attr_name == "Camera light"
    assert e._attr_options == ["Off", "Low", "Medium", "High"]
    assert e._attr_entity_registry_enabled_default is False
    # Write-optimistic: no decoded brightness in pboutput → unknown until first press.
    assert e.current_option is None


async def test_camera_light_select_each_option_publishes_matching_signal() -> None:
    from lymow.const import (
        SIGNAL_TURN_OFF_CAMERA_LIGHT,
        SIGNAL_TURN_ON_CAMERA_LIGHT,
        SIGNAL_TURN_ON_CAMERA_LIGHT_LOW,
        SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE,
    )
    from lymow.select import CameraLightSelect

    cases = [
        ("Off", SIGNAL_TURN_OFF_CAMERA_LIGHT),
        ("Low", SIGNAL_TURN_ON_CAMERA_LIGHT_LOW),
        ("Medium", SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE),
        ("High", SIGNAL_TURN_ON_CAMERA_LIGHT),
    ]
    for label, signal in cases:
        coord = _make_camera_coord()
        e = CameraLightSelect(coord, DEVICE)
        e.async_write_ha_state = MagicMock()
        await e.async_select_option(label)
        coord.async_set_robot_config.assert_awaited_once_with(THING, signal=signal)
        assert e.current_option == label  # last choice sticks


# ---------------------------------------------------------------------------
# MowPatternSelect — globalZoneConfig.cleanMode read + async_set_task_config write
# ---------------------------------------------------------------------------


def _make_pattern_coord(global_zone_config: dict | None = None) -> MagicMock:
    coord = MagicMock()
    state: dict = {"mapData": {}}
    if global_zone_config is not None:
        state["mapData"]["globalZoneConfig"] = global_zone_config
    coord.data = {THING: state}
    coord.devices = [DEVICE]
    coord.async_set_task_config = AsyncMock()
    return coord


def _decoded_gzc(clean_mode: int) -> dict:
    """Build a globalZoneConfig dict through the real codec (no hand-rolled dict)."""
    return decode_zone_config(_encode_zone_config_submessage({"cleanMode": clean_mode}))


def test_mow_pattern_unique_id_and_options() -> None:
    e = MowPatternSelect(_make_pattern_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_mow_pattern"
    assert e._attr_name == "Mowing pattern"
    assert e._attr_options == ["Zigzag", "Adaptive zigzag", "Chessboard", "Perimeter laps only"]


def test_mow_pattern_reads_current_value() -> None:
    for code, label in ((1, "Zigzag"), (2, "Adaptive zigzag"), (3, "Chessboard"), (4, "Perimeter laps only")):
        e = MowPatternSelect(_make_pattern_coord(_decoded_gzc(code)), DEVICE)
        assert e.current_option == label


def test_mow_pattern_unknown_when_absent_zero_or_invalid() -> None:
    assert MowPatternSelect(_make_pattern_coord(), DEVICE).current_option is None  # no globalZoneConfig
    assert MowPatternSelect(_make_pattern_coord({}), DEVICE).current_option is None  # empty
    assert MowPatternSelect(_make_pattern_coord(_decoded_gzc(0)), DEVICE).current_option is None  # NONE
    assert MowPatternSelect(_make_pattern_coord({"cleanMode": 99}), DEVICE).current_option is None  # future code
    assert MowPatternSelect(_make_pattern_coord({"cleanMode": "x"}), DEVICE).current_option is None  # non-int


def test_mow_pattern_current_option_tolerates_non_dict_config() -> None:
    # Malformed decode (non-dict mapData / globalZoneConfig / cleanMode) -> unknown, no raise.
    for bad_state in (
        {"mapData": "x"},
        {"mapData": {"globalZoneConfig": ["y"]}},
        {"mapData": {"globalZoneConfig": None}},
    ):
        coord = MagicMock()
        coord.data = {THING: bad_state}
        assert MowPatternSelect(coord, DEVICE).current_option is None


async def test_mow_pattern_select_option_calls_coordinator() -> None:
    coord = _make_pattern_coord()
    e = MowPatternSelect(coord, DEVICE)
    await e.async_select_option("Adaptive zigzag")
    coord.async_set_task_config.assert_awaited_once_with(THING, cleanMode=2)


def test_mow_pattern_option_values_match_const_table() -> None:
    # Labels map to the canonical CLEAN_MODES codes; 0=NONE is intentionally not offered.
    assert set(_MOW_PATTERN_OPTIONS.values()) == {1, 2, 3, 4}
    for value in _MOW_PATTERN_OPTIONS.values():
        assert value in CLEAN_MODES


# ---------------------------------------------------------------------------
# BackupMapRestoreSelect — action select: options are backups, pick to restore
# ---------------------------------------------------------------------------


def _make_backup_coord(backup_list: list | None = None) -> MagicMock:
    coord = MagicMock()
    state: dict = {}
    if backup_list is not None:
        state["backupMapList"] = backup_list
    coord.data = {THING: state}
    coord.devices = [DEVICE]
    coord.async_restore_backup_map = AsyncMock()
    return coord


def test_backup_restore_metadata_and_empty_options() -> None:
    from lymow.select import BackupMapRestoreSelect

    e = BackupMapRestoreSelect(_make_backup_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_restore_backup_map"
    assert e._attr_name == "Restore backup map"
    assert e._attr_entity_registry_enabled_default is False
    assert e.options == []  # no backups yet
    assert e.current_option is None  # action select — never a persistent value


def test_backup_restore_options_label_priority_and_uniqueness() -> None:
    from lymow.select import BackupMapRestoreSelect

    entries = [
        {"file": "dev/map/a.pb", "name": "Spring", "backupTime": 1_700_000_000},
        {"file": "dev/map/b.pb", "name": "", "backupTime": 1_700_000_000},  # -> timestamp label
        {"file": "dev/map/c.pb", "name": "Spring", "backupTime": 1_700_100_000},  # dup name -> " (2)"
        {"file": "", "name": "ignored"},  # no file -> skipped
    ]
    e = BackupMapRestoreSelect(_make_backup_coord(entries), DEVICE)
    opts = e.options
    # Duplicate "Spring" disambiguated by a STABLE file-basename discriminator, not an ordinal.
    assert "Spring · a.pb" in opts and "Spring · c.pb" in opts
    assert any("UTC" in o for o in opts)  # blank-name entry falls back to its timestamp
    assert len(opts) == 3  # the file-less entry is skipped


async def test_backup_restore_duplicate_label_resolves_to_correct_file() -> None:
    from lymow.select import BackupMapRestoreSelect

    entries = [{"file": "dev/map/a.pb", "name": "Spring"}, {"file": "dev/map/c.pb", "name": "Spring"}]
    coord = _make_backup_coord(entries)
    await BackupMapRestoreSelect(coord, DEVICE).async_select_option("Spring · c.pb")
    coord.async_restore_backup_map.assert_awaited_once_with(THING, "dev/map/c.pb")


def test_backup_restore_tolerates_malformed_name_and_file() -> None:
    from lymow.select import BackupMapRestoreSelect

    entries = [
        {"file": "dev/map/a.pb", "name": 123},  # non-string name -> basename fallback, still listed
        {"file": 999, "name": "bad-file"},  # non-string file -> skipped
        {"file": "   ", "name": "blank-file"},  # blank file -> skipped
        "not-a-dict",  # non-dict entry -> skipped
    ]
    assert BackupMapRestoreSelect(_make_backup_coord(entries), DEVICE).options == ["a.pb"]


def test_backup_restore_last_resort_suffix_when_name_and_basename_both_collide() -> None:
    """Collision loop (lines 244-245): same display-name AND same file basename in two dirs."""
    from lymow.select import BackupMapRestoreSelect

    entries = [
        {"file": "dir1/map.pb", "name": "Spring"},
        {"file": "dir2/map.pb", "name": "Spring"},  # same name + same basename -> fallback ordinal
    ]
    opts = BackupMapRestoreSelect(_make_backup_coord(entries), DEVICE).options
    # Both must appear; the second gets an ordinal suffix since the basename discriminator also collides.
    assert "Spring · map.pb" in opts
    assert "Spring · map.pb (2)" in opts
    assert len(opts) == 2


async def test_backup_restore_blocks_select_navigation() -> None:
    from homeassistant.exceptions import HomeAssistantError
    from lymow.select import BackupMapRestoreSelect

    coord = _make_backup_coord([{"file": "dev/map/a.pb", "name": "Spring"}])
    e = BackupMapRestoreSelect(coord, DEVICE)
    for nav in (e.async_first, e.async_last, e.async_next, e.async_previous):
        with pytest.raises(HomeAssistantError):
            await nav()
    coord.async_restore_backup_map.assert_not_called()


async def test_backup_restore_select_restores_matching_file() -> None:
    from lymow.select import BackupMapRestoreSelect

    entries = [{"file": "dev/map/a.pb", "name": "Spring", "backupTime": 1}]
    coord = _make_backup_coord(entries)
    e = BackupMapRestoreSelect(coord, DEVICE)
    await e.async_select_option("Spring")
    coord.async_restore_backup_map.assert_awaited_once_with(THING, "dev/map/a.pb")


async def test_backup_restore_select_unknown_label_raises() -> None:
    from homeassistant.exceptions import HomeAssistantError
    from lymow.select import BackupMapRestoreSelect

    coord = _make_backup_coord([{"file": "dev/map/a.pb", "name": "Spring"}])
    e = BackupMapRestoreSelect(coord, DEVICE)
    with pytest.raises(HomeAssistantError):
        await e.async_select_option("Gone")
    coord.async_restore_backup_map.assert_not_called()


def test_backup_label_falls_back_to_file_basename() -> None:
    from lymow.select import _backup_label

    # No (usable) name, no timestamp -> the file's basename; nothing at all -> "Backup <n>".
    assert _backup_label({"file": "dev/map/summer.pb"}, 0) == "summer.pb"
    assert _backup_label({"name": 5, "file": "dev/map/summer.pb"}, 0) == "summer.pb"  # non-string name ignored
    assert _backup_label({}, 4) == "Backup 5"
    # Unparseable backupTime (bad type/value) -> fall through to file basename.
    assert _backup_label({"file": "dev/map/bad.pb", "backupTime": "not-a-number"}, 0) == "bad.pb"
