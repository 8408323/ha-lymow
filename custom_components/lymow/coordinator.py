"""Data update coordinator for Lymow."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LymowApiClient
from .bluetooth import LymowBleController
from .const import (
    AUTH_REFRESH_MARGIN_SECONDS,
    DOMAIN,
    POLLING_INTERVAL,
    USER_CTRL_CLEAN,
    USER_CTRL_FLOOR_BACKUP,
    USER_CTRL_PAUSE,
    USER_CTRL_PAUSE_DOCK,
    USER_CTRL_QUERY_CHANNELS,
    USER_CTRL_QUERY_CLEANING_INFO,
    USER_CTRL_QUERY_CLEANING_SUMMARY,
    USER_CTRL_QUERY_NET_DETAIL,
    USER_CTRL_QUERY_PATH,
    USER_CTRL_QUERY_RTK_DIAGNOSTIC_L1,
    USER_CTRL_QUERY_RTK_DIAGNOSTIC_L2,
    USER_CTRL_QUERY_RUN_TIME_CONFIG,
    USER_CTRL_QUERY_WIFI_4G,
    USER_CTRL_RECHARGE_DOCK,
    USER_CTRL_RESUME,
    USER_CTRL_RESUME_DOCK,
    WORK_STATUS_DOCKED_GROUP,
    WORK_STATUS_DOCKING,
    WORK_STATUS_ERROR_GROUP,
    WORK_STATUS_MOWING_GROUP,
    WORK_STATUS_PAUSE_DOCKING,
    WORK_STATUS_PAUSED_GROUP,
    WORK_STATUS_RETURNING_GROUP,
)
from .mqtt import LymowMqttClient
from .protocol import (
    encode_delete_zone,
    encode_query_map,
    encode_query_robot_config,
    encode_query_schedules,
    encode_start_zones,
    encode_sync_map,
    encode_userctrl,
)

_LOGGER = logging.getLogger(__name__)

# Proto3 omits scalar fields that equal their type default, so a config the
# robot has never changed off-default simply never appears on the wire. Decoding
# is therefore incomplete: an absent field *is* its default. We fill those
# defaults into the merged state so settings entities show the real value (a
# muted volume = 0, a select on its 0th option) instead of "unknown". Real wire
# values always win — these only backfill keys the robot didn't send.
_ROBOT_CONFIG_DEFAULTS: dict[str, Any] = {
    "audioVolume": 0,
    "metric_4g": False,
    "isOpenLed": False,
    "dockOnError": False,
}
_RR_CONFIG_DEFAULTS: dict[str, Any] = {"enable": False, "rechargeBat": 0, "resumeBat": 0}
_TASK_CONFIG_DEFAULTS: dict[str, Any] = {
    "chargingMode": 0,
    "zoneOrder": 0,
    "rainCleaning": False,
    "disableChargingPark": False,
}


def _is_device_online(merged: dict[str, Any]) -> bool:
    """True if the robot is online per either the MQTT notify-app flag or the
    REST device-info ``deviceState`` (the latter is the only signal available
    when the robot was already online before HA started — no transition fires)."""
    return bool(merged.get("isOnline")) or str(merged.get("deviceState", "")).lower() == "online"


def _apply_config_defaults(merged: dict[str, Any]) -> None:
    """Backfill proto3 defaults for the robotConfig / taskConfig settings fields
    in-place, so absent (default-valued) fields read as their default rather than
    unknown. Real values already present are never overwritten."""
    rc = {**_ROBOT_CONFIG_DEFAULTS, **(merged.get("robotConfig") or {})}
    rc["rrConfig"] = {**_RR_CONFIG_DEFAULTS, **(rc.get("rrConfig") or {})}
    merged["robotConfig"] = rc
    map_data = merged.get("mapData") or {}
    merged["mapData"] = {**map_data, "taskConfig": {**_TASK_CONFIG_DEFAULTS, **(map_data.get("taskConfig") or {})}}


def build_schedule_entries(
    specs: list[dict[str, Any]], map_data: dict[str, Any], now_local: datetime
) -> list[dict[str, Any]]:
    """Expand user schedule specs into full PbSchedule entries the robot accepts.

    Looks up each zone's name / representative point / cut height from cached
    map data, converts the local hour:minute to UTC (the robot stores UTC), and
    records the local UTC offset in hours. When the UTC conversion crosses
    midnight, ``dayOfWeek`` is shifted to match. Verified against the app's wire
    format.
    """
    offset = now_local.utcoffset()
    # Truncate toward zero (not floor) so negative / fractional offsets aren't
    # pushed an hour too far (e.g. UTC-3:30 -> -3, not -4).
    tz_offset_hours = int(offset.total_seconds() / 3600) if offset else 0
    zones_by_id = {z.get("hashId"): z for z in map_data.get("goZones", [])}
    entries: list[dict[str, Any]] = []
    for i, spec in enumerate(specs):
        local_ref = now_local.replace(hour=int(spec["hour"]), minute=int(spec["minute"]), second=0, microsecond=0)
        utc_dt = local_ref.astimezone(UTC)
        # If converting to UTC moved to the previous/next calendar day, shift
        # each selected weekday by the same amount so it fires on the right day.
        day_delta = (utc_dt.date() - local_ref.date()).days
        days = [(int(d) + day_delta) % 7 for d in spec.get("dayOfWeek", [])]
        zone_ids: list[str] = spec.get("zones", [])
        zinfos: list[dict[str, Any]] = []
        cut_height: int | None = None
        for hid in zone_ids:
            zone = zones_by_id.get(hid, {})
            point = zone.get("innerPoint") or zone.get("boundMin") or {"x": 0.0, "y": 0.0}
            zinfos.append(
                {
                    "hashId": hid,
                    "name": zone.get("name", ""),
                    "point": {"x": point.get("x", 0.0), "y": point.get("y", 0.0)},
                }
            )
            if cut_height is None and zone.get("cutHeight") is not None:
                cut_height = int(zone["cutHeight"])
        entry: dict[str, Any] = {
            "dayOfWeek": days,
            "hour": utc_dt.hour,
            "minute": utc_dt.minute,
            "isRepeated": bool(spec.get("isRepeated")),
            "isDisabled": bool(spec.get("isDisabled")),
            "id": int(time.time()) % 100_000_000 + i,
            "timeZone": tz_offset_hours,
            "zones": zinfos,
        }
        if zone_ids:
            entry["config"] = {
                "hashId": zone_ids[0],
                "cutHeight": cut_height if cut_height is not None else 40,
                "moveSpeed": 0.6,
                "pathSpacing": 90,
            }
        entries.append(entry)
    return entries


def _wire_entries_from_cached(schedules: list[dict[str, Any]], map_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild ``encode_set_schedules`` wire entries from cached decoded schedules.

    The decoded ``schedules`` already hold UTC ``hour``/``minute``, ``id`` and
    ``timeZone``; only the per-zone point/name (dropped on decode) is re-looked-up
    from cached map data. Used by the granular add/delete/toggle ops, which must
    re-send the full list (schedule writes are full-list replacements) without
    re-running the local->UTC conversion on the entries that aren't changing.
    """
    zones_by_id = {z.get("hashId"): z for z in map_data.get("goZones", [])}
    entries: list[dict[str, Any]] = []
    for s in schedules:
        zinfos: list[dict[str, Any]] = []
        for hid in s.get("zones", []):
            zone = zones_by_id.get(hid, {})
            point = zone.get("innerPoint") or zone.get("boundMin") or {"x": 0.0, "y": 0.0}
            zinfos.append(
                {
                    "hashId": hid,
                    "name": zone.get("name", ""),
                    "point": {"x": point.get("x", 0.0), "y": point.get("y", 0.0)},
                }
            )
        entry: dict[str, Any] = {
            "dayOfWeek": list(s.get("dayOfWeek", [])),
            "hour": int(s.get("hour", 0)),
            "minute": int(s.get("minute", 0)),
            "isRepeated": bool(s.get("isRepeated")),
            "isDisabled": bool(s.get("isDisabled")),
            "zones": zinfos,
        }
        if s.get("id") is not None:
            entry["id"] = int(s["id"])
        if s.get("timeZone") is not None:
            entry["timeZone"] = int(s["timeZone"])
        entries.append(entry)
    return entries


# /get-backup-map is fetched on a longer interval than the main coordinator poll
# (default 30 s) because both backup-map sensors are disabled-by-default and
# backups themselves are written infrequently.
_BACKUP_MAP_REFRESH_INTERVAL = 300

# How often to re-check /prod/check-update. Firmware doesn't change that often.
_OTA_CHECK_INTERVAL = timedelta(hours=6)

# OTA job-summary `status` values that mean "no longer in progress" — used to
# clear the cached otaJobId so update.in_progress flips back to False.
_OTA_TERMINAL_STATUSES = frozenset(
    {
        "OTA_SUCCESS",
        "OTA_FAILED",
        "OTA_DOWNLOAD_FAILED",
        "OTA_UPGRADE_FAILED",
        "OTA_BATTERY_LOW",
        "OTA_EXCEEDED",
        # The robot rejects an install when it's actively mowing — the job
        # was never started, so the cached jobId must be cleared.
        "OTA_ROBOT_NOT_IN_WAIT",
    }
)


class LymowCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator that merges REST polling with live MQTT state.

    coordinator.data is a dict keyed by deviceThingName.  Each value is a
    merged dict of REST fields (from get-device-info) overlaid with MQTT
    fields (battery, workStatus, etc.) as they arrive.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: LymowApiClient,
        mqtt_client: LymowMqttClient,
        devices: list[dict],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLLING_INTERVAL),
        )
        self._client = client
        self._mqtt = mqtt_client
        self.devices = devices
        self._mqtt_state: dict[str, dict[str, Any]] = {}
        # Track work status per device to detect important transitions.
        self._prev_work_status: dict[str, int] = {}
        # Track online state so on_mqtt_online only fires the persistent-notification
        # on a True → False transition. Without this, a dismissed offline notification
        # re-appears on every subsequent offline message — annoying when the broker
        # repeatedly re-asserts the same state. Default None means "never observed".
        self._prev_online: dict[str, bool | None] = {}
        # Cached backup-map snapshot per device: (fetched_at, fields).
        self._backup_map_cache: dict[str, tuple[Any, dict[str, Any]]] = {}
        # RTK auto-pause guard: per-device knobs and tracking.
        # Enabled defaults off — opt-in safety feature.
        self._rtk_guard_enabled: dict[str, bool] = {}
        # Minimum acceptable rtkStatus while mowing. Drop ≤ this triggers PAUSE.
        # Default 1 (≥ float fix). 0 means "no fix" — anything tighter is safer.
        self._rtk_guard_threshold: dict[str, int] = {}
        # True if the *coordinator* (not the user) initiated the current pause,
        # so we know it's safe to auto-resume when RTK recovers.
        self._rtk_guard_active_pause: dict[str, bool] = {}
        # OTA fields (latestVersion / otaPrefix / otaReleaseNote / otaJobId)
        # live here so they survive coordinator refreshes — the per-refresh
        # rebuild of self.data would otherwise drop them.
        self._ota_state: dict[str, dict[str, Any]] = {}
        # When we last hit /prod/check-update for each device, so we don't
        # spam the endpoint on every 30 s coordinator tick.
        self._last_ota_check: dict[str, datetime] = {}
        # Lazily-created BLE manual-drive transport, reused across drive calls.
        self._ble_controller: LymowBleController | None = None
        # Tracks devices that already had startup queries (robotConfig + map)
        # fired. Without this, restarting HA while the robot is already online
        # never triggers on_mqtt_online, so robotConfig/taskConfig stay unknown.
        self._startup_queried: set[str] = set()
        # Channel names have no protobuf field — store HA-side so renames survive
        # MQTT polls. Keyed by thing_name → {hashId → name}. Lost on HA restart;
        # the card's localStorage covers the browser-side persistence gap.
        self._channel_name_overrides: dict[str, dict[str, str]] = {}
        # Track whether a path-query task is already scheduled so we don't flood
        # the robot with QUERY_PATH commands while mowing.
        self._path_poll_pending: dict[str, bool] = {}
        # Last non-empty pathData per device — persisted in memory so the map
        # card can still show mow coverage after the robot docks (the robot stops
        # sending path data once docked, but the last session's track is useful).
        self._last_path_data: dict[str, dict] = {}
        # Auth-refresh state, populated by set_auth_context() at setup. When the
        # auth object is None (unit tests), refresh is a no-op. Cognito access
        # tokens and the derived AWS credentials both expire; without refreshing
        # them every REST poll eventually 401s and all entities go unavailable.
        self._auth: Any | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._region: str | None = None
        self._refresh_token: str | None = None
        self._id_token: str | None = None
        self._token_expiry: datetime | None = None
        self._aws_creds_expiry: datetime | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Disconnect MQTT and BLE and stop polling.

        The BLE disconnect runs even if the MQTT disconnect raises, so a
        failure on one transport can't leak the other's connection.
        """
        await super().async_shutdown()
        try:
            await self._mqtt.disconnect()
        finally:
            if self._ble_controller is not None:
                await self._ble_controller.async_disconnect()

    # ------------------------------------------------------------------
    # BLE manual drive (local transport, not via MQTT)
    # ------------------------------------------------------------------

    async def async_ble_drive(self, address: str, linear: float, angular: float, duration: float) -> None:
        """Stream a manual-drive command to the robot over BLE for ``duration`` s.

        A controller is created lazily and reused; if the configured address
        changes, the old connection is dropped first.
        """
        if self._ble_controller is None or self._ble_controller.address != address:
            if self._ble_controller is not None:
                await self._ble_controller.async_disconnect()
            self._ble_controller = LymowBleController(address)
        await self._ble_controller.async_drive_for(linear, angular, duration)

    # ------------------------------------------------------------------
    # MQTT callbacks (called from mqtt.py via loop.call_soon_threadsafe)
    # ------------------------------------------------------------------

    # Nested patch keys that must deep-merge on top of existing state rather
    # than replace it: a pboutput carrying only one PbRobotConfig sub-field
    # (e.g. metric_4g after a network-priority change) would otherwise blow
    # away every other known robotConfig field.
    _DEEP_MERGE_KEYS = ("robotConfig",)

    def on_mqtt_state(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Receive a state update from MQTT and push to HA."""
        # A QUERY_SCHEDULES reply carries the full schedule list in one message
        # (decoded into "schedules"); other pushes omit the key, leaving it intact.
        if "mapData" in patch:
            patch = self._apply_channel_name_overrides(thing_name, patch)
        # Cache non-empty pathData so the map card can show last-mow coverage
        # even after the robot docks (robot stops sending path data when docked).
        if "pathData" in patch and patch["pathData"].get("goZones"):
            self._last_path_data[thing_name] = patch["pathData"]
        # If this patch has no pathData but we have a cached one, inject it so
        # the sensor attribute stays populated until next mow clears/replaces it.
        if "pathData" not in patch and thing_name in self._last_path_data:
            patch = {**patch, "pathData": self._last_path_data[thing_name]}
        merged_patch = self._merge_nested_patch(self._mqtt_state.setdefault(thing_name, {}), patch)
        self._mqtt_state[thing_name].update(merged_patch)
        if self.data and thing_name in self.data:
            existing = self.data[thing_name]
            merged_patch_for_data = self._merge_nested_patch(existing, patch)
            merged = {**existing, **merged_patch_for_data}
            self.async_set_updated_data({**self.data, thing_name: merged})
        self._check_work_status_transition(thing_name, patch)
        self._check_rtk_guard(thing_name, patch)

    def _apply_channel_name_overrides(self, thing_name: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Re-apply HA-side channel name overrides to a mapData patch before storing."""
        overrides = self._channel_name_overrides.get(thing_name)
        if not overrides:
            return patch
        map_data = patch["mapData"]
        channels = map_data.get("channels", [])
        new_channels = [
            {**ch, "name": overrides[ch["hashId"]]} if ch.get("hashId") in overrides else ch for ch in channels
        ]
        return {**patch, "mapData": {**map_data, "channels": new_channels}}

    def _merge_nested_patch(self, existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of patch where each ``_DEEP_MERGE_KEYS`` dict is overlaid
        on the matching dict in ``existing`` (two levels deep for robotConfig)
        so partial replies keep keys they don't mention. A pboutput that only
        carries ``rrConfig: {enable: True}`` after a toggle must not wipe the
        sibling ``rechargeBat`` / ``resumeBat`` / period fields. Non-dict patch
        values are passed through."""
        if not any(k in patch for k in self._DEEP_MERGE_KEYS):
            return patch
        out = dict(patch)
        for key in self._DEEP_MERGE_KEYS:
            new = patch.get(key)
            old = existing.get(key)
            if isinstance(new, dict) and isinstance(old, dict):
                merged = {**old, **new}
                for sub_key, sub_new in new.items():
                    sub_old = old.get(sub_key)
                    if isinstance(sub_new, dict) and isinstance(sub_old, dict):
                        merged[sub_key] = {**sub_old, **sub_new}
                out[key] = merged
        return out

    def _check_work_status_transition(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Fire HA event bus events and persistent notifications on notable work status changes."""
        new_ws = patch.get("workStatus")
        if new_ws is None:
            return
        prev_ws = self._prev_work_status.get(thing_name, -1)
        self._prev_work_status[thing_name] = new_ws

        device_label = next(
            (
                d.get("deviceName") or d.get("sn") or thing_name
                for d in self.devices
                if d["deviceThingName"] == thing_name
            ),
            thing_name,
        )

        # Always fire the event bus event so automations can react.
        self.hass.bus.async_fire(
            f"{DOMAIN}_work_status_changed",
            {"thing_name": thing_name, "device_name": device_label, "work_status": new_ws, "prev_work_status": prev_ws},
        )

        # Fire persistent notifications for error and mow-complete transitions.
        if new_ws in WORK_STATUS_ERROR_GROUP and prev_ws not in WORK_STATUS_ERROR_GROUP:
            self.hass.components.persistent_notification.async_create(
                message=f"{device_label} has reported an error (status {new_ws}). Please check the robot.",
                title=f"Lymow — {device_label} error",
                notification_id=f"{DOMAIN}_{thing_name}_error",
            )
        elif prev_ws in WORK_STATUS_MOWING_GROUP | WORK_STATUS_RETURNING_GROUP and new_ws in WORK_STATUS_DOCKED_GROUP:
            self.hass.components.persistent_notification.async_create(
                message=f"{device_label} has finished mowing and returned to the dock.",
                title=f"Lymow — {device_label} done",
                notification_id=f"{DOMAIN}_{thing_name}_done",
            )
            # PbMap.f8 taskConfig (rainy_mowing, charging_handbrake, zone_order,
            # return_to_dock_route) is only present in docked-state map responses.
            # Re-query now so those switches populate after every mow session.
            self.hass.async_create_task(self.async_query_map(thing_name))

        # Clear stale path cache when a new mow session starts (docked/waiting → mowing).
        # Without this the previous session's completed track masks current progress.
        if new_ws in WORK_STATUS_MOWING_GROUP and prev_ws not in WORK_STATUS_MOWING_GROUP:
            self._last_path_data.pop(thing_name, None)

        # Auto-query mow path while actively mowing (≤ once per 30 s).
        # Fires on any workStatus update when the robot is in a mowing state,
        # but only schedules a new task if one isn't already waiting.
        if new_ws in WORK_STATUS_MOWING_GROUP and not self._path_poll_pending.get(thing_name):
            self._path_poll_pending[thing_name] = True
            self.hass.async_create_task(self._async_poll_path(thing_name))

    def _check_rtk_guard(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Auto-pause when RTK falls below user-configured threshold; auto-resume when it recovers.

        Reads ``rtkStatus`` from the latest MQTT patch. To avoid resuming
        user-initiated pauses, we only auto-resume mowers that *we* paused via
        this guard (tracked in ``_rtk_guard_active_pause``).
        """
        if not self._rtk_guard_enabled.get(thing_name, False):
            return
        # Only react when this patch actually carries an rtkStatus update.
        new_rtk = patch.get("rtkStatus")
        if new_rtk is None:
            return
        threshold = self._rtk_guard_threshold.get(thing_name, 1)
        merged = (self.data or {}).get(thing_name) or {}
        work_status = merged.get("workStatus")
        if work_status is None:
            return
        try:
            rtk_val = int(new_rtk)
        except (TypeError, ValueError):
            return

        if rtk_val <= threshold and work_status in WORK_STATUS_MOWING_GROUP:
            self.hass.async_create_task(self._async_rtk_guard_pause(thing_name, rtk_val, threshold))
        elif (
            rtk_val > threshold
            and self._rtk_guard_active_pause.get(thing_name, False)
            and work_status in WORK_STATUS_PAUSED_GROUP
        ):
            self.hass.async_create_task(self._async_rtk_guard_resume(thing_name, rtk_val))

    async def _async_rtk_guard_pause(self, thing_name: str, rtk_val: int, threshold: int) -> None:
        """Publish PAUSE and mark this device as guard-paused."""
        await self.async_pause(thing_name)
        self._rtk_guard_active_pause[thing_name] = True
        _LOGGER.warning(
            "Lymow %s: paused mow because RTK status %d ≤ threshold %d",
            thing_name,
            rtk_val,
            threshold,
        )

    async def _async_rtk_guard_resume(self, thing_name: str, rtk_val: int) -> None:
        """Publish RESUME and clear the guard-paused flag."""
        await self.async_resume(thing_name)
        self._rtk_guard_active_pause[thing_name] = False
        _LOGGER.info("Lymow %s: resumed mow because RTK status recovered to %d", thing_name, rtk_val)

    def set_rtk_guard_enabled(self, thing_name: str, enabled: bool) -> None:
        """Switch entity calls this to flip auto-pause on/off."""
        self._rtk_guard_enabled[thing_name] = enabled
        if not enabled:
            # Clear the guard-paused flag — once the user disables the feature
            # we don't want a later natural pause/resume to be mis-attributed.
            self._rtk_guard_active_pause[thing_name] = False

    def is_rtk_guard_enabled(self, thing_name: str) -> bool:
        return self._rtk_guard_enabled.get(thing_name, False)

    def set_rtk_guard_threshold(self, thing_name: str, threshold: int) -> None:
        """Number entity calls this to update the threshold."""
        self._rtk_guard_threshold[thing_name] = int(threshold)

    def get_rtk_guard_threshold(self, thing_name: str) -> int:
        return self._rtk_guard_threshold.get(thing_name, 1)

    def on_mqtt_online(self, thing_name: str, is_online: bool) -> None:
        """Receive an online/offline notification from MQTT."""
        patch = {"isOnline": is_online, "deviceState": "online" if is_online else "offline"}
        self.on_mqtt_state(thing_name, patch)
        prev_online = self._prev_online.get(thing_name)
        self._prev_online[thing_name] = is_online
        if is_online:
            # Robot just came online — query both robotConfig and the map so all
            # entity state populates without the user needing to call services.
            # query_robot_config → PbOutput.f17 → vehicle_led, prefer_4g, auto_dock…
            # query_map → PbMap.f8 taskConfig → rainy_mowing, charging_handbrake,
            #             zone_order, return_to_dock_route
            self.hass.async_create_task(self.async_query_robot_config(thing_name))
            self.hass.async_create_task(self.async_query_map(thing_name))
        # Fire the offline notification only on a True/None → False transition so
        # consecutive offline pushes don't re-create a dismissed notification.
        if not is_online and prev_online is not False:
            device_label = next(
                (
                    d.get("deviceName") or d.get("sn") or thing_name
                    for d in self.devices
                    if d["deviceThingName"] == thing_name
                ),
                thing_name,
            )
            self.hass.components.persistent_notification.async_create(
                message=f"{device_label} has gone offline.",
                title=f"Lymow — {device_label} offline",
                notification_id=f"{DOMAIN}_{thing_name}_offline",
            )

    # ------------------------------------------------------------------
    # Auth refresh
    # ------------------------------------------------------------------

    def set_auth_context(
        self,
        auth: Any,
        username: str,
        password: str,
        region: str,
        tokens: dict[str, Any],
        creds: dict[str, Any],
    ) -> None:
        """Hand the coordinator everything it needs to keep credentials fresh.

        ``tokens`` is the Cognito AuthenticationResult (AccessToken / IdToken /
        RefreshToken / ExpiresIn); ``creds`` is the get_aws_credentials() result
        (its ``credentials.Expiration`` dates the temporary AWS keys)."""
        self._auth = auth
        self._username = username
        self._password = password
        self._region = region
        self._refresh_token = tokens.get("RefreshToken")
        self._id_token = tokens.get("IdToken")
        self._token_expiry = self._expiry_from_expires_in(tokens.get("ExpiresIn"))
        self._aws_creds_expiry = self._expiry_from_timestamp(creds.get("credentials", {}).get("Expiration"))

    @staticmethod
    def _expiry_from_expires_in(expires_in: Any) -> datetime:
        seconds = int(expires_in) if isinstance(expires_in, (int, float)) else 3600
        return datetime.now(UTC) + timedelta(seconds=seconds)

    @staticmethod
    def _expiry_from_timestamp(expiration: Any) -> datetime:
        """AWS returns Expiration as a Unix epoch (number) or an ISO/datetime."""
        if isinstance(expiration, datetime):
            return expiration if expiration.tzinfo else expiration.replace(tzinfo=UTC)
        if isinstance(expiration, (int, float)):
            return datetime.fromtimestamp(expiration, tz=UTC)
        # Unknown/absent → treat as already due so we refresh on the next poll.
        return datetime.now(UTC)

    async def _async_ensure_auth(self) -> None:
        """Refresh Cognito tokens and/or AWS credentials before they expire.

        No-op when auth context wasn't provided (unit tests). Token refresh uses
        the RefreshToken, falling back to a full SRP re-login with stored creds;
        if both fail it raises ConfigEntryAuthFailed so HA surfaces a reauth."""
        if self._auth is None:
            return
        now = datetime.now(UTC)
        margin = timedelta(seconds=AUTH_REFRESH_MARGIN_SECONDS)
        token_due = self._token_expiry is None or now >= self._token_expiry - margin
        creds_due = self._aws_creds_expiry is None or now >= self._aws_creds_expiry - margin
        if not token_due and not creds_due:
            return

        if token_due:
            await self._async_refresh_tokens(now)
            creds_due = True  # the new id token requires fresh AWS credentials
        if creds_due:
            await self._async_refresh_aws_credentials()

    async def _async_refresh_tokens(self, now: datetime) -> None:
        try:
            result = await self._auth.refresh_tokens(self._refresh_token, self._region)
        except Exception as refresh_err:  # noqa: BLE001
            _LOGGER.debug("Lymow token refresh failed, falling back to re-login: %s", refresh_err)
            try:
                result = await self._auth.login_region(self._username, self._password, self._region)
            except Exception as login_err:
                raise ConfigEntryAuthFailed("Lymow re-authentication failed") from login_err
            self._refresh_token = result.get("RefreshToken") or self._refresh_token
        self._client.update_tokens(result["AccessToken"])
        self._id_token = result.get("IdToken", self._id_token)
        self._token_expiry = self._expiry_from_expires_in(result.get("ExpiresIn"))

    async def _async_refresh_aws_credentials(self) -> None:
        creds = await self._auth.get_aws_credentials(self._id_token, self._region)
        aws = creds["credentials"]
        self._client.update_aws_credentials(aws["AccessKeyId"], aws["SecretKey"], aws.get("SessionToken"))
        self._aws_creds_expiry = self._expiry_from_timestamp(aws.get("Expiration"))

    # ------------------------------------------------------------------
    # REST polling
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        await self._async_ensure_auth()
        try:
            result: dict[str, dict[str, Any]] = {}
            for device in self.devices:
                thing = device["deviceThingName"]
                rest_data = await self._client.get_device_info(thing)
                try:
                    feature_data = await self._client.get_device_feature(thing)
                except Exception as feat_err:  # noqa: BLE001
                    _LOGGER.debug("get_device_feature failed for %s: %s", thing, feat_err)
                    feature_data = {}
                history_fields = await self._fetch_last_clean_fields(thing)
                static_fields = self._static_device_fields(device)
                backup_fields = await self._fetch_backup_map_fields(thing)
                await self._maybe_refresh_ota(thing)
                await self._maybe_poll_ota_progress(thing)
                merged = {
                    **static_fields,
                    **rest_data,
                    **feature_data,
                    **history_fields,
                    **backup_fields,
                    **self._ota_state.get(thing, {}),
                    **self._mqtt_state.get(thing, {}),
                }
                _apply_config_defaults(merged)
                result[thing] = merged
                # Fire robotConfig + map queries once per HA session so
                # switch entities (vehicle_led, rainy_mowing, etc.) populate
                # even when the robot was already online before HA started.
                # Gate on REST deviceState too: a robot already online at HA
                # start sends no notify-app transition, so isOnline stays unset
                # and the config query would otherwise never fire. Require MQTT
                # connected — the first poll runs during setup before connect, and
                # a command published then is silently dropped.
                if thing not in self._startup_queried and _is_device_online(merged) and self._mqtt.is_connected:
                    self._startup_queried.add(thing)
                    self.hass.async_create_task(self.async_query_robot_config(thing))
                    self.hass.async_create_task(self.async_query_map(thing))
            return result
        except Exception as err:
            raise UpdateFailed(f"Error fetching Lymow data: {err}") from err

    @staticmethod
    def _static_device_fields(device: dict[str, Any]) -> dict[str, Any]:
        """Diagnostic fields from /device-list-query?p=devices that don't change
        within a session: SIM, Bluetooth pairing name, model, registration date,
        minimum supported firmware, lock state."""
        from datetime import datetime

        out: dict[str, Any] = {}
        for src, dst in (
            ("sn", "serialNumber"),
            ("deviceType", "deviceType"),
            ("deviceBluetooth", "deviceBluetooth"),
            ("simId", "simId"),
            ("fwMinVersion", "fwMinVersion"),
            ("deviceLocked", "deviceLocked"),
        ):
            val = device.get(src)
            if val is None:
                continue
            if isinstance(val, str):
                stripped = val.strip()
                if not stripped:
                    continue
                out[dst] = stripped
            else:
                out[dst] = val
        # createdAt arrives as ISO 8601 with a trailing "Z" — convert so the
        # TIMESTAMP-classed sensor doesn't get a raw string.
        created_at = device.get("createdAt")
        if isinstance(created_at, str) and created_at:
            try:
                out["createdAt"] = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError:
                pass
        return out

    async def _fetch_last_clean_fields(self, thing_name: str) -> dict[str, Any]:
        """Return last-clean summary fields, or {} if the response can't be interpreted.

        Response envelope:
            {"clean_history": [
                {"clean_area": <num>, "clean_time": <int min>, "date": <epoch>,
                 "used_battery": <int>, "percent": <0..1>, ...},
                ...],
             "total_records": <int>,
             "clean_summary": {"total_clean_time": <int min>, "total_clean_area": <num>}}
        """
        from datetime import UTC, datetime

        try:
            history = await self._client.get_clean_history(thing_name, page=0, page_size=15)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("get_clean_history failed for %s: %s", thing_name, err)
            return {}
        if not isinstance(history, dict):
            return {}
        entries = history.get("clean_history")
        if not isinstance(entries, list):
            return {}

        out: dict[str, Any] = {}
        # Cumulative aggregates from the envelope (NOT per-page).
        if isinstance(history.get("total_records"), int):
            out["cleanHistoryCount"] = history["total_records"]
        summary = history.get("clean_summary")
        if isinstance(summary, dict):
            if (t := summary.get("total_clean_time")) is not None:
                out["totalCleanTimeMin"] = t
            if (a := summary.get("total_clean_area")) is not None:
                out["totalCleanHistoryAreaM2"] = a

        if not entries:
            # Only fill in zero when total_records didn't already tell us
            out.setdefault("cleanHistoryCount", 0)
            return out

        last = entries[0]
        if not isinstance(last, dict):
            # API returned an unexpected shape (e.g. list of strings, None).
            # Keep the aggregates we already extracted and stop probing.
            return out

        if (area := last.get("clean_area")) is not None:
            out["lastCleanAreaM2"] = area
        if (t := last.get("clean_time")) is not None:
            out["lastCleanDurationMin"] = t
        if (epoch := last.get("date")) is not None:
            try:
                out["lastCleanAt"] = datetime.fromtimestamp(int(epoch), tz=UTC)
            except (TypeError, ValueError, OSError):
                pass
        if (pct := last.get("percent")) is not None:
            out["lastCleanPercent"] = round(float(pct) * 100, 1)
        if (batt := last.get("used_battery")) is not None:
            out["lastCleanBatteryUsed"] = batt
        # Forward additional per-entry fields so LymowCleanHistoryDetailsSensor
        # can expose them as attributes for templating.
        if isinstance(last.get("status_times"), list):
            out["lastCleanStatusTimes"] = list(last["status_times"])
        if (sv := last.get("soc_version")) is not None:
            out["lastCleanSocVersion"] = sv
        if (st := last.get("start_type")) is not None:
            out["lastCleanStartType"] = st
        if isinstance(last.get("error_list"), list):
            out["lastCleanErrorList"] = list(last["error_list"])
        if (mta := last.get("map_total_area")) is not None:
            out["lastCleanMapTotalAreaM2"] = mta
        return out

    async def _fetch_backup_map_fields(self, thing_name: str) -> dict[str, Any]:
        """Summarise /get-backup-map for sensors.

        Throttled to one call per ``_BACKUP_MAP_REFRESH_INTERVAL`` (5 min) — backups
        are written infrequently and both consumer sensors are disabled by default,
        so polling on every 30 s coordinator refresh would generate avoidable
        backend load. The cached snapshot is replayed between refreshes.
        """
        from datetime import UTC, datetime, timedelta

        cached = self._backup_map_cache.get(thing_name)
        now = datetime.now(tz=UTC)
        if cached is not None:
            fetched_at, fields = cached
            if now - fetched_at < timedelta(seconds=_BACKUP_MAP_REFRESH_INTERVAL):
                return fields

        try:
            entries = await self._client.get_backup_map_list(thing_name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("get_backup_map_list failed for %s: %s", thing_name, err)
            # Replay whatever we had — keeps sensors stable across transient errors.
            return cached[1] if cached else {}
        if not isinstance(entries, list):
            return cached[1] if cached else {}
        normalised: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            # Prefer `map_file`, but fall back to legacy field names that
            # api.get_backup_map_key already accepts — keeps the new sensor and
            # the existing MQTT map-sync agreeing about what file each entry points at.
            file_key = None
            for candidate in ("map_file", "key", "backupMapUrl", "mapKey", "url"):
                if candidate in entry and entry[candidate]:
                    file_key = entry[candidate]
                    break
            normalised.append(
                {
                    "file": file_key,
                    "name": entry.get("name") or "",
                    "backupTime": entry.get("backup_time"),
                }
            )
        out: dict[str, Any] = {
            "backupMapCount": len(normalised),
            "backupMapList": normalised,
        }
        # mapList is newest-first; use entry[0] for the latest timestamp.
        if normalised and (ts := normalised[0].get("backupTime")) is not None:
            try:
                out["backupMapLatestAt"] = datetime.fromtimestamp(int(ts), tz=UTC)
            except (TypeError, ValueError, OSError):
                pass
        self._backup_map_cache[thing_name] = (now, out)
        return out

    # ------------------------------------------------------------------
    # Commands (published via MQTT)
    # ------------------------------------------------------------------

    def _current_work_status(self, thing_name: str) -> int:
        if self.data:
            return self.data.get(thing_name, {}).get("workStatus", -1)
        return -1

    async def async_send_user_ctrl(self, thing_name: str, user_ctrl: int) -> None:
        """Publish an arbitrary userCtrl command. Used by button entities."""
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(user_ctrl))

    async def async_start_mowing(self, thing_name: str) -> None:
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(USER_CTRL_CLEAN))

    async def async_pause(self, thing_name: str) -> None:
        ws = self._current_work_status(thing_name)
        ctrl = USER_CTRL_PAUSE_DOCK if ws == WORK_STATUS_DOCKING else USER_CTRL_PAUSE
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(ctrl))

    async def async_dock(self, thing_name: str) -> None:
        ws = self._current_work_status(thing_name)
        ctrl = USER_CTRL_RESUME_DOCK if ws == WORK_STATUS_PAUSE_DOCKING else USER_CTRL_RECHARGE_DOCK
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(ctrl))

    async def async_resume(self, thing_name: str) -> None:
        ws = self._current_work_status(thing_name)
        ctrl = USER_CTRL_RESUME_DOCK if ws == WORK_STATUS_PAUSE_DOCKING else USER_CTRL_RESUME
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(ctrl))

    async def async_sync_map(self, thing_name: str, map_data: dict) -> None:
        """Push an edited map to the robot via SYNC_MAP command and update coordinator data."""
        await self._mqtt.async_publish_command(thing_name, encode_sync_map(map_data))
        if self.data and thing_name in self.data:
            new_device = {**self.data[thing_name], "mapData": map_data}
            self.async_set_updated_data({**self.data, thing_name: new_device})
        # Robot does not re-broadcast map on pboutput after SYNC_MAP; query forces it to,
        # so the Lymow app (which listens on pboutput) picks up the updated map.
        await self.async_query_map(thing_name)

    async def async_delete_zone(self, thing_name: str, hash_id: str) -> None:
        """Delete a go-zone by hashId using USER_CTRL_CLEAR_ZONE=8."""
        await self._mqtt.async_publish_command(thing_name, encode_delete_zone(hash_id))
        # Mirror nogo/channel delete: re-query so the lovelace card stops showing the deleted zone.
        await self.async_query_map(thing_name)

    async def async_rename_zone(self, thing_name: str, hash_id: str, name: str) -> None:
        """Rename a go-zone by hashId using USER_CTRL_MODIFY_ZONE_INFO=9."""
        from .protocol import encode_rename_zone

        await self._mqtt.async_publish_command(thing_name, encode_rename_zone(hash_id, name))
        if self.data and thing_name in self.data:
            map_data = self.data[thing_name].get("mapData", {})
            new_zones = [{**z, "name": name} if z.get("hashId") == hash_id else z for z in map_data.get("goZones", [])]
            new_map = {**map_data, "goZones": new_zones}
            new_device = {**self.data[thing_name], "mapData": new_map}
            self.async_set_updated_data({**self.data, thing_name: new_device})

    async def async_rename_nogo_zone(self, thing_name: str, hash_id: str, name: str) -> None:
        """Rename a no-go zone by hashId — mirrors async_rename_zone but targets PbMap.nogoZones."""
        from .protocol import encode_rename_nogo_zone

        await self._mqtt.async_publish_command(thing_name, encode_rename_nogo_zone(hash_id, name))
        if self.data and thing_name in self.data:
            map_data = self.data[thing_name].get("mapData", {})
            new_zones = [
                {**z, "name": name} if z.get("hashId") == hash_id else z for z in map_data.get("nogoZones", [])
            ]
            new_map = {**map_data, "nogoZones": new_zones}
            new_device = {**self.data[thing_name], "mapData": new_map}
            self.async_set_updated_data({**self.data, thing_name: new_device})

    async def async_rename_channel(self, thing_name: str, hash_id: str, name: str) -> None:
        """Assign a display name to a channel (HA-side only; no protobuf name field)."""
        self._channel_name_overrides.setdefault(thing_name, {})[hash_id] = name
        if self.data and thing_name in self.data:
            map_data = self.data[thing_name].get("mapData", {})
            new_channels = [
                {**ch, "name": name} if ch.get("hashId") == hash_id else ch for ch in map_data.get("channels", [])
            ]
            new_map = {**map_data, "channels": new_channels}
            new_device = {**self.data[thing_name], "mapData": new_map}
            self.async_set_updated_data({**self.data, thing_name: new_device})

    async def async_delete_channel(self, thing_name: str, hash_id: str) -> None:
        """Delete a channel by hashId (USER_CTRL_DELETE_CHANNEL), then refresh the map."""
        from .protocol import encode_delete_channel

        await self._mqtt.async_publish_command(thing_name, encode_delete_channel(hash_id))
        await self.async_query_map(thing_name)

    async def async_delete_nogo_zone(self, thing_name: str, hash_id: str) -> None:
        """Delete a no-go zone by hashId (USER_CTRL_CLEAR_ZONE), then refresh the map."""
        from .protocol import encode_delete_nogo_zone

        await self._mqtt.async_publish_command(thing_name, encode_delete_nogo_zone(hash_id))
        await self.async_query_map(thing_name)

    async def async_start_zones(self, thing_name: str, zone_hash_ids: list[str]) -> None:
        """Start mowing specific zones by hashId."""
        await self._mqtt.async_publish_command(thing_name, encode_start_zones(zone_hash_ids))

    async def async_query_map(self, thing_name: str) -> None:
        """Send USER_CTRL_QUERY_MAP to request a fresh map from the robot."""
        await self._mqtt.async_publish_command(thing_name, encode_query_map())

    async def async_query_all_maps(self) -> None:
        """Request map data for every registered device."""
        for device in self.devices:
            await self.async_query_map(device["deviceThingName"])

    async def async_query_all_robot_configs(self) -> None:
        """Request robotConfig for every device (call once MQTT is connected).

        Marks each device queried so the per-poll startup gate doesn't fire a
        duplicate. The offline-then-online case is still covered independently by
        ``on_mqtt_online``, which re-queries on the notify-app transition."""
        for device in self.devices:
            thing = device["deviceThingName"]
            self._startup_queried.add(thing)
            await self.async_query_robot_config(thing)

    async def async_query_schedules(self, thing_name: str) -> None:
        """Send USER_CTRL_QUERY_SCHEDULES to request schedule data from the robot.

        The reply (PbOutput field 16) carries the full schedule list in one
        message, which :meth:`on_mqtt_state` publishes as ``schedules``. Clear
        any already-published list first so the UI doesn't show stale entries if
        the robot is slow to reply (or has none left).
        """
        if thing_name in self._mqtt_state:
            self._mqtt_state[thing_name].pop("schedules", None)
        if self.data and thing_name in self.data and "schedules" in self.data[thing_name]:
            cleared = {k: v for k, v in self.data[thing_name].items() if k != "schedules"}
            self.async_set_updated_data({**self.data, thing_name: cleared})
        await self._mqtt.async_publish_command(thing_name, encode_query_schedules())

    async def async_query_all_schedules(self) -> None:
        """Request schedules for every registered device."""
        for device in self.devices:
            await self.async_query_schedules(device["deviceThingName"])

    async def async_clear_schedules(self, thing_name: str) -> None:
        """Clear all mowing schedules (sends an empty PbInput.schedule)."""
        from .protocol import encode_clear_schedules

        await self._mqtt.async_publish_command(thing_name, encode_clear_schedules())
        await self.async_query_schedules(thing_name)

    async def async_set_schedules(self, thing_name: str, specs: list[dict[str, Any]]) -> None:
        """Write the full set of mowing schedules, then re-query to refresh state.

        ``specs`` are user-level entries with local ``hour``/``minute``,
        ``dayOfWeek``, ``zones`` (hash IDs), ``isRepeated`` and ``isDisabled``.
        Zone name/point/cut-height and the UTC conversion are filled in here from
        cached map data and Home Assistant's configured time zone — the robot
        requires the full PbSchedule (verified against the app's wire format).
        """
        from .protocol import encode_set_schedules

        tz_name = getattr(self.hass.config, "time_zone", None) or "UTC"
        now_local = datetime.now(ZoneInfo(tz_name))
        map_data = (self.data or {}).get(thing_name, {}).get("mapData") or {}
        entries = build_schedule_entries(specs, map_data, now_local)
        await self._mqtt.async_publish_command(thing_name, encode_set_schedules(entries))
        await self.async_query_schedules(thing_name)

    def _cached_schedules(self, thing_name: str) -> list[dict[str, Any]]:
        return (self.data or {}).get(thing_name, {}).get("schedules") or []

    async def async_add_schedule(
        self,
        thing_name: str,
        *,
        hour: int,
        minute: int,
        day_of_week: list[int],
        zones: list[str],
        is_repeated: bool = True,
        is_disabled: bool = False,
    ) -> None:
        """Append one schedule, preserving the existing ones (full-list re-send).

        ``hour``/``minute`` are local; the new entry is converted to UTC via
        :func:`build_schedule_entries` while existing entries keep their cached
        UTC values.
        """
        from .protocol import encode_set_schedules

        map_data = (self.data or {}).get(thing_name, {}).get("mapData") or {}
        existing = _wire_entries_from_cached(self._cached_schedules(thing_name), map_data)
        tz_name = getattr(self.hass.config, "time_zone", None) or "UTC"
        now_local = datetime.now(ZoneInfo(tz_name))
        new_spec = {
            "hour": hour,
            "minute": minute,
            "dayOfWeek": day_of_week,
            "zones": zones,
            "isRepeated": is_repeated,
            "isDisabled": is_disabled,
        }
        new_entries = build_schedule_entries([new_spec], map_data, now_local)
        await self._mqtt.async_publish_command(thing_name, encode_set_schedules(existing + new_entries))
        await self.async_query_schedules(thing_name)

    async def async_delete_schedule(self, thing_name: str, schedule_id: int) -> None:
        """Delete one schedule by id (re-sends the remaining list)."""
        from .protocol import encode_clear_schedules, encode_set_schedules

        cached = self._cached_schedules(thing_name)
        remaining = [s for s in cached if s.get("id") != schedule_id]
        if len(remaining) == len(cached):
            raise HomeAssistantError(f"No schedule with id {schedule_id} to delete")
        map_data = (self.data or {}).get(thing_name, {}).get("mapData") or {}
        entries = _wire_entries_from_cached(remaining, map_data)
        pb = encode_set_schedules(entries) if entries else encode_clear_schedules()
        await self._mqtt.async_publish_command(thing_name, pb)
        await self.async_query_schedules(thing_name)

    async def async_toggle_schedule(self, thing_name: str, schedule_id: int, *, disabled: bool) -> None:
        """Enable/disable one schedule by id (re-sends the full list)."""
        from .protocol import encode_set_schedules

        cached = self._cached_schedules(thing_name)
        if not any(s.get("id") == schedule_id for s in cached):
            raise HomeAssistantError(f"No schedule with id {schedule_id} to toggle")
        map_data = (self.data or {}).get(thing_name, {}).get("mapData") or {}
        entries = _wire_entries_from_cached(cached, map_data)
        for entry, sched in zip(entries, cached):
            if sched.get("id") == schedule_id:
                entry["isDisabled"] = disabled
        await self._mqtt.async_publish_command(thing_name, encode_set_schedules(entries))
        await self.async_query_schedules(thing_name)

    async def async_set_task_config(self, thing_name: str, **fields: Any) -> None:
        """Set global mowing settings (userCtrl=49 GLOBAL_SETTING, "Keep Custom").

        Only the provided globalZoneConfig fields are sent; see
        :data:`protocol._TASK_CONFIG_FIELDS` for the supported names.
        """
        from .protocol import encode_set_task_config

        await self._mqtt.async_publish_command(thing_name, encode_set_task_config(**fields))

    async def async_set_recharge_resume(
        self,
        thing_name: str,
        *,
        enable: bool | None = None,
        period_start: tuple[int, int] | None = None,
        period_end: tuple[int, int] | None = None,
        recharge_bat: int | None = None,
        resume_bat: int | None = None,
    ) -> None:
        """Publish a Recharge & Resume (PbRobotConfig.rrConfig) write.

        See :func:`protocol.encode_set_recharge_resume`. Any combination of
        parameters can be ``None`` to leave that R&R field untouched on the
        robot.
        """
        from .protocol import encode_set_recharge_resume

        await self._mqtt.async_publish_command(
            thing_name,
            encode_set_recharge_resume(
                enable=enable,
                period_start=period_start,
                period_end=period_end,
                recharge_bat=recharge_bat,
                resume_bat=resume_bat,
            ),
        )

    async def async_set_headlight_schedule(
        self,
        thing_name: str,
        *,
        enable: bool,
        start: tuple[int, int] | None = None,
        end: tuple[int, int] | None = None,
    ) -> None:
        """Publish a Headlight Mode schedule write (PbRobotConfig f14/f15).

        See :func:`protocol.encode_set_headlight_schedule`. ``start`` / ``end``
        are (hour, minute) in UTC; both are required when ``enable`` is true.
        """
        from .protocol import encode_set_headlight_schedule

        await self._mqtt.async_publish_command(
            thing_name,
            encode_set_headlight_schedule(enable=enable, start=start, end=end),
        )

    async def async_set_pin(self, thing_name: str, pin: str) -> None:
        """Set the mower's 4-digit LCD-screen PIN. The value is never logged."""
        from .protocol import encode_set_pin

        await self._mqtt.async_publish_command(thing_name, encode_set_pin(pin))

    async def async_bind_rtk(self, thing_name: str, base_id: str) -> None:
        """Bind the mower to an RTK base station by id (PbRobotConfig.rtkBind)."""
        from .protocol import encode_bind_rtk

        await self._mqtt.async_publish_command(thing_name, encode_bind_rtk(base_id))

    async def async_set_wifi(self, address: str, ssid: str, password: str) -> None:
        """Provision the mower's Wi-Fi over BLE.

        Sends PbInput{f17:{f1:ssid, f2:password, f5:3}} base64-encoded to the
        drive characteristic.  Uses a fresh BLE controller (not the shared drive
        one) so a running drive session is not interrupted.  Creds never logged.
        """
        import base64

        from .protocol import encode_set_wifi

        payload = base64.b64encode(encode_set_wifi(ssid, password))
        controller = LymowBleController(address)
        await controller.async_write_once(payload)

    async def async_set_robot_config(self, thing_name: str, **fields: Any) -> None:
        """Set PbRobotConfig fields on the robot — currently just network priority.

        These writes don't set userCtrl — the robot dispatches by the presence
        of the robotConfig submessage. Supported field names are listed in
        :data:`protocol._ROBOT_CONFIG_BOOL_FIELDS` (extend it, and
        :func:`protocol.encode_set_robot_config`, to add non-bool fields).
        """
        from .protocol import encode_set_robot_config

        await self._mqtt.async_publish_command(thing_name, encode_set_robot_config(**fields))

    async def async_find_my_robot_play_sound(self, thing_name: str, volume: int = 100) -> None:
        """Trigger the app's "Find My Robot → Play Sound" beacon.

        Sends ``PbInput {f13.audioVolume=volume, f16=1}`` — the f16 trigger fires
        a one-shot locate beep on the robot. Volume defaults to 100 (max) to
        match the app. Wire format captured live 2026-05-27.
        """
        from .protocol import encode_find_my_robot_play_sound

        await self._mqtt.async_publish_command(thing_name, encode_find_my_robot_play_sound(volume))

    async def async_sync_timezone(self, thing_name: str, offset_seconds: int) -> None:
        """Push a timezone offset (seconds east of UTC) to the robot.

        Mirrors the app's "Sync with Phone" button (Hermes setTimezone #9036),
        which writes ``PbRobotConfig.timezoneOffset`` (f21) over the no-userCtrl
        robotConfig path. The app uses the phone's local timezone; HA exposes
        its own ``hass.config.time_zone`` through the button entity, so the
        coordinator just takes the pre-computed seconds value.
        """
        await self.async_set_robot_config(thing_name, timezoneOffset=int(offset_seconds))

    async def async_set_device_settings(
        self,
        thing_name: str,
        *,
        charging_mode: int | None = None,
        zone_order: int | None = None,
        rainy_mowing: bool | None = None,
        charging_handbrake: bool | None = None,
    ) -> None:
        """Publish a Device Settings (PbTaskConfig) write.

        See :func:`protocol.encode_set_device_settings`. Any of the four
        params can be ``None`` to leave that field untouched.
        """
        from .protocol import encode_set_device_settings

        await self._mqtt.async_publish_command(
            thing_name,
            encode_set_device_settings(
                charging_mode=charging_mode,
                zone_order=zone_order,
                rainy_mowing=rainy_mowing,
                charging_handbrake=charging_handbrake,
            ),
        )
        # Optimistic update: the robot never echoes PbTaskConfig via MQTT, so
        # we write the new wire values into coordinator data immediately so
        # HA switches reflect the change without staying "unknown" forever.
        if self.data and thing_name in self.data:
            updates: dict[str, Any] = {}
            if rainy_mowing is not None:
                updates["rainCleaning"] = rainy_mowing
            if charging_handbrake is not None:
                updates["disableChargingPark"] = not charging_handbrake  # inverted: UI→wire
            if zone_order is not None:
                updates["zoneOrder"] = zone_order
            if charging_mode is not None:
                updates["chargingMode"] = charging_mode
            if updates:
                existing = self.data[thing_name]
                map_data = {**existing.get("mapData", {})}
                map_data["taskConfig"] = {**map_data.get("taskConfig", {}), **updates}
                self.async_set_updated_data({**self.data, thing_name: {**existing, "mapData": map_data}})

    async def async_set_run_time_config(self, thing_name: str, **fields: Any) -> None:
        """Set run-time config parameters (USER_CTRL_SET_RUN_TIME_CONFIG).

        Unlike task-config (which is the next-mow default), run-time-config
        overrides settings on the currently-running task. Only the provided
        PbRunTimeConfig fields are sent; see :data:`protocol._RUN_TIME_CONFIG_FIELDS`
        for the supported names.

        Successful writes are mirrored into
        ``self.data[thing_name]["runTimeConfig"]`` so the Live cut-height /
        move-speed / cut-speed Number entities reflect what the user just set
        — the integration doesn't yet decode QUERY_RUN_TIME_CONFIG replies.
        """
        from .protocol import encode_set_run_time_config

        await self._mqtt.async_publish_command(thing_name, encode_set_run_time_config(**fields))
        patch = {name: value for name, value in fields.items() if value is not None}
        if patch and self.data and thing_name in self.data:
            # Cached runTimeConfig comes from a wire decode path that may not
            # exist yet (we don't decode QUERY_RUN_TIME_CONFIG replies) — if a
            # future malformed payload puts a non-dict here, the dict-union
            # below would TypeError and turn a successful publish into a
            # failed service call. Coerce non-dict cache to an empty baseline.
            cached = self.data[thing_name].get("runTimeConfig")
            existing = (cached if isinstance(cached, dict) else {}) | patch
            self._publish_device_patch(thing_name, {"runTimeConfig": existing})

    async def _publish_userctrl(self, thing_name: str, code: int) -> None:
        """Publish a bare ``userCtrl=code`` pbinput — for the read-only QUERY_*
        commands that the robot answers via pboutput."""
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(code))

    async def async_query_cleaning_info(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_CLEANING_INFO)

    async def async_query_cleaning_summary(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_CLEANING_SUMMARY)

    async def async_query_robot_config(self, thing_name: str) -> None:
        # Robot requires PbInput.f9={f10=1} format, not plain userCtrl=35.
        await self._mqtt.async_publish_command(thing_name, encode_query_robot_config())

    async def async_query_path(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_PATH)

    async def _async_poll_path(self, thing_name: str) -> None:
        """Poll QUERY_PATH every 30 s while the robot is mowing.

        The pending flag gates entry so workStatus ticks don't pile up requests.
        After each query-and-sleep cycle we re-schedule only if the robot is still
        in a mowing state, giving a clean 30 s cadence with no drift.
        """
        try:
            await asyncio.sleep(2)  # brief delay so robot finishes the current strip
            await self._publish_userctrl(thing_name, USER_CTRL_QUERY_PATH)
            await asyncio.sleep(28)  # rest of the 30 s window
            # If still mowing, kick off the next cycle immediately
            ws = (self.data or {}).get(thing_name, {}).get("workStatus")
            if ws in WORK_STATUS_MOWING_GROUP:
                self.hass.async_create_task(self._async_poll_path(thing_name))
                return  # keep pending=True for the new task
        finally:
            self._path_poll_pending[thing_name] = False

    async def async_query_channels(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_CHANNELS)

    async def async_query_run_time_config(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_RUN_TIME_CONFIG)

    async def async_query_wifi_4g(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_WIFI_4G)

    async def async_query_net_detail(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_NET_DETAIL)

    async def async_query_rtk_diagnostic_l1(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_RTK_DIAGNOSTIC_L1)

    async def async_query_rtk_diagnostic_l2(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_RTK_DIAGNOSTIC_L2)

    async def async_update_zone_cut_height(self, thing_name: str, hash_id: str, mm: int) -> None:
        """Update cut height for a go-zone and push the map back to the robot."""
        import copy

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        updated = copy.deepcopy(map_data)
        for z in updated.get("goZones", []):
            if z.get("hashId") == hash_id:
                z["cutHeight"] = mm
                break
        await self.async_sync_map(thing_name, updated)

    async def async_set_zone_config(self, thing_name: str, updates: list[dict[str, Any]]) -> None:
        """Set per-zone PbZoneConfig overrides via userCtrl=9 (app's path).

        Bandwidth-efficient alternative to ``async_update_zone_cut_height`` /
        ``async_sync_map``: only the named zones + only the named config fields
        are sent. Wire format byte-equal to the app's Mowing Settings →
        Customize tab (BLE capture 2026-05-27, see BRANCH_STATUS reply 4).

        Each ``updates`` entry: ``{"hashId": str, "isEnabled": bool=True,
        ...PbZoneConfig fields}``. Re-queries the map after publish so the
        local cache reflects the new values within one round-trip.
        """
        from .protocol import encode_set_zone_config

        if not updates:
            raise HomeAssistantError("set_zone_config: at least one zone update is required")
        await self._mqtt.async_publish_command(thing_name, encode_set_zone_config(updates))
        await self.async_query_map(thing_name)

    async def async_get_clean_history(
        self,
        thing_name: str,
        *,
        page: int = 0,
        page_size: int = 15,
    ) -> list[dict[str, Any]]:
        """Return the cleaning-history list (most-recent first) for templating.

        The HA sensors only surface the *last* session's fields — automations
        that want to walk the full history (e.g. "alert me if my last 3 mows
        failed") need the raw list. Hits the same /get-clean-history-collect
        REST endpoint as the periodic refresh.
        """
        try:
            history = await self._client.get_clean_history(thing_name, page=page, page_size=page_size)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"get_clean_history failed: {err}") from err
        if not isinstance(history, dict):
            return []
        entries = history.get("clean_history")
        if not isinstance(entries, list):
            return []
        return [e for e in entries if isinstance(e, dict)]

    async def async_update_channel_settings(
        self,
        thing_name: str,
        hash_id: str,
        *,
        cut_height_mm: int | None = None,
        channel_lift: int | None = None,
    ) -> None:
        """Override mowing settings for a single channel and resync the map.

        Channels carry their settings directly on the PbChannel record
        (``cutHeight`` at f9, ``channelLift`` at f10) — there's no separate
        configBox sub-message as there is for zones. We mutate the local
        cache and resync via sync_map (userCtrl=25) — same proven path
        ``async_update_zone_cut_height`` uses for zones.
        """
        import copy

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        updated = copy.deepcopy(map_data)
        target = None
        for ch in updated.get("channels", []):
            if ch.get("hashId") == hash_id:
                target = ch
                break
        if target is None:
            raise HomeAssistantError(f"Channel {hash_id!r} not found in current map data")
        if cut_height_mm is not None:
            target["cutHeight"] = int(cut_height_mm)
        if channel_lift is not None:
            target["channelLift"] = int(channel_lift)
        await self.async_sync_map(thing_name, updated)

    async def async_update_zone_polygon(self, thing_name: str, hash_id: str, polygon: list[dict]) -> None:
        """Replace a go-zone's polygon with the caller-supplied vertices and SYNC_MAP.

        ``polygon`` is a list of ``{"x": float, "y": float}`` points in the robot's
        local ENU frame — the same shape the existing decoder produces. We don't
        validate the polygon's geometry (self-intersection, winding order, etc.)
        because the robot's behaviour with invalid input isn't documented; the
        caller is responsible for sending well-formed shapes.

        Marks ``modifyHashs`` so the robot knows which zone changed.
        """
        import copy

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise HomeAssistantError(
                f"Polygon needs at least 3 vertices, got {len(polygon) if isinstance(polygon, list) else type(polygon).__name__}"
            )
        for pt in polygon:
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                raise HomeAssistantError("Polygon vertices must be dicts with 'x' and 'y' keys")
        updated = copy.deepcopy(map_data)
        target = None
        for z in updated.get("goZones", []):
            if z.get("hashId") == hash_id:
                target = z
                break
        if target is None:
            raise HomeAssistantError(f"Zone {hash_id!r} not found in map")
        target["polygon"] = [{"x": float(p["x"]), "y": float(p["y"])} for p in polygon]
        # Tell the robot which zone changed — same pattern used by delete_zone.
        existing_modified = updated.get("modifyHashs") or []
        if hash_id not in existing_modified:
            updated["modifyHashs"] = [*existing_modified, hash_id]
        await self.async_sync_map(thing_name, updated)

    async def async_update_nogo_polygon(self, thing_name: str, hash_id: str, polygon: list[dict]) -> None:
        """Replace a no-go zone's polygon with the caller-supplied vertices and SYNC_MAP."""
        import copy

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise HomeAssistantError(
                f"Polygon needs at least 3 vertices, got {len(polygon) if isinstance(polygon, list) else type(polygon).__name__}"
            )
        for pt in polygon:
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                raise HomeAssistantError("Polygon vertices must be dicts with 'x' and 'y' keys")
        updated = copy.deepcopy(map_data)
        target = None
        for z in updated.get("nogoZones", []):
            if z.get("hashId") == hash_id:
                target = z
                break
        if target is None:
            raise HomeAssistantError(f"No-go zone {hash_id!r} not found in map")
        target["polygon"] = [{"x": float(p["x"]), "y": float(p["y"])} for p in polygon]
        existing_modified = updated.get("modifyHashs") or []
        if hash_id not in existing_modified:
            updated["modifyHashs"] = [*existing_modified, hash_id]
        await self.async_sync_map(thing_name, updated)

    async def async_move_charging_station(
        self, thing_name: str, x: float, y: float, theta: float | None = None
    ) -> None:
        """Move the charging station to new ENU coordinates and SYNC_MAP."""
        import copy

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        updated = copy.deepcopy(map_data)
        existing = updated.get("chargingStation") or {}
        updated["chargingStation"] = {
            **existing,
            "x": float(x),
            "y": float(y),
            "theta": float(theta) if theta is not None else existing.get("theta", 0.0),
        }
        await self.async_sync_map(thing_name, updated)

    async def async_add_zone(
        self,
        thing_name: str,
        polygon: list[dict],
        name: str = "",
        cut_height_mm: int = 40,
    ) -> str:
        """Create a brand-new go-zone with the given polygon and push the map.

        Generates a fresh 8-char hex hashId (same format the robot uses) and
        appends the new zone to the map's ``goZones``. Returns the new hashId
        so a follow-up automation can target it (e.g. ``start_zone``).
        """
        import copy
        import secrets

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise HomeAssistantError(
                f"Polygon needs at least 3 vertices, got {len(polygon) if isinstance(polygon, list) else type(polygon).__name__}"
            )
        for pt in polygon:
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                raise HomeAssistantError("Polygon vertices must be dicts with 'x' and 'y' keys")
        new_hash_id = secrets.token_hex(4)
        # Ensure we don't clash with an existing zone's hash (vanishingly unlikely
        # but the cost of the check is one set lookup, so paranoia is cheap).
        existing_ids = {z.get("hashId") for z in map_data.get("goZones", [])} | {
            z.get("hashId") for z in map_data.get("nogoZones", [])
        }
        while new_hash_id in existing_ids:
            new_hash_id = secrets.token_hex(4)
        updated = copy.deepcopy(map_data)
        new_zone = {
            "hashId": new_hash_id,
            "name": name,
            "isEnabled": True,
            "cutHeight": int(cut_height_mm),
            "polygon": [{"x": float(p["x"]), "y": float(p["y"])} for p in polygon],
        }
        updated.setdefault("goZones", []).append(new_zone)
        existing_modified = updated.get("modifyHashs") or []
        updated["modifyHashs"] = [*existing_modified, new_hash_id]
        await self.async_sync_map(thing_name, updated)
        return new_hash_id

    async def async_add_nogo_zone(
        self,
        thing_name: str,
        polygon: list[dict],
        parent_zone_hash_id: str = "",
    ) -> str:
        """Create a new no-go zone and push the map. Returns the new hashId."""
        import copy
        import secrets

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise HomeAssistantError(
                f"Polygon needs at least 3 vertices, got {len(polygon) if isinstance(polygon, list) else type(polygon).__name__}"
            )
        for pt in polygon:
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                raise HomeAssistantError("Polygon vertices must be dicts with 'x' and 'y' keys")
        new_hash_id = secrets.token_hex(4)
        existing_ids = {z.get("hashId") for z in map_data.get("goZones", [])} | {
            z.get("hashId") for z in map_data.get("nogoZones", [])
        }
        while new_hash_id in existing_ids:
            new_hash_id = secrets.token_hex(4)
        updated = copy.deepcopy(map_data)
        new_zone: dict[str, Any] = {
            "hashId": new_hash_id,
            "type": 0,
            "isEnabled": True,
            "polygon": [{"x": float(p["x"]), "y": float(p["y"])} for p in polygon],
        }
        if parent_zone_hash_id:
            new_zone["parentZoneHashId"] = parent_zone_hash_id
        updated.setdefault("nogoZones", []).append(new_zone)
        existing_modified = updated.get("modifyHashs") or []
        updated["modifyHashs"] = [*existing_modified, new_hash_id]
        await self.async_sync_map(thing_name, updated)
        return new_hash_id

    async def async_add_channel(
        self,
        thing_name: str,
        polygon: list[dict],
        zone1_hash_id: str = "",
        zone2_hash_id: str = "",
        cut_height_mm: int = 40,
    ) -> str:
        """Create a new channel (path connector) and push the map. Returns the new hashId."""
        import copy
        import secrets

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        if not isinstance(polygon, list) or len(polygon) < 2:
            raise HomeAssistantError(
                f"Channel needs at least 2 points, got {len(polygon) if isinstance(polygon, list) else type(polygon).__name__}"
            )
        for pt in polygon:
            if not isinstance(pt, dict) or "x" not in pt or "y" not in pt:
                raise HomeAssistantError("Channel points must be dicts with 'x' and 'y' keys")
        new_hash_id = secrets.token_hex(4)
        existing_ids = (
            {z.get("hashId") for z in map_data.get("goZones", [])}
            | {z.get("hashId") for z in map_data.get("nogoZones", [])}
            | {c.get("hashId") for c in map_data.get("channels", [])}
        )
        while new_hash_id in existing_ids:
            new_hash_id = secrets.token_hex(4)
        updated = copy.deepcopy(map_data)
        new_channel: dict[str, Any] = {
            "hashId": new_hash_id,
            "isValid": True,
            "isDockingChannel": False,
            "cutHeight": int(cut_height_mm),
            "polygon": [{"x": float(p["x"]), "y": float(p["y"])} for p in polygon],
        }
        if zone1_hash_id:
            new_channel["zone1"] = zone1_hash_id
        if zone2_hash_id:
            new_channel["zone2"] = zone2_hash_id
        updated.setdefault("channels", []).append(new_channel)
        existing_modified = updated.get("modifyHashs") or []
        updated["modifyHashs"] = [*existing_modified, new_hash_id]
        await self.async_sync_map(thing_name, updated)
        return new_hash_id

    async def async_set_geofence_radius(self, thing_name: str, radius_m: int) -> None:
        """Update the radius of the first (and only observed) geofence circle.

        The wire format is a list of objects with name/latitude/longitude/
        radius. We mutate just the radius and resend the whole array so the
        rest of the geofence record (centre coords + name) stays intact.
        """
        current = (self.data or {}).get(thing_name, {}).get("geoFence") or []
        if not isinstance(current, list) or not current:
            raise HomeAssistantError("No geofence configured yet — set the centre in the Lymow app first.")
        first = current[0]
        if not isinstance(first, dict):
            # Defensive: the API has only ever returned a list of dicts; if a
            # malformed entry ever appears, surface a controlled error
            # instead of a TypeError from the `{**first, ...}` spread below.
            raise HomeAssistantError(
                f"Geofence record is malformed (got {type(first).__name__} instead of a dict); "
                "re-save the geofence in the Lymow app to repair it."
            )
        updated = [{**first, "radius": int(radius_m)}, *current[1:]]
        await self.async_set_device_feature(thing_name, geoFence=updated)

    async def async_set_geofence(
        self,
        thing_name: str,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        radius_m: int | None = None,
        name: str | None = None,
        index: int = 0,
    ) -> None:
        """Set one anti-theft geofence region's centre, radius, and optional name in one PATCH.

        Mirrors the app's Settings → Anti-theft page (centre + radius slider +
        Save). The app navigates between regions with `< >` arrows; `index`
        selects which region to mutate (0 = first). Pass `index == len(current)`
        to append a new region. Empty/unset existing records are seeded with
        sensible defaults so callers can configure a fresh device without first
        opening the Lymow app.
        """
        raw = (self.data or {}).get(thing_name, {}).get("geoFence") or []
        current: list[Any] = list(raw) if isinstance(raw, list) else []
        if index < 0 or index > len(current):
            raise HomeAssistantError(
                f"Geofence index {index} is out of range (have {len(current)} region(s); "
                "pass index == len to append a new region)."
            )
        if index < len(current) and isinstance(current[index], dict):
            existing = current[index]
        else:
            existing = {"name": "", "latitude": 0.0, "longitude": 0.0, "radius": 150}
        merged = {**existing}
        if latitude is not None:
            merged["latitude"] = float(latitude)
        if longitude is not None:
            merged["longitude"] = float(longitude)
        if radius_m is not None:
            merged["radius"] = int(radius_m)
        if name is not None:
            merged["name"] = str(name)
        if index == len(current):
            updated = [*current, merged]
        else:
            updated = [*current[:index], merged, *current[index + 1 :]]
        await self.async_set_device_feature(thing_name, geoFence=updated)

    async def async_set_device_feature(self, thing_name: str, **fields: Any) -> None:
        """PATCH device feature settings (theft, find-robot, mobile-notification, etc.)
        and optimistically merge the change into coordinator data so entities
        reflect the new state immediately.

        Publishes a fresh top-level data snapshot via
        ``async_set_updated_data`` rather than mutating ``self.data[...]``
        in place, so listeners always see a consistent dict (and any
        downstream code that holds a reference to the previous snapshot
        won't observe shifted state mid-cycle).
        """
        await self._client.update_device_feature(thing_name, **fields)
        if self.data and thing_name in self.data:
            new_device = {**self.data[thing_name], **fields}
            self.async_set_updated_data({**self.data, thing_name: new_device})

    async def async_rename_device(self, thing_name: str, device_name: str) -> None:
        """Set the robot's cloud display name and merge it into coordinator data."""
        await self._client.rename_device(thing_name, device_name)
        if self.data and thing_name in self.data:
            new_device = {**self.data[thing_name], "deviceName": device_name}
            self.async_set_updated_data({**self.data, thing_name: new_device})

    # ------------------------------------------------------------------
    # Backup-map management
    # ------------------------------------------------------------------

    async def async_restore_backup_map(self, thing_name: str, from_key: str) -> None:
        """Restore a saved backup map onto the device, then re-query the map."""
        await self._client.restore_backup_map(thing_name, from_key)
        await self.async_query_map(thing_name)

    async def async_backup_map(self, thing_name: str) -> None:
        """Snapshot the robot's current map to cloud (USER_CTRL_FLOOR_BACKUP).

        Drops the cached backup snapshot so the backup sensors pick up the new
        entry on the next poll instead of waiting out the 5-minute cache.
        """
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(USER_CTRL_FLOOR_BACKUP))
        self._backup_map_cache.pop(thing_name, None)

    async def async_delete_backup_map(self, thing_name: str, object_key: str) -> None:
        """Delete a saved backup map and drop the cached backup snapshot."""
        await self._client.delete_backup_map(object_key)
        self._backup_map_cache.pop(thing_name, None)

    async def async_rename_backup_map(self, thing_name: str, object_key: str, name: str) -> None:
        """Rename a saved backup map and drop the cached backup snapshot."""
        await self._client.rename_backup_map(object_key, name)
        self._backup_map_cache.pop(thing_name, None)

    async def _maybe_refresh_ota(self, thing_name: str) -> None:
        """Refresh the OTA snapshot for one device if our cache is stale.

        Hits /prod/check-update at most once per ``_OTA_CHECK_INTERVAL`` per
        device. Failures are swallowed and still count toward the throttle —
        if the endpoint is down we don't want every 30 s tick to retry.
        """
        last = self._last_ota_check.get(thing_name)
        now = datetime.now(UTC)
        if last is not None and (now - last) < _OTA_CHECK_INTERVAL:
            return
        self._last_ota_check[thing_name] = now
        try:
            data = await self._client.check_update(thing_name)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("check_update failed for %s: %s", thing_name, err)
            return
        patch = self._ota_patch_from_check(data)
        if patch:
            self._ota_state.setdefault(thing_name, {}).update(patch)

    async def _maybe_poll_ota_progress(self, thing_name: str) -> None:
        """If an OTA job is in flight, poll its status so in_progress can flip back."""
        job_id = (self._ota_state.get(thing_name) or {}).get("otaJobId")
        if not job_id:
            return
        try:
            await self.async_get_ota_progress(thing_name, job_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("OTA progress poll failed for %s: %s", thing_name, err)

    @staticmethod
    def _ota_patch_from_check(data: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not isinstance(data, dict):
            return out
        for src, dst in (
            ("latestVersion", "latestVersion"),
            ("prefix", "otaPrefix"),
            ("releaseNote", "otaReleaseNote"),
        ):
            if src in data:
                out[dst] = data[src]
        return out

    async def async_check_firmware_update(self, thing_name: str) -> dict[str, Any]:
        """Explicit OTA refresh (e.g. from a service call).

        Always hits the endpoint, updates the persisted OTA snapshot, and
        publishes a fresh top-level data dict so entities see the new value
        without waiting for the next coordinator tick.
        """
        data = await self._client.check_update(thing_name)
        self._last_ota_check[thing_name] = datetime.now(UTC)
        patch = self._ota_patch_from_check(data)
        if patch:
            self._ota_state.setdefault(thing_name, {}).update(patch)
            self._publish_device_patch(thing_name, patch)
        return data

    async def async_install_firmware_update(self, thing_name: str, object_key: str) -> str | None:
        """Trigger an OTA install. Returns the created jobId if the API gave one.

        ``object_key`` is sent verbatim as the ``?objectKey=`` query param to
        /prod/create-ota-job. The UpdateEntity passes ``otaPrefix + latestVersion``;
        it refuses to install if those fields haven't been populated by a prior
        check_update.
        """
        result = await self._client.create_ota_job(thing_name, object_key)
        job_id = result.get("jobId") if isinstance(result, dict) else None
        self._ota_state.setdefault(thing_name, {})["otaJobId"] = job_id
        self._publish_device_patch(thing_name, {"otaJobId": job_id})
        return job_id

    async def async_get_ota_progress(self, thing_name: str, job_id: str) -> dict[str, Any]:
        """Poll the current OTA job status.

        When the status is terminal (success / failed / etc.), clear the
        cached otaJobId so update.in_progress flips back to False on the
        next refresh.
        """
        result = await self._client.get_ota_job_summary(thing_name, job_id)
        status = result.get("status") if isinstance(result, dict) else None
        if isinstance(status, str) and status in _OTA_TERMINAL_STATUSES:
            self._ota_state.get(thing_name, {}).pop("otaJobId", None)
            self._publish_device_patch(thing_name, {"otaJobId": None})
        return result

    def _publish_device_patch(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Merge ``patch`` into self.data[thing_name] and publish a fresh snapshot.

        Generic — used by OTA progress writes and by optimistic-state writes for
        run-time-config Numbers / device-settings entities. No-op if the device
        isn't in coordinator data yet (avoids seeding pre-poll).
        """
        if not self.data or thing_name not in self.data:
            return
        new_device = {**self.data[thing_name], **patch}
        self.async_set_updated_data({**self.data, thing_name: new_device})

    async def async_start_video_session(self, thing_name: str) -> dict[str, Any]:
        """Open a Kinesis Video Streams viewer session and resolve the full
        WebRTC connect config for the robot's camera.

        Chains the captured flow: ``kvs/cmd`` → ``getSignalingChannelEndpoint``
        → ``get-ice-server-config`` and returns everything a WebRTC viewer
        needs — channelARN, temporary AWS credentials, the signaling WSS +
        HTTPS endpoints, and the ICE/TURN server list. The HA integration
        itself does not pipe video bytes (that needs aiortc / go2rtc / a
        camera entity); this service hands a viewer the complete handshake
        inputs. Endpoint/ICE resolution failures are non-fatal — the base
        session is still returned so a caller can resolve them itself.
        """
        session = await self._client.start_video_session(thing_name)
        if not isinstance(session, dict):
            # The gateway should always return a JSON object; anything else is
            # an error, not a session.
            raise HomeAssistantError(f"Unexpected kvs/cmd response for {thing_name}: {type(session).__name__}")
        channel_arn = session.get("channelARN")
        creds = session.get("credentials")
        region = session.get("region")
        if not (channel_arn and isinstance(creds, dict)):
            return session
        try:
            endpoints = await self._client.get_signaling_channel_endpoint(channel_arn, creds, region=region)
            session["signalingEndpoints"] = endpoints
            if endpoints.get("HTTPS"):
                session["iceServers"] = await self._client.get_ice_server_config(
                    channel_arn, endpoints["HTTPS"], creds, region=region
                )
            # Turnkey viewer config: a browser/WebRTC client can connect with
            # just these — the SigV4-presigned signaling WSS, a unique viewer
            # client id, and the ICE/TURN list shaped for RTCPeerConnection.
            if endpoints.get("WSS"):
                client_id = self._client.viewer_client_id()
                session["viewerClientId"] = client_id
                session["viewerWssUrl"] = self._client.presign_signaling_url(
                    endpoints["WSS"], channel_arn, client_id, creds, region=region
                )
                session["webrtcIceServers"] = [
                    {
                        "urls": s.get("Uris") or s.get("uris"),
                        "username": s.get("Username"),
                        "credential": s.get("Password"),
                    }
                    for s in session.get("iceServers", [])
                    if isinstance(s, dict)
                ]
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("KVS endpoint/ICE resolution failed for %s: %s", thing_name, err)
        return session

    async def async_merge_zones(
        self,
        thing_name: str,
        hash_ids: list[str],
        name: str = "",
        cut_height_mm: int | None = None,
    ) -> str:
        """Merge two or more go-zones into a single convex-hull zone and SYNC_MAP.

        - Deletes every input zone (and their child no-go zones).
        - Computes the convex hull of all input polygons' vertices.
        - Adds a new zone with a fresh hashId carrying that hull as its polygon.
        - Falls back to the highest input zone's ``cutHeight`` if ``cut_height_mm``
          is not supplied.

        Returns the new hashId. Raises ``HomeAssistantError`` if the map isn't
        loaded, fewer than 2 zones are requested, or any requested zone is
        missing from the cached map.
        """
        import copy
        import secrets

        from .geometry import merge_zone_polygons

        if len(hash_ids) < 2:
            raise HomeAssistantError(f"async_merge_zones needs at least 2 zones, got {len(hash_ids)}")
        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        existing = {z.get("hashId"): z for z in map_data.get("goZones", [])}
        missing = [h for h in hash_ids if h not in existing]
        if missing:
            raise HomeAssistantError(f"Zone(s) not found in map: {missing}")
        polygons = [existing[h].get("polygon") or [] for h in hash_ids]
        # Drop zones with no polygon — nothing useful to merge from them.
        polygons = [p for p in polygons if p]
        if not polygons:
            raise HomeAssistantError("None of the requested zones have a polygon to merge")
        try:
            merged_hull = merge_zone_polygons(*polygons)
        except ValueError as err:
            raise HomeAssistantError(f"Could not merge zones: {err}") from err

        if cut_height_mm is None:
            cut_height_mm = max((existing[h].get("cutHeight") or 40) for h in hash_ids)

        # Generate a non-clashing hash. Collision is vanishingly unlikely but
        # the existing-ids set is cheap to consult.
        all_ids = {z.get("hashId") for z in map_data.get("goZones", [])} | {
            z.get("hashId") for z in map_data.get("nogoZones", [])
        }
        new_hash_id = secrets.token_hex(4)
        while new_hash_id in all_ids:
            new_hash_id = secrets.token_hex(4)

        updated = copy.deepcopy(map_data)
        # Remove the source zones AND their child no-go zones (cascade-delete
        # mirrors the existing async_delete_zone behaviour).
        hash_set = set(hash_ids)
        updated["goZones"] = [z for z in updated.get("goZones", []) if z.get("hashId") not in hash_set]
        updated["nogoZones"] = [
            n
            for n in updated.get("nogoZones", [])
            if n.get("hashId") not in hash_set and n.get("parentZoneHashId") not in hash_set
        ]
        # Append the merged zone.
        new_zone = {
            "hashId": new_hash_id,
            "name": name,
            "isEnabled": True,
            "cutHeight": int(cut_height_mm),
            "polygon": merged_hull,
        }
        updated.setdefault("goZones", []).append(new_zone)
        # Tell the robot which hashes changed.
        existing_modified = updated.get("modifyHashs") or []
        updated["modifyHashs"] = [*existing_modified, *hash_ids, new_hash_id]
        await self.async_sync_map(thing_name, updated)
        return new_hash_id

    async def async_pin_and_go(
        self,
        thing_name: str,
        x: float,
        y: float,
        radius_m: float = 1.0,
        cut_height_mm: int = 40,
        name: str = "",
    ) -> str:
        """Drop a square go-zone of side ``2*radius_m`` around ``(x, y)`` and
        immediately start mowing it. Returns the new zone's hashId.

        Coordinates are in the robot's local ENU frame (same as
        ``poseEastM`` / ``poseNorthM``). The new zone persists in the map
        after the mow completes; the caller can delete it via
        ``async_delete_zone`` if it's a one-shot.
        """
        if radius_m <= 0:
            raise HomeAssistantError(f"Pin-and-go radius must be positive, got {radius_m}")
        square = [
            {"x": float(x) - radius_m, "y": float(y) - radius_m},
            {"x": float(x) + radius_m, "y": float(y) - radius_m},
            {"x": float(x) + radius_m, "y": float(y) + radius_m},
            {"x": float(x) - radius_m, "y": float(y) + radius_m},
        ]
        new_hash_id = await self.async_add_zone(thing_name, square, name=name, cut_height_mm=cut_height_mm)
        await self.async_start_zones(thing_name, [new_hash_id])
        return new_hash_id

    async def async_split_zone(
        self,
        thing_name: str,
        hash_id: str,
        cut_p1: dict[str, float],
        cut_p2: dict[str, float],
        names: tuple[str, str] = ("", ""),
    ) -> tuple[str, str]:
        """Split a go-zone in two along a cut line and SYNC_MAP the result.

        Returns ``(left_hash_id, right_hash_id)`` for the two new zones, where
        "left" is the side where ``_line_side`` is positive (CCW from the cut
        direction). Both new zones inherit the parent zone's ``cutHeight`` and
        ``isEnabled``. Child no-go zones are dropped — their parent reference
        would become invalid after the split, so the caller must re-create any
        no-go zones if they should persist.
        """
        import copy
        import secrets

        from .geometry import split_polygon

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        source = next((z for z in map_data.get("goZones", []) if z.get("hashId") == hash_id), None)
        if source is None:
            raise HomeAssistantError(f"Zone {hash_id!r} not found in map")
        polygon = source.get("polygon") or []
        if len(polygon) < 3:
            raise HomeAssistantError(f"Source zone has no polygon (only {len(polygon)} vertices)")
        try:
            left_poly, right_poly = split_polygon(polygon, cut_p1, cut_p2)
        except ValueError as err:
            raise HomeAssistantError(f"Could not split zone: {err}") from err

        all_ids = {z.get("hashId") for z in map_data.get("goZones", [])} | {
            z.get("hashId") for z in map_data.get("nogoZones", [])
        }

        def _fresh_hash(used: set[str]) -> str:
            h = secrets.token_hex(4)
            while h in used:
                h = secrets.token_hex(4)
            return h

        left_id = _fresh_hash(all_ids)
        right_id = _fresh_hash(all_ids | {left_id})
        cut_height = source.get("cutHeight") or 40
        is_enabled = bool(source.get("isEnabled", True))

        updated = copy.deepcopy(map_data)
        updated["goZones"] = [z for z in updated.get("goZones", []) if z.get("hashId") != hash_id]
        updated["nogoZones"] = [
            n
            for n in updated.get("nogoZones", [])
            if n.get("hashId") != hash_id and n.get("parentZoneHashId") != hash_id
        ]
        updated["goZones"].append(
            {
                "hashId": left_id,
                "name": names[0],
                "isEnabled": is_enabled,
                "cutHeight": int(cut_height),
                "polygon": left_poly,
            }
        )
        updated["goZones"].append(
            {
                "hashId": right_id,
                "name": names[1],
                "isEnabled": is_enabled,
                "cutHeight": int(cut_height),
                "polygon": right_poly,
            }
        )
        existing_modified = updated.get("modifyHashs") or []
        updated["modifyHashs"] = [*existing_modified, hash_id, left_id, right_id]
        await self.async_sync_map(thing_name, updated)
        return left_id, right_id

    async def async_update_zone_enabled(self, thing_name: str, hash_id: str, is_enabled: bool) -> None:
        """Enable or disable a go-zone (and its child no-go zones) and push map to robot."""
        import copy

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        updated = copy.deepcopy(map_data)
        for z in updated.get("goZones", []):
            if z.get("hashId") == hash_id:
                z["isEnabled"] = is_enabled
                break
        for z in updated.get("nogoZones", []):
            if z.get("parentZoneHashId") == hash_id:
                z["isEnabled"] = is_enabled
        await self.async_sync_map(thing_name, updated)
