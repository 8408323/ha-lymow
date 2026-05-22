"""Button entities for discrete Lymow userCtrl commands."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    USER_CTRL_ABORT_OTA,
    USER_CTRL_CHARGING_STATION_RESET,
    USER_CTRL_CLEAR_ALL_ZONES_CHANNELS,
    USER_CTRL_COMPLETE_ZONE_PARTITION,
    USER_CTRL_EXIT_REMOTE,
    USER_CTRL_FORCE_REINIT,
    USER_CTRL_LOCK,
    USER_CTRL_RESTORE_FACTORY,
    USER_CTRL_SELF_CHECKING,
    USER_CTRL_SWITCH_LTE_AIRPLANE,
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
                AbortOtaButton(coordinator, device),
                CompleteZonePartitionButton(coordinator, device),
                ExitRemoteControlButton(coordinator, device),
                RestoreFactoryDefaultsButton(coordinator, device),
                ClearAllZonesAndChannelsButton(coordinator, device),
                ToggleLteAirplaneButton(coordinator, device),
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


class AbortOtaButton(_UserCtrlButton):
    """Cancel an in-flight firmware install. Only meaningful while an OTA is running."""

    _user_ctrl = USER_CTRL_ABORT_OTA
    _key = "abort_ota"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Abort OTA", "mdi:close-octagon")


class CompleteZonePartitionButton(_UserCtrlButton):
    """Exit zone-recording mode after recording is done."""

    _user_ctrl = USER_CTRL_COMPLETE_ZONE_PARTITION
    _key = "complete_zone_partition"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Finish zone recording", "mdi:check-circle-outline")


class ExitRemoteControlButton(_UserCtrlButton):
    """Exit remote-control mode (BLE drive)."""

    _user_ctrl = USER_CTRL_EXIT_REMOTE
    _key = "exit_remote"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Exit remote control", "mdi:gamepad-variant-off")


class RestoreFactoryDefaultsButton(_UserCtrlButton):
    """Reset robot to factory defaults. **Destructive** — disabled by default.

    Press wipes user-side settings and map. Re-pairing is required after.
    """

    _user_ctrl = USER_CTRL_RESTORE_FACTORY
    _key = "restore_factory"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Factory reset", "mdi:restart-alert")


class ClearAllZonesAndChannelsButton(_UserCtrlButton):
    """Wipe every zone and channel from the robot's map. **Very destructive** — disabled by default."""

    _user_ctrl = USER_CTRL_CLEAR_ALL_ZONES_CHANNELS
    _key = "clear_all_zones_channels"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Clear all zones & channels", "mdi:delete-sweep")


class ToggleLteAirplaneButton(_UserCtrlButton):
    """Toggle the robot's LTE airplane mode.

    The command carries no payload — it flips the current state, so it is a
    button rather than a stateful switch. Disabled by default since it affects
    cellular connectivity.
    """

    _user_ctrl = USER_CTRL_SWITCH_LTE_AIRPLANE
    _key = "toggle_lte_airplane"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Toggle LTE airplane mode", "mdi:airplane")
