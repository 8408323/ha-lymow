"""Lymow lawn mower entity."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import async_discovered_service_info
from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ANGULAR,
    ATTR_DURATION,
    ATTR_LINEAR,
    BLE_DRIVE_ANGULAR_MAX,
    BLE_DRIVE_LINEAR_MAX,
    BLE_DRIVE_MAX_DURATION_S,
    CHARGING_MODES,
    CONF_BLE_ADDRESS,
    DOMAIN,
    SERVICE_BLE_DRIVE,
    WORK_STATUS_DOCKED_GROUP,
    WORK_STATUS_ERROR_GROUP,
    WORK_STATUS_MOWING_GROUP,
    WORK_STATUS_OFFLINE,
    WORK_STATUS_PAUSED_GROUP,
    WORK_STATUS_RETURNING_GROUP,
    ZONE_ORDERS,
)
from .coordinator import LymowCoordinator
from .entity import lymow_device_info

_LOGGER = logging.getLogger(__name__)


def _discover_ble_address(hass: HomeAssistant, ble_name: str) -> str | None:
    """Find the robot's BLE address by its advertised name (e.g. 'Lymow_7B6521').

    Lets manual drive work without the user hand-entering a MAC, using the
    bluetooth integration's already-discovered devices. Returns None if the
    robot isn't currently in range / advertising.
    """
    if not ble_name:
        return None
    for info in async_discovered_service_info(hass, connectable=True):
        if info.name == ble_name:
            return info.address
    return None


_SERVICE_DELETE_ZONE = "delete_zone"
_SERVICE_DELETE_CHANNEL = "delete_channel"
_SERVICE_DELETE_NOGO_ZONE = "delete_nogo_zone"
_ATTR_ZONE_HASH_ID = "zone_hash_id"
_ATTR_CHANNEL_HASH_ID = "channel_hash_id"
_ATTR_NOGO_HASH_ID = "nogo_hash_id"
_SERVICE_START_ZONE = "start_zone"
_ATTR_ZONE_HASH_IDS = "zone_hash_ids"
_SERVICE_PAUSE = "pause"
_SERVICE_QUERY_MAP = "query_map"
_SERVICE_RESUME = "resume"
_SERVICE_QUERY_SCHEDULES = "query_schedules"
_SERVICE_START_VIDEO_SESSION = "start_video_session"
_SERVICE_UPDATE_ZONE_POLYGON = "update_zone_polygon"
_SERVICE_ADD_ZONE = "add_zone"
_SERVICE_MERGE_ZONES = "merge_zones"
_SERVICE_PIN_AND_GO = "pin_and_go"
_SERVICE_SPLIT_ZONE = "split_zone"
_SERVICE_RENAME_ZONE = "rename_zone"
_SERVICE_CLEAR_SCHEDULES = "clear_schedules"
_SERVICE_SET_SCHEDULES = "set_schedules"
_SERVICE_SET_TASK_CONFIG = "set_task_config"
_SERVICE_SET_RUN_TIME_CONFIG = "set_run_time_config"
_SERVICE_SET_NETWORK_PRIORITY = "set_network_priority"
_SERVICE_SET_RECHARGE_RESUME = "set_recharge_resume"
_SERVICE_SET_DEVICE_SETTINGS = "set_device_settings"
_SERVICE_SET_DEVICE_NAME = "set_device_name"
_ATTR_PREFERRED = "preferred"
_ATTR_RR_ENABLE = "enable"
_ATTR_RR_PERIOD_START = "period_start"
_ATTR_RR_PERIOD_END = "period_end"
_ATTR_RR_RECHARGE_BAT = "recharge_bat"
_ATTR_RR_RESUME_BAT = "resume_bat"
_ATTR_DS_CHARGING_MODE = "charging_mode"
_ATTR_DS_ZONE_ORDER = "zone_order"
_ATTR_DS_RAINY_MOWING = "rainy_mowing"
_ATTR_DS_CHARGING_HANDBRAKE = "charging_handbrake"


def _service_label(name: str) -> str:
    """Map a const-style enum name (NORMAL / QUICK / etc.) to its HA-service
    choice label. We use the app's UI sense — "follow_perimeter" / "direct_route"
    / "optimize" / "custom" — rather than the raw APK enum names, which include
    quirks like the (sic) CHARING_MODE typo."""
    return {
        "NORMAL": "follow_perimeter",
        "QUICK": "direct_route",
        "OPTIMIZE": "optimize",
        "CUSTOM": "custom",
    }[name]


# Service-side choice → wire int, derived from the pinned const enums so the
# two stay in lockstep (CHARGING_MODES and ZONE_ORDERS).
_CHARGING_MODE_CHOICES = {_service_label(name): value for value, name in CHARGING_MODES.items()}
_ZONE_ORDER_CHOICES = {_service_label(name): value for value, name in ZONE_ORDERS.items()}

# Service-field (snake_case) → PbTaskConfig field (camelCase). A safe, intuitive
# subset of PbTaskConfig; the encoder supports more. ``move_speed`` is a float
# (m/s); the rest are ints. ``raise_cut_height``/``lower_cut_height`` and the
# bool toggles (``path_order``/``line_follow_mode``) are coerced via cv.boolean
# (see _TASK_CONFIG_BOOL_FIELDS below) and emitted as varints by the encoder.
_TASK_CONFIG_SERVICE_FIELDS = {
    "move_speed": "moveSpeed",
    "path_spacing": "pathSpacing",
    "perimeter_mow_laps": "perimeterMowLaps",
    "perimeter_mow_dir": "perimeterMowDir",
    "nogo_mow_laps": "noGoMowLaps",
    "cut_speed": "cutSpeed",
    "brush_speed": "brushSpeed",
    "obs_dec_mode": "obsDecMode",
    "clean_mode": "cleanMode",
    "path_order": "pathOrder",
    "line_follow_mode": "lineFollowMode",
    "raise_cut_height": "raiseCutHeight",
    "lower_cut_height": "lowerCutHeight",
}
# Service fields the encoder wants as floats rather than ints.
_TASK_CONFIG_FLOAT_FIELDS = {"move_speed"}
# Bool-shaped service fields (the encoder still emits a varint, but the value
# is conceptually true/false — coerce so YAML "true"/"false" works).
_TASK_CONFIG_BOOL_FIELDS = {"path_order", "line_follow_mode", "raise_cut_height", "lower_cut_height"}

# Service-field (snake_case) → PbRunTimeConfig field (camelCase) + safe numeric
# bounds. Run-time config overrides settings on the currently-running task (vs
# set_task_config, which is the next-mow default). cut_height is mm, move_speed
# is m/s. Bounds match the documented UI selectors in services.yaml so non-UI
# callers (automations, REST) can't bypass the selector ranges and push out-of-
# range values straight to the mower.
_RUN_TIME_CONFIG_SERVICE_FIELDS = {
    "cut_height": ("cutHeight", "int", (20, 100)),
    "move_speed": ("moveSpeed", "float", (0.1, 1.5)),
    "cut_speed": ("cutSpeed", "int", (0, 1000)),
}
_SERVICE_RESTORE_BACKUP_MAP = "restore_backup_map"
_SERVICE_DELETE_BACKUP_MAP = "delete_backup_map"
_SERVICE_RENAME_BACKUP_MAP = "rename_backup_map"
_ATTR_OBJECT_KEY = "object_key"
_ATTR_POLYGON = "polygon"
_ATTR_NAME = "name"
_ATTR_NAMES = "names"
_ATTR_CUT_HEIGHT_MM = "cut_height_mm"
_ATTR_X = "x"
_ATTR_Y = "y"
_ATTR_RADIUS_M = "radius_m"
_ATTR_CUT_P1 = "cut_p1"
_ATTR_CUT_P2 = "cut_p2"
_ATTR_SCHEDULES = "schedules"

_DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _to_day_int(value: Any) -> int:
    """Coerce a day-of-week given as 0-6 or a SUN..SAT name to its int value."""
    if isinstance(value, str):
        key = value.strip().lower()[:3]
        if key in _DAY_NAMES:
            return _DAY_NAMES[key]
    try:
        day = int(value)
    except (TypeError, ValueError):
        raise vol.Invalid("day_of_week must be 0-6 (Sun-Sat) or a weekday name") from None
    if not 0 <= day <= 6:
        raise vol.Invalid("day_of_week must be 0-6 (Sun-Sat) or a weekday name")
    return day


def _to_hour_minute(value: Any) -> tuple[int, int]:
    """Accept ``"H:MM"`` or ``"HH:MM"`` (24-hour) and return a bounded (hour, minute) tuple."""
    if not isinstance(value, str):
        raise vol.Invalid("must be a 24-hour time string like H:MM or HH:MM")
    stripped = value.strip()
    if ":" not in stripped:
        raise vol.Invalid("must be a 24-hour time string like H:MM or HH:MM")
    h_s, m_s = stripped.split(":", 1)
    try:
        hour, minute = int(h_s), int(m_s)
    except ValueError:
        raise vol.Invalid("must be a 24-hour time string like H:MM or HH:MM") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise vol.Invalid("hour must be 0-23 and minute 0-59")
    return hour, minute


# Read-only diagnostic queries: each publishes a bare userCtrl=<code> pbinput.
# The robot's pboutput reply is handled by decode_pboutput; new field decoders
# land in a separate slice (issue #40).
_QUERY_SERVICES: tuple[tuple[str, str], ...] = (
    ("query_cleaning_info", "async_query_cleaning_info"),
    ("query_cleaning_summary", "async_query_cleaning_summary"),
    ("query_robot_config", "async_query_robot_config"),
    ("query_path", "async_query_path"),
    ("query_channels", "async_query_channels"),
    ("query_run_time_config", "async_query_run_time_config"),
    ("query_wifi_4g", "async_query_wifi_4g"),
    ("query_net_detail", "async_query_net_detail"),
    ("query_rtk_diagnostic_l1", "async_query_rtk_diagnostic_l1"),
    ("query_rtk_diagnostic_l2", "async_query_rtk_diagnostic_l2"),
)

_ENTITY_ID_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids})
_DELETE_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_ID): cv.string,
    }
)
_DELETE_CHANNEL_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_CHANNEL_HASH_ID): cv.string,
    }
)
_DELETE_NOGO_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_NOGO_HASH_ID): cv.string,
    }
)
_START_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_IDS): vol.All(cv.ensure_list, [cv.string]),
    }
)
_MERGE_ZONES_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_IDS): vol.All(cv.ensure_list, [cv.string], vol.Length(min=2)),
        vol.Optional(_ATTR_NAME, default=""): cv.string,
        vol.Optional(_ATTR_CUT_HEIGHT_MM): vol.All(vol.Coerce(int), vol.Range(min=20, max=100)),
    }
)
_POINT_SCHEMA = vol.Schema(
    {
        vol.Required("x"): vol.Coerce(float),
        vol.Required("y"): vol.Coerce(float),
    },
    extra=vol.ALLOW_EXTRA,
)
_SPLIT_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_ID): cv.string,
        vol.Required(_ATTR_CUT_P1): _POINT_SCHEMA,
        vol.Required(_ATTR_CUT_P2): _POINT_SCHEMA,
        vol.Optional(_ATTR_NAMES, default=["", ""]): vol.All(cv.ensure_list, [cv.string], vol.Length(min=2, max=2)),
    }
)

# A polygon vertex is {"x": float, "y": float} in the robot's local ENU frame.
_POINT_SCHEMA = vol.Schema(
    {
        vol.Required("x"): vol.Coerce(float),
        vol.Required("y"): vol.Coerce(float),
    },
    extra=vol.ALLOW_EXTRA,
)
_UPDATE_ZONE_POLYGON_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_ID): cv.string,
        vol.Required(_ATTR_POLYGON): vol.All([_POINT_SCHEMA], vol.Length(min=3)),
    }
)
_ADD_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_POLYGON): vol.All([_POINT_SCHEMA], vol.Length(min=3)),
        vol.Optional(_ATTR_NAME, default=""): cv.string,
        vol.Optional(_ATTR_CUT_HEIGHT_MM, default=40): vol.All(vol.Coerce(int), vol.Range(min=20, max=100)),
    }
)
_PIN_AND_GO_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_X): vol.Coerce(float),
        vol.Required(_ATTR_Y): vol.Coerce(float),
        vol.Optional(_ATTR_RADIUS_M, default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=20.0)),
        vol.Optional(_ATTR_CUT_HEIGHT_MM, default=40): vol.All(vol.Coerce(int), vol.Range(min=20, max=100)),
        vol.Optional(_ATTR_NAME, default=""): cv.string,
    }
)
_RENAME_ZONE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_ZONE_HASH_ID): cv.string,
        vol.Required(_ATTR_NAME): cv.string,
    }
)


def _task_config_validator(field: str):
    """Per-field schema validator: float / bool / int based on its semantic kind."""
    if field in _TASK_CONFIG_FLOAT_FIELDS:
        return vol.Coerce(float)
    if field in _TASK_CONFIG_BOOL_FIELDS:
        return cv.boolean
    return vol.Coerce(int)


_SET_TASK_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        **{vol.Optional(k): _task_config_validator(k) for k in _TASK_CONFIG_SERVICE_FIELDS},
    }
)
_SET_RUN_TIME_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        **{
            vol.Optional(k): vol.All(
                vol.Coerce(float) if kind == "float" else vol.Coerce(int),
                vol.Range(min=lo, max=hi),
            )
            for k, (_proto, kind, (lo, hi)) in _RUN_TIME_CONFIG_SERVICE_FIELDS.items()
        },
    }
)
_SET_NETWORK_PRIORITY_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_PREFERRED): vol.In(("4g", "wifi")),
    }
)
_SET_DEVICE_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Optional(_ATTR_DS_CHARGING_MODE): vol.In(tuple(_CHARGING_MODE_CHOICES)),
        vol.Optional(_ATTR_DS_ZONE_ORDER): vol.In(tuple(_ZONE_ORDER_CHOICES)),
        vol.Optional(_ATTR_DS_RAINY_MOWING): cv.boolean,
        vol.Optional(_ATTR_DS_CHARGING_HANDBRAKE): cv.boolean,
    }
)
_SET_RECHARGE_RESUME_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Optional(_ATTR_RR_ENABLE): cv.boolean,
        vol.Optional(_ATTR_RR_PERIOD_START): _to_hour_minute,
        vol.Optional(_ATTR_RR_PERIOD_END): _to_hour_minute,
        vol.Optional(_ATTR_RR_RECHARGE_BAT): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional(_ATTR_RR_RESUME_BAT): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
    }
)
_SCHEDULE_ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required("hour"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Required("minute"): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
        vol.Optional("day_of_week", default=list): vol.All(cv.ensure_list, [_to_day_int]),
        vol.Optional("zones", default=list): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("repeated", default=True): cv.boolean,
        vol.Optional("disabled", default=False): cv.boolean,
    }
)
_SET_SCHEDULES_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_SCHEDULES): vol.All(cv.ensure_list, [_SCHEDULE_ENTRY_SCHEMA]),
    }
)
_SET_DEVICE_NAME_SCHEMA = vol.Schema({vol.Required("entity_id"): cv.entity_ids, vol.Required(_ATTR_NAME): cv.string})
_RESTORE_BACKUP_MAP_SCHEMA = vol.Schema(
    {vol.Required("entity_id"): cv.entity_ids, vol.Required(_ATTR_OBJECT_KEY): cv.string}
)
_DELETE_BACKUP_MAP_SCHEMA = vol.Schema(
    {vol.Required("entity_id"): cv.entity_ids, vol.Required(_ATTR_OBJECT_KEY): cv.string}
)
_RENAME_BACKUP_MAP_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(_ATTR_OBJECT_KEY): cv.string,
        vol.Required(_ATTR_NAME): cv.string,
    }
)
_BLE_DRIVE_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_ids,
        vol.Required(ATTR_LINEAR): vol.All(
            vol.Coerce(float), vol.Range(min=-BLE_DRIVE_LINEAR_MAX, max=BLE_DRIVE_LINEAR_MAX)
        ),
        vol.Required(ATTR_ANGULAR): vol.All(
            vol.Coerce(float), vol.Range(min=-BLE_DRIVE_ANGULAR_MAX, max=BLE_DRIVE_ANGULAR_MAX)
        ),
        vol.Optional(ATTR_DURATION, default=1.0): vol.All(
            vol.Coerce(float), vol.Range(min=0.0, max=BLE_DRIVE_MAX_DURATION_S)
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = list(LymowMower(coordinator, device) for device in coordinator.devices)
    async_add_entities(entities)

    async def handle_delete_zone(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_ZONE_HASH_ID]

        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            thing_name = entity._thing_name
            # Validate zone exists in cached map (best-effort — map may not be loaded yet)
            map_data = coordinator.data.get(thing_name, {}).get("mapData") or {}
            go_ids = {z.get("hashId") for z in map_data.get("goZones", [])}
            if go_ids and hash_id not in go_ids:
                raise ServiceValidationError(f"Zone {hash_id!r} not found in map. Known go zones: {sorted(go_ids)}")
            await coordinator.async_delete_zone(thing_name, hash_id)

    async def handle_delete_channel(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_CHANNEL_HASH_ID]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            thing_name = entity._thing_name
            map_data = coordinator.data.get(thing_name, {}).get("mapData") or {}
            chan_ids = {cid for c in map_data.get("channels", []) if (cid := c.get("hashId"))}
            if chan_ids and hash_id not in chan_ids:
                raise ServiceValidationError(
                    f"Channel {hash_id!r} not found in map. Known channels: {sorted(chan_ids)}"
                )
            await coordinator.async_delete_channel(thing_name, hash_id)

    async def handle_delete_nogo_zone(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_NOGO_HASH_ID]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            thing_name = entity._thing_name
            map_data = coordinator.data.get(thing_name, {}).get("mapData") or {}
            nogo_ids = {nid for n in map_data.get("nogoZones", []) if (nid := n.get("hashId"))}
            if nogo_ids and hash_id not in nogo_ids:
                raise ServiceValidationError(
                    f"No-go zone {hash_id!r} not found in map. Known no-go zones: {sorted(nogo_ids)}"
                )
            await coordinator.async_delete_nogo_zone(thing_name, hash_id)

    async def handle_start_zone(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        zone_hash_ids: list[str] = call.data[_ATTR_ZONE_HASH_IDS]

        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            thing_name = entity._thing_name
            await coordinator.async_start_zones(thing_name, zone_hash_ids)

    async def handle_pause(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_pause(entity._thing_name)

    async def handle_query_map(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_query_map(entity._thing_name)

    async def handle_resume(call: ServiceCall) -> None:
        # Resume a paused/returning mow without losing progress. HA's standard
        # start_mowing sends USER_CTRL_CLEAN (a fresh task); this sends
        # USER_CTRL_RESUME so the robot picks up where it left off.
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_resume(entity._thing_name)

    async def handle_query_schedules(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_query_schedules(entity._thing_name)

    async def handle_update_zone_polygon(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_ZONE_HASH_ID]
        polygon: list[dict] = call.data[_ATTR_POLYGON]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_update_zone_polygon(entity._thing_name, hash_id, polygon)

    async def handle_add_zone(call: ServiceCall) -> dict[str, Any]:
        entity_ids: list[str] = call.data["entity_id"]
        polygon: list[dict] = call.data[_ATTR_POLYGON]
        name: str = call.data[_ATTR_NAME]
        cut_height: int = call.data[_ATTR_CUT_HEIGHT_MM]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        new_ids: dict[str, str] = {}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            new_id = await coordinator.async_add_zone(entity._thing_name, polygon, name=name, cut_height_mm=cut_height)
            new_ids[eid] = new_id
        return {"hash_ids": new_ids}

    async def handle_merge_zones(call: ServiceCall) -> dict[str, Any]:
        entity_ids: list[str] = call.data["entity_id"]
        hash_ids: list[str] = call.data[_ATTR_ZONE_HASH_IDS]
        name: str = call.data[_ATTR_NAME]
        cut_height: int | None = call.data.get(_ATTR_CUT_HEIGHT_MM)
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        new_ids: dict[str, str] = {}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            new_id = await coordinator.async_merge_zones(
                entity._thing_name, hash_ids, name=name, cut_height_mm=cut_height
            )
            new_ids[eid] = new_id
        return {"hash_ids": new_ids}

    async def handle_pin_and_go(call: ServiceCall) -> dict[str, Any]:
        entity_ids: list[str] = call.data["entity_id"]
        x: float = call.data[_ATTR_X]
        y: float = call.data[_ATTR_Y]
        radius_m: float = call.data[_ATTR_RADIUS_M]
        cut_height: int = call.data[_ATTR_CUT_HEIGHT_MM]
        name: str = call.data[_ATTR_NAME]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        new_ids: dict[str, str] = {}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            new_id = await coordinator.async_pin_and_go(
                entity._thing_name, x, y, radius_m=radius_m, cut_height_mm=cut_height, name=name
            )
            new_ids[eid] = new_id
        return {"hash_ids": new_ids}

    async def handle_split_zone(call: ServiceCall) -> dict[str, Any]:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_ZONE_HASH_ID]
        cut_p1: dict[str, float] = call.data[_ATTR_CUT_P1]
        cut_p2: dict[str, float] = call.data[_ATTR_CUT_P2]
        names: list[str] = call.data[_ATTR_NAMES]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        split_ids: dict[str, tuple[str, str]] = {}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            left_id, right_id = await coordinator.async_split_zone(
                entity._thing_name, hash_id, cut_p1, cut_p2, names=(names[0], names[1])
            )
            split_ids[eid] = (left_id, right_id)
        return {"hash_ids": split_ids}

    async def handle_start_video_session(call: ServiceCall) -> dict[str, Any]:
        """Open a Kinesis Video Streams viewer session for the first matched device.

        Returns the channelARN + temporary AWS credentials needed for a
        WebRTC viewer (e.g. go2rtc / aiortc). Caller is responsible for
        consuming the response and completing the WebRTC handshake within
        the credentials' ~15-minute lifetime.
        """
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            return await coordinator.async_start_video_session(entity._thing_name)
        raise ServiceValidationError(f"No matching Lymow entity in {entity_ids!r}")

    def _make_query_handler(method_name: str):
        async def _handler(call: ServiceCall) -> None:
            entity_ids: list[str] = call.data["entity_id"]
            entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
            for eid in entity_ids:
                entity = entity_map.get(eid)
                if entity is None:
                    continue
                await getattr(coordinator, method_name)(entity._thing_name)

        return _handler

    async def handle_clear_schedules(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_clear_schedules(entity._thing_name)

    async def handle_set_schedules(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        entries = [
            {
                "hour": s["hour"],
                "minute": s["minute"],
                "dayOfWeek": s["day_of_week"],
                "zones": s["zones"],
                "isRepeated": s["repeated"],
                "isDisabled": s["disabled"],
            }
            for s in call.data[_ATTR_SCHEDULES]
        ]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_set_schedules(entity._thing_name, entries)

    async def handle_rename_zone(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        hash_id: str = call.data[_ATTR_ZONE_HASH_ID]
        name: str = call.data[_ATTR_NAME]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_rename_zone(entity._thing_name, hash_id, name)

    async def handle_set_task_config(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        # Map provided snake_case params to PbTaskConfig field names.
        fields = {proto: call.data[svc] for svc, proto in _TASK_CONFIG_SERVICE_FIELDS.items() if svc in call.data}
        if not fields:
            raise ServiceValidationError("set_task_config: provide at least one parameter to set.")
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_set_task_config(entity._thing_name, **fields)

    async def handle_set_run_time_config(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        fields = {
            proto: call.data[svc]
            for svc, (proto, _kind, _range) in _RUN_TIME_CONFIG_SERVICE_FIELDS.items()
            if svc in call.data
        }
        if not fields:
            raise ServiceValidationError("set_run_time_config: provide at least one parameter to set.")
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_set_run_time_config(entity._thing_name, **fields)

    async def handle_set_network_priority(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        preferred: str = call.data[_ATTR_PREFERRED]
        metric_4g = preferred == "4g"
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_set_robot_config(entity._thing_name, metric_4g=metric_4g)

    async def handle_set_recharge_resume(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        rr_kwargs = {
            "enable": call.data.get(_ATTR_RR_ENABLE),
            "period_start": call.data.get(_ATTR_RR_PERIOD_START),
            "period_end": call.data.get(_ATTR_RR_PERIOD_END),
            "recharge_bat": call.data.get(_ATTR_RR_RECHARGE_BAT),
            "resume_bat": call.data.get(_ATTR_RR_RESUME_BAT),
        }
        if not any(v is not None for v in rr_kwargs.values()):
            raise ServiceValidationError("set_recharge_resume: provide at least one parameter to set.")
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_set_recharge_resume(entity._thing_name, **rr_kwargs)

    async def handle_set_device_settings(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        cm = call.data.get(_ATTR_DS_CHARGING_MODE)
        zo = call.data.get(_ATTR_DS_ZONE_ORDER)
        ds_kwargs = {
            "charging_mode": _CHARGING_MODE_CHOICES[cm] if cm is not None else None,
            "zone_order": _ZONE_ORDER_CHOICES[zo] if zo is not None else None,
            "rainy_mowing": call.data.get(_ATTR_DS_RAINY_MOWING),
            "charging_handbrake": call.data.get(_ATTR_DS_CHARGING_HANDBRAKE),
        }
        if not any(v is not None for v in ds_kwargs.values()):
            raise ServiceValidationError("set_device_settings: provide at least one parameter to set.")
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_set_device_settings(entity._thing_name, **ds_kwargs)

    async def handle_set_device_name(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        name: str = call.data[_ATTR_NAME]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_rename_device(entity._thing_name, name)

    async def handle_restore_backup_map(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        object_key: str = call.data[_ATTR_OBJECT_KEY]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_restore_backup_map(entity._thing_name, object_key)

    async def handle_delete_backup_map(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        object_key: str = call.data[_ATTR_OBJECT_KEY]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_delete_backup_map(entity._thing_name, object_key)

    async def handle_rename_backup_map(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        object_key: str = call.data[_ATTR_OBJECT_KEY]
        name: str = call.data[_ATTR_NAME]
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        for eid in entity_ids:
            entity = entity_map.get(eid)
            if entity is None:
                continue
            await coordinator.async_rename_backup_map(entity._thing_name, object_key, name)

    async def handle_ble_drive(call: ServiceCall) -> None:
        entity_ids: list[str] = call.data["entity_id"]
        linear: float = call.data[ATTR_LINEAR]
        angular: float = call.data[ATTR_ANGULAR]
        duration: float = call.data[ATTR_DURATION]

        # One BLE transport per config entry (one robot at one address): drive
        # exactly once even when several entity_ids are targeted, so overlapping
        # motions never stack on the same link.
        entity_map: dict[str, LymowMower] = {e.entity_id: e for e in entities}
        targeted = [entity_map[eid] for eid in entity_ids if eid in entity_map]
        if not targeted:
            return

        # Prefer an explicitly-configured address; otherwise auto-discover the
        # robot over Bluetooth by its advertised name (deviceBluetooth).
        address = (entry.options.get(CONF_BLE_ADDRESS) or "").strip()
        if not address:
            ble_name = (coordinator.data.get(targeted[0]._thing_name) or {}).get("deviceBluetooth")
            address = _discover_ble_address(hass, ble_name or "") or ""
        if not address:
            raise ServiceValidationError(
                "Couldn't find the robot over Bluetooth — make sure it's powered and in range, "
                "or set its BLE address in the Lymow integration options."
            )
        await coordinator.async_ble_drive(address, linear, angular, duration)

    hass.services.async_register(DOMAIN, _SERVICE_DELETE_ZONE, handle_delete_zone, schema=_DELETE_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_DELETE_CHANNEL, handle_delete_channel, schema=_DELETE_CHANNEL_SCHEMA)
    hass.services.async_register(
        DOMAIN, _SERVICE_DELETE_NOGO_ZONE, handle_delete_nogo_zone, schema=_DELETE_NOGO_ZONE_SCHEMA
    )
    hass.services.async_register(DOMAIN, _SERVICE_START_ZONE, handle_start_zone, schema=_START_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_PAUSE, handle_pause, schema=_ENTITY_ID_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_QUERY_MAP, handle_query_map, schema=_ENTITY_ID_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_RESUME, handle_resume, schema=_ENTITY_ID_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_QUERY_SCHEDULES, handle_query_schedules, schema=_ENTITY_ID_SCHEMA)
    for service_name, method_name in _QUERY_SERVICES:
        hass.services.async_register(
            DOMAIN,
            service_name,
            _make_query_handler(method_name),
            schema=_ENTITY_ID_SCHEMA,
        )
    hass.services.async_register(
        DOMAIN, _SERVICE_UPDATE_ZONE_POLYGON, handle_update_zone_polygon, schema=_UPDATE_ZONE_POLYGON_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_ADD_ZONE,
        handle_add_zone,
        schema=_ADD_ZONE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_MERGE_ZONES,
        handle_merge_zones,
        schema=_MERGE_ZONES_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_PIN_AND_GO,
        handle_pin_and_go,
        schema=_PIN_AND_GO_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_SPLIT_ZONE,
        handle_split_zone,
        schema=_SPLIT_ZONE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_START_VIDEO_SESSION,
        handle_start_video_session,
        schema=_ENTITY_ID_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_SET_DEVICE_NAME, handle_set_device_name, schema=_SET_DEVICE_NAME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_RESTORE_BACKUP_MAP, handle_restore_backup_map, schema=_RESTORE_BACKUP_MAP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_DELETE_BACKUP_MAP, handle_delete_backup_map, schema=_DELETE_BACKUP_MAP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_RENAME_BACKUP_MAP, handle_rename_backup_map, schema=_RENAME_BACKUP_MAP_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_BLE_DRIVE, handle_ble_drive, schema=_BLE_DRIVE_SCHEMA)
    hass.services.async_register(
        DOMAIN, _SERVICE_SET_TASK_CONFIG, handle_set_task_config, schema=_SET_TASK_CONFIG_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_SET_RUN_TIME_CONFIG, handle_set_run_time_config, schema=_SET_RUN_TIME_CONFIG_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_SET_NETWORK_PRIORITY, handle_set_network_priority, schema=_SET_NETWORK_PRIORITY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_SET_RECHARGE_RESUME, handle_set_recharge_resume, schema=_SET_RECHARGE_RESUME_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, _SERVICE_SET_DEVICE_SETTINGS, handle_set_device_settings, schema=_SET_DEVICE_SETTINGS_SCHEMA
    )
    hass.services.async_register(DOMAIN, _SERVICE_RENAME_ZONE, handle_rename_zone, schema=_RENAME_ZONE_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_CLEAR_SCHEDULES, handle_clear_schedules, schema=_ENTITY_ID_SCHEMA)
    hass.services.async_register(DOMAIN, _SERVICE_SET_SCHEDULES, handle_set_schedules, schema=_SET_SCHEDULES_SCHEMA)


class LymowMower(CoordinatorEntity[LymowCoordinator], LawnMowerEntity):
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING | LawnMowerEntityFeature.PAUSE | LawnMowerEntityFeature.DOCK
    )
    # Primary entity for the device: has_entity_name + name=None renders as just the device name.
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = self._thing_name
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def _device_data(self) -> dict:
        return self.coordinator.data.get(self._thing_name, {})

    @property
    def activity(self) -> LawnMowerActivity:
        if not self._device_data.get("isOnline", True):
            return LawnMowerActivity.ERROR

        ws = self._device_data.get("workStatus", WORK_STATUS_OFFLINE)

        if ws in WORK_STATUS_MOWING_GROUP:
            return LawnMowerActivity.MOWING
        if ws in WORK_STATUS_RETURNING_GROUP:
            return LawnMowerActivity.RETURNING
        if ws in WORK_STATUS_DOCKED_GROUP:
            return LawnMowerActivity.DOCKED
        if ws in WORK_STATUS_PAUSED_GROUP:
            return LawnMowerActivity.PAUSED
        if ws in WORK_STATUS_ERROR_GROUP:
            return LawnMowerActivity.ERROR
        # Offline or unknown
        return LawnMowerActivity.ERROR

    async def async_start_mowing(self) -> None:
        await self.coordinator.async_start_mowing(self._thing_name)

    async def async_pause(self) -> None:
        await self.coordinator.async_pause(self._thing_name)

    async def async_dock(self) -> None:
        await self.coordinator.async_dock(self._thing_name)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        map_data = self._device_data.get("mapData") or {}
        return {
            "zones": [
                {
                    "hash_id": z.get("hashId", ""),
                    "area_m2": z.get("area"),
                    "enabled": z.get("isEnabled", True),
                }
                for z in map_data.get("goZones", [])
            ]
        }
