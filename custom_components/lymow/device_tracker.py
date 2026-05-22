"""device_tracker for the robot's reported location (REST robotLocation)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LymowCoordinator
from .entity import lymow_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TrackerEntity] = [LymowDeviceTracker(coordinator, device) for device in coordinator.devices]
    if entities:
        async_add_entities(entities)


class LymowDeviceTracker(CoordinatorEntity[LymowCoordinator], TrackerEntity):
    """Tracker showing the robot's last reported GPS coordinate.

    robotLocation comes from /prod/get-device-info as [lat, lon]. When the
    field is missing or the coordinates are obviously unset (both zero),
    we expose None so HA shows the tracker as "unknown" rather than
    pinning the map to (0, 0) in the Atlantic.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:robot-mower"
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Location"
        self._attr_unique_id = f"{self._thing_name}_location"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def _device_data(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._thing_name) or {}

    @property
    def _coords(self) -> tuple[float, float] | None:
        loc = self._device_data.get("robotLocation")
        if not isinstance(loc, list) or len(loc) < 2:
            return None
        try:
            lat = float(loc[0])
            lon = float(loc[1])
        except (TypeError, ValueError):
            return None
        if lat == 0.0 and lon == 0.0:
            return None
        return (lat, lon)

    @property
    def latitude(self) -> float | None:
        c = self._coords
        return c[0] if c else None

    @property
    def longitude(self) -> float | None:
        c = self._coords
        return c[1] if c else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        data = self._device_data
        if (sn := data.get("sn")) is not None:
            attrs["serial"] = sn
        if (state := data.get("deviceState")) is not None:
            attrs["device_state"] = state
        if (stolen := data.get("stolenStatus")) is not None:
            attrs["stolen"] = bool(stolen)
        return attrs
