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
    USER_CTRL_RECHARGE_DOCK,
    USER_CTRL_RESUME,
    USER_CTRL_RESUME_DOCK,
    WORK_STATUS_DOCKED_GROUP,
    WORK_STATUS_DOCKING,
    WORK_STATUS_ERROR_GROUP,
    WORK_STATUS_MOWING_GROUP,
    WORK_STATUS_PAUSE_DOCKING,
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
