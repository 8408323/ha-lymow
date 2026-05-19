"""Button entities for discrete Lymow userCtrl commands."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    USER_CTRL_CHARGING_STATION_RESET,
    USER_CTRL_FORCE_REINIT,
    USER_CTRL_LOCK,
    USER_CTRL_SELF_CHECKING,
)
from .coordinator import LymowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for device in coordinator.devices:
        entities.extend(
            [
                LockRobotButton(coordinator, device),
                SelfCheckButton(coordinator, device),
                ForceReinitButton(coordinator, device),
                ChargingStationResetButton(coordinator, device),
            ]
        )
    if entities:
        async_add_entities(entities)


class _UserCtrlButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    """Base class for buttons that send a userCtrl command via MQTT."""

    _user_ctrl: int = 0
    _key: str = ""

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} {name}"
        self._attr_unique_id = f"{self._thing_name}_{self._key}"
        self._attr_icon = icon

    async def async_press(self) -> None:
        await self.coordinator.async_send_user_ctrl(self._thing_name, self._user_ctrl)


class LockRobotButton(_UserCtrlButton):
    _user_ctrl = USER_CTRL_LOCK
    _key = "lock_robot"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Lock", "mdi:lock")


class SelfCheckButton(_UserCtrlButton):
    _user_ctrl = USER_CTRL_SELF_CHECKING
    _key = "self_check"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Self-check", "mdi:tools")


class ForceReinitButton(_UserCtrlButton):
    """Stop in place and reset to waiting (soft stop)."""

    _user_ctrl = USER_CTRL_FORCE_REINIT
    _key = "force_reinit"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Force stop", "mdi:stop-circle")


class ChargingStationResetButton(_UserCtrlButton):
    """Reset the charging-station location (re-positioning required after press)."""

    _user_ctrl = USER_CTRL_CHARGING_STATION_RESET
    _key = "charging_station_reset"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Reset charging station", "mdi:home-lightning-bolt")
