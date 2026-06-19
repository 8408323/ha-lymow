"""Text entities for Lymow — currently the LCD-screen PIN."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    entities = [LymowPinText(coordinator, device) for device in coordinator.devices]
    if entities:
        async_add_entities(entities)


class LymowPinText(CoordinatorEntity[LymowCoordinator], TextEntity):
    """The mower's 4-digit LCD-screen unlock PIN (PbRobotConfig.lcdPin).

    Sensitive, so disabled by default (opt-in) and masked. Setting it writes via
    the same MQTT path as the ``lymow.set_pin`` service; the value is never logged.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_mode = TextMode.PASSWORD
    _attr_native_min = 4
    _attr_native_max = 4
    _attr_pattern = r"^\d{4}$"
    _attr_icon = "mdi:form-textbox-password"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_pin"
        self._attr_name = "Screen PIN"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def native_value(self) -> str | None:
        pin = (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig", {}).get("lcdPin")
        return pin if isinstance(pin, str) and len(pin) == 4 and pin.isdigit() else None

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_pin(self._thing_name, value)
