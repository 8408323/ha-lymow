"""Select entities for the Lymow Device Settings page."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SIGNAL_TURN_OFF_CAMERA_LIGHT,
    SIGNAL_TURN_ON_CAMERA_LIGHT,
    SIGNAL_TURN_ON_CAMERA_LIGHT_LOW,
    SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE,
)
from .coordinator import LymowCoordinator
from .entity import lymow_device_info

# Friendly labels for the app's Device Settings dropdowns. Values map 1:1 to
# the wire enum codes in CHARGING_MODES / ZONE_ORDERS (const.py) — pinned in
# tests so the label↔wire mapping can't drift past the encoder.
_CHARGING_MODE_OPTIONS: dict[str, int] = {
    "Follow perimeter": 0,
    "Direct route": 1,
}
_ZONE_ORDER_OPTIONS: dict[str, int] = {
    "Optimize": 0,
    "Custom": 1,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = []
    for device in coordinator.devices:
        entities.append(ChargingModeSelect(coordinator, device))
        entities.append(ZoneOrderSelect(coordinator, device))
        entities.append(CameraLightSelect(coordinator, device))
    if entities:
        async_add_entities(entities)


class _DeviceSettingsSelect(CoordinatorEntity[LymowCoordinator], SelectEntity):
    """Base class for the Device Settings dropdowns (PbTaskConfig f1/f2).

    Reads from coordinator state at ``mapData.taskConfig.<wire_key>`` (decoded
    from PbMap.f8 by ``decode_task_config``). Writes via the existing
    ``async_set_device_settings`` coordinator method so the encoder and field
    inversions stay in one place.
    """

    _wire_key: str = ""
    _settings_kwarg: str = ""
    _label_to_value: dict[str, int] = {}
    _value_to_label: dict[int, str] = {}
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LymowCoordinator,
        device: dict,
        name: str,
        icon: str,
        unique_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = name
        self._attr_unique_id = f"{self._thing_name}_{unique_suffix}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_icon = icon
        self._attr_options = list(self._label_to_value)

    @property
    def current_option(self) -> str | None:
        tc = (self.coordinator.data or {}).get(self._thing_name, {}).get("mapData", {}).get("taskConfig") or {}
        value = tc.get(self._wire_key)
        # Untrusted wire data: only known enum codes map to a label; anything
        # else (None before first poll, a future firmware enum, or a non-int)
        # shows as unknown rather than silently picking option 0.
        if not isinstance(value, int):
            return None
        return self._value_to_label.get(value)

    async def async_select_option(self, option: str) -> None:
        value = self._label_to_value[option]
        await self.coordinator.async_set_device_settings(self._thing_name, **{self._settings_kwarg: value})


class ChargingModeSelect(_DeviceSettingsSelect):
    """Device Settings → "Return to Dock" route. PbTaskConfig.chargingMode."""

    _wire_key = "chargingMode"
    _settings_kwarg = "charging_mode"
    _label_to_value = _CHARGING_MODE_OPTIONS
    _value_to_label = {v: k for k, v in _CHARGING_MODE_OPTIONS.items()}

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Return-to-dock route", "mdi:routes", "charging_mode")


class ZoneOrderSelect(_DeviceSettingsSelect):
    """Device Settings → Zone order. PbTaskConfig.zoneOrder."""

    _wire_key = "zoneOrder"
    _settings_kwarg = "zone_order"
    _label_to_value = _ZONE_ORDER_OPTIONS
    _value_to_label = {v: k for k, v in _ZONE_ORDER_OPTIONS.items()}

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Zone order", "mdi:order-numeric-ascending", "zone_order")


# Camera headlight brightness levels. Each option dispatches a single SocSignal
# code via PbRobotConfig.signal (the same write path Vehicle LED uses for its
# on/off pair). The robot doesn't echo a brightness value back on pboutput we
# can decode (camLedStatus is enum-checked but we don't have label strings),
# so the entity is optimistic and unknown until the user picks a level.
_CAMERA_LIGHT_OPTIONS: dict[str, int] = {
    "Off": SIGNAL_TURN_OFF_CAMERA_LIGHT,
    "Low": SIGNAL_TURN_ON_CAMERA_LIGHT_LOW,
    "Medium": SIGNAL_TURN_ON_CAMERA_LIGHT_MIDDLE,
    "High": SIGNAL_TURN_ON_CAMERA_LIGHT,
}


class CameraLightSelect(CoordinatorEntity[LymowCoordinator], SelectEntity):
    """Camera headlight brightness — Off / Low / Medium / High.

    Backed by PbRobotConfig.signal one-shot codes (SocSignal): each option
    fires the matching ``SIGNAL_TURN_ON_CAMERA_LIGHT*`` / ``_OFF`` signal
    over the no-userCtrl robotConfig path the app uses (same wiring as the
    Vehicle LED switch). The robot doesn't surface a decoded brightness in
    pboutput, so this is a write-optimistic select: the chosen value is
    cached in memory only and resets to unknown on Home Assistant restart
    or integration reload — re-press to re-state. (Persisting through
    ``RestoreEntity`` would be possible but the cached value can lie if
    the robot was toggled out-of-band, so leaving it as a press-driven
    transient is the more honest default.)
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:car-light-high"
    _attr_options = list(_CAMERA_LIGHT_OPTIONS)
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Camera light"
        self._attr_unique_id = f"{self._thing_name}_camera_light"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._last_choice: str | None = None

    @property
    def current_option(self) -> str | None:
        return self._last_choice

    async def async_select_option(self, option: str) -> None:
        signal_code = _CAMERA_LIGHT_OPTIONS[option]
        await self.coordinator.async_set_robot_config(self._thing_name, signal=signal_code)
        self._last_choice = option
        self.async_write_ha_state()
