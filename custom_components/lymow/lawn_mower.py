"""Lymow lawn mower entity."""

from __future__ import annotations

from homeassistant.components.lawn_mower import LawnMowerActivity, LawnMowerEntity, LawnMowerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    WORK_STATUS_DOCKED_GROUP,
    WORK_STATUS_ERROR_GROUP,
    WORK_STATUS_MOWING_GROUP,
    WORK_STATUS_OFFLINE,
    WORK_STATUS_PAUSED_GROUP,
    WORK_STATUS_RETURNING_GROUP,
)
from .coordinator import LymowCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(LymowMower(coordinator, device) for device in coordinator.devices)


class LymowMower(CoordinatorEntity[LymowCoordinator], LawnMowerEntity):
    _attr_supported_features = (
        LawnMowerEntityFeature.START_MOWING | LawnMowerEntityFeature.PAUSE | LawnMowerEntityFeature.DOCK
    )

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = self._thing_name
        device_label = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = device_label

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
