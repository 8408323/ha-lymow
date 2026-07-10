"""Lymow event entities."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_SESSION_COMPLETED
from .coordinator import LymowCoordinator
from .entity import lymow_device_info

_EVENT_TYPE_SESSION_COMPLETED = "session_completed"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(LymowSessionCompletedEvent(coordinator, device) for device in coordinator.devices)


class LymowSessionCompletedEvent(EventEntity):
    """Fires when a mow session finishes, carrying the session summary.

    Re-emits the ``lymow_session_completed`` bus event (fired once by the
    coordinator on the mowing/returning -> docked transition) as a first-class
    HA event entity, so a mow-complete shows up on dashboards with area /
    duration / end-battery attributes without writing a bus-trigger automation.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:flag-checkered"
    _attr_event_types = [_EVENT_TYPE_SESSION_COMPLETED]

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        self._thing_name = device["deviceThingName"]
        self._attr_name = "Last mow session"
        self._attr_unique_id = f"{self._thing_name}_session_completed"
        self._attr_device_info = lymow_device_info(coordinator, device)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hass.bus.async_listen(EVENT_SESSION_COMPLETED, self._handle_bus_event))

    @callback
    def _handle_bus_event(self, event: Event) -> None:
        if event.data.get("thing_name") != self._thing_name:
            return
        self._trigger_event(_EVENT_TYPE_SESSION_COMPLETED, dict(event.data))
        self.async_write_ha_state()
