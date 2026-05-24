"""Tests for number.py — ZoneCutHeightNumber and async_setup_entry."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lymow.number import GeofenceRadiusNumber, ZoneCutHeightNumber, async_setup_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}
HASH_ID = "aabbccdd"
HASH_ID2 = "11223344"

_ZONE = {"hashId": HASH_ID, "cutHeight": 60, "isEnabled": True, "area": 12.0}
_ZONE2 = {"hashId": HASH_ID2, "cutHeight": 40, "isEnabled": False, "area": 8.0}


def _make_coord(state: dict | None = None) -> MagicMock:
    coord = MagicMock()
    coord.data = {THING: state or {}}
    coord.devices = [DEVICE]
    coord.async_update_zone_cut_height = AsyncMock()
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def _make_entity(zone: dict | None = None) -> ZoneCutHeightNumber:
    state = {"mapData": {"goZones": [zone or _ZONE]}}
    coord = _make_coord(state)
    return ZoneCutHeightNumber(coord, DEVICE, HASH_ID)


# ---------------------------------------------------------------------------
# ZoneCutHeightNumber init
# ---------------------------------------------------------------------------


def test_unique_id() -> None:
    e = _make_entity()
    assert e._attr_unique_id == f"{THING}_{HASH_ID}_cut_height"


def test_name() -> None:
    e = _make_entity()
    assert HASH_ID[:4] in e._attr_name
    assert "Cut Height" in e._attr_name


def test_native_constraints() -> None:
    e = _make_entity()
    assert e._attr_native_min_value == 20
    assert e._attr_native_max_value == 100
    assert e._attr_native_step == 1


# ---------------------------------------------------------------------------
# _zone property
# ---------------------------------------------------------------------------


def test_zone_found() -> None:
    e = _make_entity()
    assert e._zone is not None
    assert e._zone["hashId"] == HASH_ID


def test_zone_not_found() -> None:
    coord = _make_coord({})  # no mapData
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)
    assert e._zone is None


def test_zone_wrong_hash() -> None:
    state = {"mapData": {"goZones": [_ZONE2]}}
    coord = _make_coord(state)
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)  # HASH_ID != HASH_ID2
    assert e._zone is None


# ---------------------------------------------------------------------------
# available
# ---------------------------------------------------------------------------


def test_available_when_zone_found() -> None:
    e = _make_entity()
    assert e.available is True


def test_not_available_when_zone_missing() -> None:
    coord = _make_coord({})
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)
    assert e.available is False


# ---------------------------------------------------------------------------
# native_value
# ---------------------------------------------------------------------------


def test_native_value_returns_cut_height() -> None:
    e = _make_entity({"hashId": HASH_ID, "cutHeight": 55})
    assert e.native_value == 55.0


def test_native_value_none_when_cut_height_absent() -> None:
    e = _make_entity({"hashId": HASH_ID})  # no cutHeight
    assert e.native_value is None


def test_native_value_none_when_no_zone() -> None:
    coord = _make_coord({})
    e = ZoneCutHeightNumber(coord, DEVICE, HASH_ID)
    assert e.native_value is None


# ---------------------------------------------------------------------------
# async_set_native_value
# ---------------------------------------------------------------------------


async def test_set_native_value_calls_coordinator() -> None:
    e = _make_entity()
    await e.async_set_native_value(75.0)
    e.coordinator.async_update_zone_cut_height.assert_called_once_with(THING, HASH_ID, 75)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_no_zones_initially() -> None:
    """With no mapData, no ZONE entities — geofence-radius still adds once per device."""
    from lymow.const import DOMAIN

    coord = _make_coord({})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))
    zone_entities = [e for e in added if isinstance(e, ZoneCutHeightNumber)]
    assert zone_entities == []


async def test_async_setup_entry_creates_entities_for_zones() -> None:
    from lymow.const import DOMAIN

    coord = _make_coord({"mapData": {"goZones": [_ZONE, _ZONE2]}})
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    zone_entities = [e for e in added if isinstance(e, ZoneCutHeightNumber)]
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
    """Listener callback dynamically adds new zone entities when data updates."""
    from lymow.const import DOMAIN

    coord = _make_coord({})  # start with no zones
    coord.devices = [DEVICE]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    captured_callback = None
    captured_add = None

    def _register_listener(cb):
        nonlocal captured_callback
        captured_callback = cb
        return lambda: None  # unsubscribe no-op

    coord.async_add_listener.side_effect = _register_listener

    added: list = []

    def _add(entities):
        nonlocal captured_add
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)
    zone_entities = [e for e in added if isinstance(e, ZoneCutHeightNumber)]
    assert zone_entities == []  # no zones yet (geofence-radius doesn't count)

    # Simulate coordinator data update with zones
    coord.data = {THING: {"mapData": {"goZones": [_ZONE]}}}
    captured_callback()

    zone_entities = [e for e in added if isinstance(e, ZoneCutHeightNumber)]
    assert len(zone_entities) == 1


# ---------------------------------------------------------------------------
# GeofenceRadiusNumber
# ---------------------------------------------------------------------------


def _make_radius_coord(geofence: list | None = None) -> MagicMock:
    coord = MagicMock()
    coord.devices = [DEVICE]
    coord.data = {THING: {"geoFence": geofence} if geofence is not None else {}}
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    coord.async_set_geofence_radius = AsyncMock()
    return coord


def test_geofence_radius_unique_id_and_name() -> None:
    coord = _make_radius_coord([{"name": "", "latitude": 0.0, "longitude": 0.0, "radius": 150}])
    e = GeofenceRadiusNumber(coord, DEVICE)
    assert e._attr_unique_id == f"{THING}_geofence_radius"
    assert "Geofence radius" in e._attr_name


def test_geofence_radius_native_value() -> None:
    coord = _make_radius_coord([{"name": "", "latitude": 0.0, "longitude": 0.0, "radius": 175}])
    e = GeofenceRadiusNumber(coord, DEVICE)
    assert e.native_value == 175.0


def test_geofence_radius_native_value_none_when_no_geofence() -> None:
    coord = _make_radius_coord(None)
    e = GeofenceRadiusNumber(coord, DEVICE)
    assert e.native_value is None
    assert e.available is False


def test_geofence_radius_native_value_none_when_empty_list() -> None:
    coord = _make_radius_coord([])
    e = GeofenceRadiusNumber(coord, DEVICE)
    assert e.native_value is None
    assert e.available is False


def test_geofence_radius_native_value_none_when_radius_missing() -> None:
    coord = _make_radius_coord([{"name": "", "latitude": 0.0, "longitude": 0.0}])
    e = GeofenceRadiusNumber(coord, DEVICE)
    assert e.native_value is None


def test_geofence_radius_native_value_none_when_first_entry_not_dict() -> None:
    coord = _make_radius_coord(["not-a-dict"])
    e = GeofenceRadiusNumber(coord, DEVICE)
    assert e.native_value is None


async def test_geofence_radius_set_native_value() -> None:
    coord = _make_radius_coord([{"name": "", "latitude": 0.0, "longitude": 0.0, "radius": 150}])
    e = GeofenceRadiusNumber(coord, DEVICE)
    await e.async_set_native_value(200)
    coord.async_set_geofence_radius.assert_awaited_once_with(THING, 200)


async def test_async_setup_entry_registers_geofence_radius_per_device() -> None:
    from lymow.const import DOMAIN

    coord = _make_radius_coord([{"name": "", "latitude": 0.0, "longitude": 0.0, "radius": 150}])
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    radius_entities = [e for e in added if isinstance(e, GeofenceRadiusNumber)]
    assert len(radius_entities) == 1
    assert radius_entities[0]._thing_name == THING


# ---------------------------------------------------------------------------
# RtkPauseThresholdNumber — pure coordinator-state knob, no REST call
# ---------------------------------------------------------------------------


def test_rtk_pause_threshold_native_value_reads_coordinator() -> None:
    from lymow.number import RtkPauseThresholdNumber

    coord = MagicMock()
    coord.get_rtk_guard_threshold = MagicMock(return_value=2)
    e = RtkPauseThresholdNumber(coord, DEVICE)
    assert e.native_value == 2.0


def test_rtk_pause_threshold_bounds_disabled_default() -> None:
    from lymow.number import RtkPauseThresholdNumber

    coord = MagicMock()
    coord.get_rtk_guard_threshold = MagicMock(return_value=1)
    e = RtkPauseThresholdNumber(coord, DEVICE)
    assert e._attr_native_min_value == 0
    assert e._attr_native_max_value == 3
    assert e._attr_entity_registry_enabled_default is False
    assert e._attr_unique_id == f"{THING}_rtk_pause_threshold"


async def test_rtk_pause_threshold_set_writes_coordinator() -> None:
    from lymow.number import RtkPauseThresholdNumber

    coord = MagicMock()
    coord.get_rtk_guard_threshold = MagicMock(return_value=1)
    e = RtkPauseThresholdNumber(coord, DEVICE)
    e.async_write_ha_state = MagicMock()
    await e.async_set_native_value(2)
    coord.set_rtk_guard_threshold.assert_called_once_with(THING, 2)


async def test_async_setup_entry_registers_rtk_threshold_per_device() -> None:
    from lymow.const import DOMAIN
    from lymow.number import RtkPauseThresholdNumber

    coord = _make_radius_coord([{"name": "", "latitude": 0.0, "longitude": 0.0, "radius": 150}])
    coord.get_rtk_guard_threshold = MagicMock(return_value=1)
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    threshold_entities = [e for e in added if isinstance(e, RtkPauseThresholdNumber)]
    assert len(threshold_entities) == 1


# ---------------------------------------------------------------------------
# MowerVolumeNumber — robotConfig.audioVolume (int 0..100, slider)
# ---------------------------------------------------------------------------


def _make_volume_coord(robot_config: dict | None) -> MagicMock:
    from unittest.mock import AsyncMock

    coord = MagicMock()
    coord.data = {THING: {"robotConfig": dict(robot_config)} if robot_config is not None else {}}
    coord.devices = [DEVICE]
    coord.async_set_robot_config = AsyncMock()
    coord.get_rtk_guard_threshold = MagicMock(return_value=1)
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    return coord


def test_volume_number_native_value_reads_robot_config() -> None:
    from lymow.number import MowerVolumeNumber

    e = MowerVolumeNumber(_make_volume_coord({"audioVolume": 65}), DEVICE)
    assert e.native_value == 65.0
    assert e._attr_unique_id == f"{THING}_audio_volume"
    assert "Volume" in e._attr_name


def test_volume_number_unknown_when_missing_or_out_of_range() -> None:
    from lymow.number import MowerVolumeNumber

    assert MowerVolumeNumber(_make_volume_coord(None), DEVICE).native_value is None
    assert MowerVolumeNumber(_make_volume_coord({}), DEVICE).native_value is None
    # Untrusted wire data: out-of-range falls back to unknown rather than clamping
    assert MowerVolumeNumber(_make_volume_coord({"audioVolume": -5}), DEVICE).native_value is None
    assert MowerVolumeNumber(_make_volume_coord({"audioVolume": 250}), DEVICE).native_value is None


async def test_volume_number_set_publishes_robot_config_int() -> None:
    from lymow.number import MowerVolumeNumber

    coord = _make_volume_coord({"audioVolume": 30})
    await MowerVolumeNumber(coord, DEVICE).async_set_native_value(80)
    coord.async_set_robot_config.assert_awaited_once_with(THING, audioVolume=80)


async def test_async_setup_entry_registers_volume_per_device() -> None:
    from lymow.const import DOMAIN
    from lymow.number import MowerVolumeNumber

    coord = _make_volume_coord({"audioVolume": 50})
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    entry = MagicMock()
    entry.entry_id = "entry-1"

    added: list = []
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    volume_entities = [e for e in added if isinstance(e, MowerVolumeNumber)]
    assert len(volume_entities) == 1
    assert volume_entities[0]._thing_name == THING
