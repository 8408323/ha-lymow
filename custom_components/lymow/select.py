"""Select entities for Lymow: tri-state LED modes backed by PbRobotConfig."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
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
    entities: list[SelectEntity] = []
    for device in coordinator.devices:
        entities.append(VehicleLedModeSelect(coordinator, device))
        entities.append(CameraLedModeSelect(coordinator, device))
    if entities:
        async_add_entities(entities)


# Tri-state LED encoding shared by camera & vehicle LEDs (PbRobotConfig int32).
_LED_OPTION_TO_VALUE: dict[str, int] = {"off": 0, "on": 1, "auto": 2}
_LED_VALUE_TO_OPTION: dict[int, str] = {v: k for k, v in _LED_OPTION_TO_VALUE.items()}
_LED_OPTIONS: list[str] = list(_LED_OPTION_TO_VALUE)


class _RobotConfigLedSelect(CoordinatorEntity[LymowCoordinator], SelectEntity):
    """Base for the tri-state (off/on/auto) LED selects.

    State comes from PbOutput.robotConfig (decoded into
    coordinator.data[thing]["robotConfig"]). The decoder doesn't yet surface
    veh/camLedStatus directly — every successful write is mirrored into that
    dict by the coordinator so the entity tracks the user's most recent value
    immediately. ``unavailable`` until first write or first PbOutput frame
    carrying the field.
    """

    _attr_has_entity_name = True
    _attr_options = _LED_OPTIONS
    _attr_entity_registry_enabled_default = False
    _robot_config_key: str = ""

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_{self._robot_config_key}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = name
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        config = (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig") or {}
        return self._robot_config_key in config

    @property
    def current_option(self) -> str | None:
        config = (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig") or {}
        val = config.get(self._robot_config_key)
        if val is None:
            return None
        return _LED_VALUE_TO_OPTION.get(int(val))

    async def async_select_option(self, option: str) -> None:
        value = _LED_OPTION_TO_VALUE[option]
        await self.coordinator.async_set_robot_config(self._thing_name, **{self._robot_config_key: value})


class VehicleLedModeSelect(_RobotConfigLedSelect):
    """Vehicle LED mode (off / on / auto). Distinct from the ``Vehicle LED``
    switch (isOpenLed bool); this select drives the tri-state vehLedStatus
    int that the app uses for the LED's auto-schedule behavior."""

    _robot_config_key = "vehLedStatus"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Vehicle LED mode", "mdi:car-light-high")


class CameraLedModeSelect(_RobotConfigLedSelect):
    """Camera LED mode (off / on / auto)."""

    _robot_config_key = "camLedStatus"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Camera LED mode", "mdi:cctv")
