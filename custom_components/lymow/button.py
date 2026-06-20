"""Button entities for discrete Lymow userCtrl commands."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SIGNAL_TURN_OFF_CAMERA_LIGHT,
    USER_CTRL_ABORT_OTA,
    USER_CTRL_CHARGING_STATION_RESET,
    USER_CTRL_CLEAR_ALL_ZONES_CHANNELS,
    USER_CTRL_COMPLETE_ZONE_PARTITION,
    USER_CTRL_DOCK,
    USER_CTRL_EXIT_REMOTE,
    USER_CTRL_FLOOR_BACKUP,
    USER_CTRL_FORCE_REINIT,
    USER_CTRL_LOCK,
    USER_CTRL_MODIFY_STATION,
    USER_CTRL_RESTORE_FACTORY,
    USER_CTRL_SELF_CHECKING,
    USER_CTRL_SWITCH_LTE_AIRPLANE,
)
from .coordinator import LymowCoordinator
from .entity import lymow_device_info


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
                CancelTaskButton(coordinator, device),
                SelfCheckButton(coordinator, device),
                ForceReinitButton(coordinator, device),
                ChargingStationResetButton(coordinator, device),
                SetChargingStationHereButton(coordinator, device),
                AbortOtaButton(coordinator, device),
                CompleteZonePartitionButton(coordinator, device),
                ExitRemoteControlButton(coordinator, device),
                RestoreFactoryDefaultsButton(coordinator, device),
                ClearAllZonesAndChannelsButton(coordinator, device),
                ToggleLteAirplaneButton(coordinator, device),
                BackupMapButton(coordinator, device),
                FindMyRobotPlaySoundButton(coordinator, device),
                SyncTimezoneButton(coordinator, device, hass),
                BtBroadcastButton(coordinator, device),
                CameraLightOffNowButton(coordinator, device),
            ]
        )
    if entities:
        async_add_entities(entities)


class SyncTimezoneButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    """One-shot "Sync timezone with Home Assistant" — equivalent to the app's
    "Sync with Phone" button on Settings → Device Settings → Timezone.

    Writes ``PbRobotConfig.timezoneOffset`` (f21) with the current HA
    timezone's offset from UTC in seconds, matching what the app's
    ``setTimezone`` (Hermes #9036) does with the phone's local timezone.
    DST is implicit in the offset because we compute it at press time —
    the robot stores a frozen number; users on DST-observing regions can
    re-press after the transition or automate it on the HA `time_changed`
    event."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:earth"

    def __init__(self, coordinator: LymowCoordinator, device: dict, hass: HomeAssistant) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._hass = hass
        self._attr_name = "Sync timezone"
        self._attr_unique_id = f"{self._thing_name}_sync_timezone"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    async def _current_offset_seconds(self) -> int:
        # Resolve HA's configured time_zone string (e.g. "Europe/Stockholm") to
        # a zoneinfo and read its offset right now. ``async_get_time_zone``
        # offloads the (potentially disk-touching) ZoneInfo construction to
        # the executor so we don't block the event loop on a cache miss.
        # ``utcoffset()`` returns a timedelta with whole-second resolution at
        # most — round to int seconds so the wire value matches what the app
        # would write from the phone.
        tz_name = self._hass.config.time_zone or "UTC"
        tz = await dt_util.async_get_time_zone(tz_name) or dt_util.UTC
        offset = datetime.now(tz).utcoffset()
        return int(offset.total_seconds()) if offset is not None else 0

    async def async_press(self) -> None:
        await self.coordinator.async_sync_timezone(self._thing_name, await self._current_offset_seconds())


class BtBroadcastButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    """Manually trigger the mower to start advertising BLE so HA/the app can
    reconnect without a power cycle.

    Wire: ``PbRobotConfig.signal = SocSignal.SIGNAL_TURN_ON_BT_BROADCAST (12)``
    over the no-userCtrl robotConfig path (Hermes #9506 PbRobotConfig encoder).
    Disabled by default — most installs don't need it, and a stray press can
    interfere with an in-flight pairing on the phone app.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:bluetooth-connect"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Re-advertise Bluetooth"
        self._attr_unique_id = f"{self._thing_name}_bt_broadcast"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    async def async_press(self) -> None:
        from .const import SIGNAL_TURN_ON_BT_BROADCAST

        await self.coordinator.async_set_robot_config(self._thing_name, signal=SIGNAL_TURN_ON_BT_BROADCAST)


class CameraLightOffNowButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    """One-shot "Turn camera light off now" — fires the same SocSignal the
    app fires when a user disables Night Mode (per Hermes ``setNightMode``
    #9019: it tacks ``SIGNAL_TURN_OFF_CAMERA_LIGHT`` onto its schedule write
    to kill the light immediately, regardless of where in the window we are).

    **Does NOT disable the Night Mode schedule** — the next scheduled open
    time will still turn the light back on. Use ``lymow.set_night_mode``
    with ``enable=false`` if you want to suppress the schedule too. Handy
    here for automation shortcuts like a motion-triggered "lights out".
    Functionally overlaps with ``CameraLightSelect.select_option("Off")``
    (which also exposes Low/Medium/High brightness), so disabled by default
    to avoid double entries on the device card.

    Wire: ``PbRobotConfig.signal = SIGNAL_TURN_OFF_CAMERA_LIGHT (7)`` over
    the no-userCtrl robotConfig path.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:lightbulb-off"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Camera light off now"
        self._attr_unique_id = f"{self._thing_name}_camera_light_off_now"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    async def async_press(self) -> None:
        await self.coordinator.async_set_robot_config(self._thing_name, signal=SIGNAL_TURN_OFF_CAMERA_LIGHT)


class _UserCtrlButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    """Base class for buttons that send a userCtrl command via MQTT."""

    _user_ctrl: int = 0
    _key: str = ""
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = name
        self._attr_unique_id = f"{self._thing_name}_{self._key}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
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


class CancelTaskButton(_UserCtrlButton):
    """Cancel the current mowing task and return to dock — the app's
    Settings → Cancel Task action. Distinct from the lawn-mower entity's
    DOCK service (RECHARGE_DOCK=33, which preserves task progress); this
    one (USER_CTRL_DOCK=2) ends the task before docking.
    """

    _user_ctrl = USER_CTRL_DOCK
    _key = "cancel_task"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Cancel task", "mdi:cancel")


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


class SetChargingStationHereButton(_UserCtrlButton):
    """Record the robot's current position as the new charging-station location.

    Counterpart to ``ChargingStationResetButton``: that one clears the recorded
    station so the robot has to relearn its position; this one captures wherever
    the mower is parked right now as the station's map location (the app's
    "Set station here" action; payload-less command per APK startCtrlCharging
    encoder).
    """

    _user_ctrl = USER_CTRL_MODIFY_STATION
    _key = "set_charging_station_here"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Set charging station here", "mdi:home-map-marker")


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


class BackupMapButton(_UserCtrlButton):
    """Save a backup of the robot's current map (the app's "Back up" action).

    The robot snapshots its map to cloud storage; restore it later with the
    restore_backup_map service. Verified against hardware (USER_CTRL_FLOOR_BACKUP).
    """

    _user_ctrl = USER_CTRL_FLOOR_BACKUP
    _key = "backup_map"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Back up map", "mdi:content-save-cog")

    async def async_press(self) -> None:
        # Route through the coordinator so the backup-map cache is invalidated
        # and the backup sensors reflect the new snapshot on the next poll.
        await self.coordinator.async_backup_map(self._thing_name)


class FindMyRobotPlaySoundButton(CoordinatorEntity[LymowCoordinator], ButtonEntity):
    """Trigger the app's "Find My Robot → Play Sound" beacon.

    Sends a one-shot wire frame (`PbInput {f13.audioVolume=100, f16=1}`) that
    makes the robot beep so the owner can locate it. Captured live 2026-05-27
    from the app's Settings → Find My Robot → Play Sound flow.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:bullhorn"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Find my robot (play sound)"
        self._attr_unique_id = f"{self._thing_name}_find_my_robot_play_sound"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    async def async_press(self) -> None:
        await self.coordinator.async_find_my_robot_play_sound(self._thing_name)
