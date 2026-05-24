"""Data update coordinator for Lymow."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LymowApiClient
from .bluetooth import LymowBleController
from .const import (
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
    USER_CTRL_QUERY_ROBOT_CONFIG,
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
    encode_query_schedules,
    encode_start_zones,
    encode_sync_map,
    encode_userctrl,
)

_LOGGER = logging.getLogger(__name__)


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
        merged_patch = self._merge_nested_patch(self._mqtt_state.setdefault(thing_name, {}), patch)
        self._mqtt_state[thing_name].update(merged_patch)
        if self.data and thing_name in self.data:
            existing = self.data[thing_name]
            merged_patch_for_data = self._merge_nested_patch(existing, patch)
            merged = {**existing, **merged_patch_for_data}
            self.async_set_updated_data({**self.data, thing_name: merged})
        self._check_work_status_transition(thing_name, patch)
        self._check_rtk_guard(thing_name, patch)

    def _merge_nested_patch(self, existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of patch where each ``_DEEP_MERGE_KEYS`` dict is overlaid
        on the matching dict in ``existing`` (one level deep) so partial replies
        keep keys they don't mention. Non-dict patch values are passed through."""
        if not any(k in patch for k in self._DEEP_MERGE_KEYS):
            return patch
        out = dict(patch)
        for key in self._DEEP_MERGE_KEYS:
            new = patch.get(key)
            old = existing.get(key)
            if isinstance(new, dict) and isinstance(old, dict):
                out[key] = {**old, **new}
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
        if not is_online:
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
    # REST polling
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
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
                result[thing] = merged
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
        """Push an edited map to the robot via SYNC_MAP command."""
        await self._mqtt.async_publish_command(thing_name, encode_sync_map(map_data))

    async def async_delete_zone(self, thing_name: str, hash_id: str) -> None:
        """Delete a go-zone by hashId using USER_CTRL_CLEAR_ZONE=8."""
        await self._mqtt.async_publish_command(thing_name, encode_delete_zone(hash_id))

    async def async_rename_zone(self, thing_name: str, hash_id: str, name: str) -> None:
        """Rename a go-zone by hashId using USER_CTRL_MODIFY_ZONE_INFO=9."""
        from .protocol import encode_rename_zone

        await self._mqtt.async_publish_command(thing_name, encode_rename_zone(hash_id, name))

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

    async def async_set_task_config(self, thing_name: str, **fields: Any) -> None:
        """Set mowing task-config parameters (USER_CTRL_SET_TASK_CONFIG).

        Only the provided PbTaskConfig fields are sent; see
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

    async def async_set_robot_config(self, thing_name: str, **fields: Any) -> None:
        """Set PbRobotConfig fields on the robot — currently just network priority.

        These writes don't set userCtrl — the robot dispatches by the presence
        of the robotConfig submessage. Supported field names are listed in
        :data:`protocol._ROBOT_CONFIG_BOOL_FIELDS` (extend it, and
        :func:`protocol.encode_set_robot_config`, to add non-bool fields).
        """
        from .protocol import encode_set_robot_config

        await self._mqtt.async_publish_command(thing_name, encode_set_robot_config(**fields))

    async def async_set_run_time_config(self, thing_name: str, **fields: Any) -> None:
        """Set run-time config parameters (USER_CTRL_SET_RUN_TIME_CONFIG).

        Unlike task-config (which is the next-mow default), run-time-config
        overrides settings on the currently-running task. Only the provided
        PbRunTimeConfig fields are sent; see :data:`protocol._RUN_TIME_CONFIG_FIELDS`
        for the supported names.
        """
        from .protocol import encode_set_run_time_config

        await self._mqtt.async_publish_command(thing_name, encode_set_run_time_config(**fields))

    async def _publish_userctrl(self, thing_name: str, code: int) -> None:
        """Publish a bare ``userCtrl=code`` pbinput — for the read-only QUERY_*
        commands that the robot answers via pboutput."""
        await self._mqtt.async_publish_command(thing_name, encode_userctrl(code))

    async def async_query_cleaning_info(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_CLEANING_INFO)

    async def async_query_cleaning_summary(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_CLEANING_SUMMARY)

    async def async_query_robot_config(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_ROBOT_CONFIG)

    async def async_query_path(self, thing_name: str) -> None:
        await self._publish_userctrl(thing_name, USER_CTRL_QUERY_PATH)

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
            self._publish_ota_patch(thing_name, patch)
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
        self._publish_ota_patch(thing_name, {"otaJobId": job_id})
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
            self._publish_ota_patch(thing_name, {"otaJobId": None})
        return result

    def _publish_ota_patch(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Merge ``patch`` into self.data[thing_name] and publish a fresh snapshot."""
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
