"""Data update coordinator for Lymow."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
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

# How often to re-check the OTA endpoint. Lower than this would just hammer
# AWS API Gateway for no benefit — firmware doesn't change that often.
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
        # OTA_ROBOT_NOT_IN_WAIT means the install never started because the
        # robot wasn't in the waiting state — the job is dead, clear it.
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
        # OTA fields (latestVersion / otaPrefix / otaReleaseNote / otaJobId)
        # live here so they survive coordinator refreshes — the per-refresh
        # rebuild of self.data would otherwise drop them.
        self._ota_state: dict[str, dict[str, Any]] = {}
        # When we last hit /prod/check-update for each device, so we don't
        # spam the endpoint on every 30s coordinator tick.
        self._last_ota_check: dict[str, datetime] = {}
        # Track work status per device to detect important transitions.
        self._prev_work_status: dict[str, int] = {}

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
                await self._maybe_refresh_ota(thing)
                await self._maybe_poll_ota_progress(thing)
                merged = {
                    **rest_data,
                    **self._ota_state.get(thing, {}),
                    **self._mqtt_state.get(thing, {}),
                }
                result[thing] = merged
            return result
        except Exception as err:
            raise UpdateFailed(f"Error fetching Lymow data: {err}") from err

    async def _maybe_refresh_ota(self, thing_name: str) -> None:
        """Refresh the OTA snapshot for one device if our cache is stale.

        Hits /prod/check-update at most once per `_OTA_CHECK_INTERVAL` per
        device. Failures are swallowed and *still* count for the throttle:
        if the endpoint is down we don't want every 30s tick to retry.
        """
        last = self._last_ota_check.get(thing_name)
        now = datetime.now(UTC)
        if last is not None and (now - last) < _OTA_CHECK_INTERVAL:
            return
        # Record the attempt timestamp first, so an exception below still
        # counts for the throttle and we don't hammer a failing endpoint.
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
        """If an OTA job is in flight for this device, poll its status.

        ``async_get_ota_progress`` clears ``otaJobId`` automatically on a
        terminal status, which lets ``update.in_progress`` flip back to
        False without any external caller. Without this poll the entity
        would stay stuck as in-progress for the entire HA process lifetime
        once an install starts.
        """
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
        for src, dst in (
            ("latestVersion", "latestVersion"),
            ("prefix", "otaPrefix"),
            ("releaseNote", "otaReleaseNote"),
        ):
            if src in data:
                out[dst] = data[src]
        return out

    # ------------------------------------------------------------------
    # Commands (published via MQTT)
    # ------------------------------------------------------------------

    def _current_work_status(self, thing_name: str) -> int:
        if self.data:
            return self.data.get(thing_name, {}).get("workStatus", -1)
        return -1

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

        from homeassistant.exceptions import HomeAssistantError

        map_data = (self.data or {}).get(thing_name, {}).get("mapData")
        if not map_data:
            raise HomeAssistantError("Map data not yet loaded — query map first")
        updated = copy.deepcopy(map_data)
        for z in updated.get("goZones", []):
            if z.get("hashId") == hash_id:
                z["cutHeight"] = mm
                break
        await self.async_sync_map(thing_name, updated)

    async def async_check_firmware_update(self, thing_name: str) -> dict[str, Any]:
        """Explicit OTA refresh (e.g. from a service call).

        Always hits the endpoint, updates the persisted OTA snapshot, and
        publishes a fresh top-level data dict so entities see the new value
        without waiting for the next 30 s coordinator tick.
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

        `object_key` is sent verbatim as the ?objectKey= query param to
        /prod/create-ota-job. Callers should pass `otaPrefix + latestVersion`;
        the UpdateEntity does this for them and refuses to install if those
        fields haven't been populated by a prior check_update.
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
        """Merge `patch` into self.data[thing_name] and publish a fresh snapshot.

        No-op when self.data is None (we haven't completed our first
        coordinator refresh yet) or the device isn't in it; in that case
        the values are still preserved in _ota_state and will appear on
        the next refresh.
        """
        if not self.data or thing_name not in self.data:
            return
        new_device = {**self.data[thing_name], **patch}
        self.async_set_updated_data({**self.data, thing_name: new_device})

    async def async_update_zone_enabled(self, thing_name: str, hash_id: str, is_enabled: bool) -> None:
        """Enable or disable a go-zone (and its child no-go zones) and push map to robot."""
        import copy

        from homeassistant.exceptions import HomeAssistantError

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
