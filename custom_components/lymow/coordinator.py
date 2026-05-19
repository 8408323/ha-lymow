"""Data update coordinator for Lymow."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LymowApiClient
from .const import (
    DOMAIN,
    POLLING_INTERVAL,
    USER_CTRL_CLEAN,
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

# /get-backup-map is fetched on a longer interval than the main coordinator poll
# (default 30 s) because both backup-map sensors are disabled-by-default and
# backups themselves are written infrequently.
_BACKUP_MAP_REFRESH_INTERVAL = 300


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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """Disconnect MQTT and stop polling."""
        await super().async_shutdown()
        await self._mqtt.disconnect()

    # ------------------------------------------------------------------
    # MQTT callbacks (called from mqtt.py via loop.call_soon_threadsafe)
    # ------------------------------------------------------------------

    def on_mqtt_state(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Receive a state update from MQTT and push to HA."""
        if thing_name not in self._mqtt_state:
            self._mqtt_state[thing_name] = {}
        self._mqtt_state[thing_name].update(patch)
        if self.data and thing_name in self.data:
            merged = {**self.data[thing_name], **patch}
            self.async_set_updated_data({**self.data, thing_name: merged})
        self._check_work_status_transition(thing_name, patch)
        self._check_rtk_guard(thing_name, patch)

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
                merged = {
                    **static_fields,
                    **rest_data,
                    **feature_data,
                    **history_fields,
                    **backup_fields,
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
                {"clean_area": <num>, "clean_time": <int sec>, "date": <epoch>,
                 "used_battery": <int>, "percent": <0..1>, ...},
                ...],
             "total_records": <int>,
             "clean_summary": {"total_clean_time": <int>, "total_clean_area": <num>}}
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
                out["totalCleanTimeS"] = t
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
            out["lastCleanDurationS"] = t
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
        """Send USER_CTRL_QUERY_SCHEDULES to request schedule data from the robot."""
        await self._mqtt.async_publish_command(thing_name, encode_query_schedules())

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

    async def async_start_video_session(self, thing_name: str) -> dict[str, Any]:
        """Open a Kinesis Video Streams viewer session for the robot's camera.

        Returns the channelARN + temporary AWS credentials needed for a
        WebRTC viewer. The HA integration itself does not pipe video bytes
        (that needs aiortc / go2rtc / similar); this is exposed via a service
        so users can plumb the WebRTC handshake into their own stack.
        """
        return await self._client.start_video_session(thing_name)

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
