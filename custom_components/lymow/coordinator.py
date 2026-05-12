"""Data update coordinator for Lymow."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import LymowApiClient
from .const import (
    DOMAIN,
    POLLING_INTERVAL,
    REGION_CONFIG,
    USER_CTRL_CLEAN,
    USER_CTRL_PAUSE,
    USER_CTRL_PAUSE_DOCK,
    USER_CTRL_RECHARGE_DOCK,
    USER_CTRL_RESUME,
    USER_CTRL_RESUME_DOCK,
    WORK_STATUS_DOCKING,
    WORK_STATUS_PAUSE_DOCKING,
)
from .mqtt import LymowMqttClient
from .protocol import encode_userctrl

_LOGGER = logging.getLogger(__name__)


class LymowCoordinator(DataUpdateCoordinator[dict[str, Any]]):
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
        self._client      = client
        self._mqtt        = mqtt_client
        self.devices      = devices
        # Live MQTT state patches, merged into data on each REST poll
        self._mqtt_state: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # MQTT callbacks (called from mqtt.py via loop.call_soon_threadsafe)
    # ------------------------------------------------------------------

    def on_mqtt_state(self, thing_name: str, patch: dict[str, Any]) -> None:
        """Receive a state update from MQTT and push to HA."""
        if thing_name not in self._mqtt_state:
            self._mqtt_state[thing_name] = {}
        self._mqtt_state[thing_name].update(patch)
        # Merge into current coordinator data and notify listeners immediately
        if self.data and thing_name in self.data:
            merged = {**self.data[thing_name], **patch}
            self.async_set_updated_data({**self.data, thing_name: merged})

    def on_mqtt_online(self, thing_name: str, is_online: bool) -> None:
        """Receive an online/offline notification from MQTT."""
        patch = {"isOnline": is_online, "deviceState": "online" if is_online else "offline"}
        self.on_mqtt_state(thing_name, patch)

    # ------------------------------------------------------------------
    # REST polling
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            result: dict[str, Any] = {}
            for device in self.devices:
                thing = device["deviceThingName"]
                rest_data = await self._client.get_device_info(thing)
                # Overlay any live MQTT state on top of the REST snapshot
                merged = {**rest_data, **self._mqtt_state.get(thing, {})}
                result[thing] = merged
            return result
        except Exception as err:
            raise UpdateFailed(f"Error fetching Lymow data: {err}") from err

    # ------------------------------------------------------------------
    # Commands (published via MQTT)
    # ------------------------------------------------------------------

    def _current_work_status(self, thing_name: str) -> int:
        if self.data:
            return self.data.get(thing_name, {}).get("workStatus", -1)
        return -1

    async def async_start_mowing(self, thing_name: str) -> None:
        self._mqtt.publish_command(thing_name, encode_userctrl(USER_CTRL_CLEAN))

    async def async_pause(self, thing_name: str) -> None:
        ws = self._current_work_status(thing_name)
        ctrl = USER_CTRL_PAUSE_DOCK if ws == WORK_STATUS_DOCKING else USER_CTRL_PAUSE
        self._mqtt.publish_command(thing_name, encode_userctrl(ctrl))

    async def async_dock(self, thing_name: str) -> None:
        ws = self._current_work_status(thing_name)
        ctrl = USER_CTRL_RESUME_DOCK if ws == WORK_STATUS_PAUSE_DOCKING else USER_CTRL_RECHARGE_DOCK
        self._mqtt.publish_command(thing_name, encode_userctrl(ctrl))

    async def async_resume(self, thing_name: str) -> None:
        ws = self._current_work_status(thing_name)
        ctrl = USER_CTRL_RESUME_DOCK if ws == WORK_STATUS_PAUSE_DOCKING else USER_CTRL_RESUME
        self._mqtt.publish_command(thing_name, encode_userctrl(ctrl))
