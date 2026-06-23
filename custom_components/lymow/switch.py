"""Switch entities for Lymow: per-zone enable + device feature toggles."""

from __future__ import annotations

from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
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
    added: set[tuple[str, str]] = set()

    # One-shot setup: device-feature switches (one set per device)
    feature_entities: list[SwitchEntity] = []
    for device in coordinator.devices:
        feature_entities.extend(
            [
                TheftDetectionSwitch(coordinator, device),
                TheftLockSwitch(coordinator, device),
                FindRobotSwitch(coordinator, device),
                MobileNotificationSwitch(coordinator, device),
                AlertsOnlySwitch(coordinator, device),
                VehicleLedSwitch(coordinator, device),
                Prefer4gSwitch(coordinator, device),
                DockOnErrorSwitch(coordinator, device),
                RainCleaningSwitch(coordinator, device),
                ChargingHandbrakeSwitch(coordinator, device),
                RechargeResumeSwitch(coordinator, device),
                RtkAutoPauseSwitch(coordinator, device),
                AppPresenceSwitch(coordinator, device),
                RtkDiagnosticsPollSwitch(coordinator, device),
            ]
        )
    if feature_entities:
        async_add_entities(feature_entities)

    @callback
    def _add_new_zones() -> None:
        new_entities: list[ZoneEnabledSwitch] = []
        for device in coordinator.devices:
            thing = device["deviceThingName"]
            map_data = (coordinator.data or {}).get(thing, {}).get("mapData") or {}
            for zone in map_data.get("goZones", []):
                key = (thing, zone["hashId"])
                if key not in added:
                    added.add(key)
                    new_entities.append(ZoneEnabledSwitch(coordinator, device, zone["hashId"]))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_zones))
    _add_new_zones()


class _DeviceFeatureSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Base class for boolean device-feature switches backed by /update-device-feature."""

    _feature_key: str = ""
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = name
        self._attr_unique_id = f"{self._thing_name}_{self._feature_key}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        value = data.get(self._feature_key)
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on is True:
            # Already on — skip REST PATCH to avoid duplicate cloud notifications
            # (app may have set it; HA will see the change on the next poll).
            return
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: False})


class TheftDetectionSwitch(_DeviceFeatureSwitch):
    """Anti-theft motion-detection feature (notify on unexpected movement)."""

    _feature_key = "theftDetectionSwitch"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Theft detection", "mdi:shield-alert")


class TheftLockSwitch(_DeviceFeatureSwitch):
    """Anti-theft lock: prevents mowing/movement until unlocked from the app."""

    _feature_key = "theftLock"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Theft lock", "mdi:lock")


class FindRobotSwitch(_DeviceFeatureSwitch):
    """Find-my-robot beep / locate signal toggle."""

    _feature_key = "findRobotSwitch"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Find robot beep", "mdi:bell-ring")


class MobileNotificationSwitch(_DeviceFeatureSwitch):
    """Push-notification master toggle. The wire value is a tristate int (matches
    the app's Notifications page): 0 = off, 1 = alerts only, 2 = all. On/off here
    map to 0 and 2; "alerts only" (1) is exposed as ``AlertsOnlySwitch`` and still
    counts as on."""

    _feature_key = "mobileNotificationSwitch"
    _OFF_VALUE = 0
    _ALERTS_ONLY_VALUE = 1
    _ON_VALUE = 2

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Mobile notifications", "mdi:bell-outline")

    @property
    def is_on(self) -> bool | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        value = data.get(self._feature_key)
        # Untrusted wire data: only 0/1/2 are known. Report unknown for anything
        # else rather than silently claiming "off".
        if value not in (self._OFF_VALUE, self._ALERTS_ONLY_VALUE, self._ON_VALUE):
            return None
        return value in (self._ALERTS_ONLY_VALUE, self._ON_VALUE)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: self._ON_VALUE})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: self._OFF_VALUE})


class AlertsOnlySwitch(_DeviceFeatureSwitch):
    """Mirrors the app's "Alerts only" sub-toggle, backed by the same
    ``mobileNotificationSwitch`` tristate: on = 1 (alerts only), off = 2 (all).
    Unavailable only when the master toggle is explicitly off (0), like the app
    hides the row then."""

    _feature_key = "mobileNotificationSwitch"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Alerts only", "mdi:bell-alert-outline")
        # Shares the mobileNotificationSwitch field with MobileNotificationSwitch,
        # so override the feature-key-derived unique_id to avoid a collision.
        self._attr_unique_id = f"{self._thing_name}_alerts_only"

    @property
    def available(self) -> bool:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        # Available unless notifications are explicitly off; unknown (None, e.g.
        # before the first poll) stays available rather than flickering out.
        return data.get(self._feature_key) != MobileNotificationSwitch._OFF_VALUE

    @property
    def is_on(self) -> bool | None:
        data = (self.coordinator.data or {}).get(self._thing_name) or {}
        value = data.get(self._feature_key)
        if value not in (
            MobileNotificationSwitch._OFF_VALUE,
            MobileNotificationSwitch._ALERTS_ONLY_VALUE,
            MobileNotificationSwitch._ON_VALUE,
        ):
            return None
        return value == MobileNotificationSwitch._ALERTS_ONLY_VALUE

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(
            self._thing_name, **{self._feature_key: MobileNotificationSwitch._ALERTS_ONLY_VALUE}
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_feature(
            self._thing_name, **{self._feature_key: MobileNotificationSwitch._ON_VALUE}
        )


class _RobotConfigBoolSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Base class for bool switches backed by PbInput.robotConfig writes.

    Unlike device-feature switches (REST /update-device-feature), these go over
    MQTT as a PbInput with only the robotConfig submessage set — the robot
    dispatches by submessage shape, no userCtrl. State comes from the
    PbOutput.robotConfig (decoded into coordinator.data[thing]["robotConfig"]).
    """

    _config_key: str = ""
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict, name: str, icon: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = name
        self._attr_unique_id = f"{self._thing_name}_{self._config_key}"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        config = (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig") or {}
        value = config.get(self._config_key)
        # Absent ⇒ proto3 default (False): the robot omits a bool that's off, so
        # an interactive off-toggle is correct, not the ⚡ that None renders as.
        # Present-but-malformed ⇒ unknown (don't coerce untrusted wire data).
        if value is None:
            return False
        if not isinstance(value, bool):
            return None
        return value

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_robot_config(self._thing_name, **{self._config_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_robot_config(self._thing_name, **{self._config_key: False})


class VehicleLedSwitch(_RobotConfigBoolSwitch):
    """Mower's status LED (the app's Device Settings → Vehicle LED toggle).

    Read: PbRobotConfig.isOpenLed (field 7, bool — the persistent state).
    Write: PbRobotConfig.signal (field 8, int) carrying
    ``SIGNAL_TURN_ON_VEHICLE_LIGHT=10`` / ``SIGNAL_TURN_OFF_VEHICLE_LIGHT=11``
    — same one-shot action the app's switchVehicleLed function publishes
    (Hermes fn #9021). The robot reflects the action back into ``isOpenLed``
    on the next pboutput, so the read key matches the write outcome.
    """

    _config_key = "isOpenLed"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Vehicle LED", "mdi:led-on")

    async def async_turn_on(self, **kwargs: Any) -> None:
        from .protocol import SIGNAL_TURN_ON_VEHICLE_LIGHT

        await self.coordinator.async_set_robot_config(self._thing_name, signal=SIGNAL_TURN_ON_VEHICLE_LIGHT)
        self._set_led_optimistic(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        from .protocol import SIGNAL_TURN_OFF_VEHICLE_LIGHT

        await self.coordinator.async_set_robot_config(self._thing_name, signal=SIGNAL_TURN_OFF_VEHICLE_LIGHT)
        self._set_led_optimistic(False)

    def _set_led_optimistic(self, state: bool) -> None:
        if not (self.coordinator.data and self._thing_name in self.coordinator.data):
            return
        existing = self.coordinator.data[self._thing_name]
        rc = {**existing.get("robotConfig", {}), "isOpenLed": state}
        self.coordinator.async_set_updated_data(
            {**self.coordinator.data, self._thing_name: {**existing, "robotConfig": rc}}
        )


class Prefer4gSwitch(_RobotConfigBoolSwitch):
    """Network priority: 4G preferred (on) vs Wi-Fi preferred (off).

    Wire: PbRobotConfig.metric_4g (field 11, bool). True = always prefer
    cellular (may incur data charges), false = prefer Wi-Fi, fall back to 4G
    if it drops — same options the app's Network Settings → Network Priority
    page exposes. The lymow.set_network_priority service is kept alongside
    (semantically equivalent) for automations that prefer the explicit "4g"/
    "wifi" enum.
    """

    _config_key = "metric_4g"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Prefer 4G", "mdi:signal-4g")


class DockOnErrorSwitch(_RobotConfigBoolSwitch):
    """Auto-dock when the mower errors out (app's Device Settings toggle).

    Wire: PbRobotConfig.dockOnError (field 22, bool). When on, after an error
    the mower attempts to return to the dock instead of stopping in place.
    """

    _config_key = "dockOnError"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Auto-dock on error", "mdi:home-alert")


class _DeviceSettingsBoolSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Base class for the Device Settings boolean toggles (PbTaskConfig f3/f4).

    Read from coordinator state at ``mapData.taskConfig.<wire_key>`` (decoded
    from PbMap.f8 by ``decode_task_config``). Write via the existing
    ``async_set_device_settings`` — keeps the encoder and the f4 inversion
    (UI ``charging_handbrake`` vs wire ``disableChargingPark``) in one place.
    """

    _wire_key: str = ""
    _settings_kwarg: str = ""
    _invert_for_ui: bool = False
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

    def _ui_from_wire(self, wire_value: bool) -> bool:
        return not wire_value if self._invert_for_ui else wire_value

    @property
    def is_on(self) -> bool | None:
        tc = (self.coordinator.data or {}).get(self._thing_name, {}).get("mapData", {}).get("taskConfig") or {}
        value = tc.get(self._wire_key)
        # Absent ⇒ proto3 default (wire False), then apply the UI inversion — so
        # e.g. an omitted disableChargingPark reads as "handbrake on", not None
        # (which renders as ⚡). Present-but-malformed ⇒ unknown, don't coerce.
        if value is None:
            value = False
        elif not isinstance(value, bool):
            return None
        return self._ui_from_wire(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_settings(self._thing_name, **{self._settings_kwarg: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_device_settings(self._thing_name, **{self._settings_kwarg: False})


class RainCleaningSwitch(_DeviceSettingsBoolSwitch):
    """Device Settings → Rainy mowing. PbTaskConfig.rainCleaning (f3, bool)."""

    _wire_key = "rainCleaning"
    _settings_kwarg = "rainy_mowing"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Rainy mowing", "mdi:weather-rainy", "rainy_mowing")


class ChargingHandbrakeSwitch(_DeviceSettingsBoolSwitch):
    """Device Settings → Charging handbrake. PbTaskConfig.disableChargingPark
    (f4, bool) — inverted so the HA toggle reads in the UI's positive sense:
    ON means "engage the handbrake while charging" = wire ``False``.
    """

    _wire_key = "disableChargingPark"
    _settings_kwarg = "charging_handbrake"
    _invert_for_ui = True

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Charging handbrake", "mdi:car-brake-parking", "charging_handbrake")


class RechargeResumeSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Recharge & Resume master toggle.

    Wire: ``PbRobotConfig.rrConfig.enableRr`` (PbRRConfig f1, bool).
    Decoded into coordinator state as ``rrConfig['enable']`` by
    ``decode_rr_config`` (the wire name is renamed to drop the redundant
    ``Rr`` prefix once it's already inside ``rrConfig``).

    Period start/end and the two battery thresholds are exposed separately
    (period times as ``extra_state_attributes`` for now; the thresholds as
    Number entities). Writes go via the no-userCtrl PbInput.robotConfig
    path the app uses for setRrConfig.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:battery-sync"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Recharge & resume"
        self._attr_unique_id = f"{self._thing_name}_recharge_resume"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def _rr_config(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._thing_name, {}).get("robotConfig", {}).get("rrConfig") or {}

    @property
    def is_on(self) -> bool | None:
        value = self._rr_config.get("enable")
        # Absent ⇒ proto3 default (off); present-but-malformed ⇒ unknown.
        if value is None:
            return False
        if not isinstance(value, bool):
            return None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        rr = self._rr_config
        attrs: dict[str, Any] = {}
        for key, label in (("periodStart", "period_start"), ("periodEnd", "period_end")):
            t = rr.get(key)
            if isinstance(t, dict) and "hour" in t and "minute" in t:
                attrs[label] = f"{t['hour']:02d}:{t['minute']:02d}"
        return attrs or None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_recharge_resume(self._thing_name, enable=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_recharge_resume(self._thing_name, enable=False)


class ZoneEnabledSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Enable / disable a single go-zone. Backed by SYNC_MAP on toggle."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:map-marker-radius"

    def __init__(self, coordinator: LymowCoordinator, device: dict, hash_id: str) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._hash_id = hash_id
        self._attr_unique_id = f"{self._thing_name}_{hash_id}_enabled"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = f"Zone {hash_id[:4]}"

    @property
    def _zone(self) -> dict[str, Any] | None:
        map_data = (self.coordinator.data or {}).get(self._thing_name, {}).get("mapData") or {}
        for z in map_data.get("goZones", []):
            if z.get("hashId") == self._hash_id:
                return z
        return None

    @property
    def available(self) -> bool:
        return self._zone is not None

    @property
    def is_on(self) -> bool | None:
        z = self._zone
        return bool(z.get("isEnabled", True)) if z is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_zone_enabled(self._thing_name, self._hash_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_zone_enabled(self._thing_name, self._hash_id, False)


class RtkAutoPauseSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity):
    """Opt-in safety switch: when on, the coordinator auto-pauses the mower
    if RTK status drops to or below the configured threshold, and auto-resumes
    once it recovers — protects against the mower wandering on a degraded fix."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:satellite-variant"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_rtk_auto_pause"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._attr_name = "RTK auto-pause"

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_rtk_guard_enabled(self._thing_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_rtk_guard_enabled(self._thing_name, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_rtk_guard_enabled(self._thing_name, False)
        self.async_write_ha_state()


class AppPresenceSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity, RestoreEntity):
    """Send the app-presence heartbeat so the robot treats HA like a connected app.

    Separate from RTK polling because registering presence may affect other robot
    behaviour (it thinks an app is watching). Off by default. Turning it off also
    stops RTK diagnostics, which can't work without presence.
    """

    _attr_has_entity_name = True
    _attr_name = "App presence"
    _attr_icon = "mdi:cellphone-link"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_app_presence"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self.coordinator.set_presence(self._thing_name, True)
            self.coordinator.async_update_listeners()

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_presence_on(self._thing_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.set_presence(self._thing_name, True)
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_presence(self._thing_name, False)
        self.coordinator.async_update_listeners()


class RtkDiagnosticsPollSwitch(CoordinatorEntity[LymowCoordinator], SwitchEntity, RestoreEntity):
    """Retrieve RTK diagnostics continuously (queries L1+L2 on a fast timer) so the
    RTK sensors stay live without the Lymow app. Requires App presence; enabling
    this auto-enables it. Off by default — it's continuous MQTT traffic.
    """

    _attr_has_entity_name = True
    _attr_name = "RTK diagnostics"
    _attr_icon = "mdi:radio-tower"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name = device["deviceThingName"]
        self._attr_unique_id = f"{self._thing_name}_rtk_diagnostics_poll"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == "on":
            self.coordinator.set_rtk_polling(self._thing_name, True)
            self.coordinator.async_update_listeners()

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_rtk_polling(self._thing_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        presence_added = self.coordinator.set_rtk_polling(self._thing_name, True)
        if presence_added:
            persistent_notification.async_create(
                self.hass,
                "RTK diagnostics retrieval also turned on the **App presence** switch — "
                "the robot only streams RTK detail while it thinks an app is connected. "
                "You'll find it next to RTK diagnostics under this mower's Diagnostic "
                "controls. To stop everything, turn **App presence** off; to keep presence "
                "but stop the RTK queries, just turn **RTK diagnostics** off.",
                title="Lymow: App presence enabled for RTK",
                notification_id=f"lymow_rtk_presence_{self._thing_name}",
            )
        self.coordinator.async_update_listeners()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.set_rtk_polling(self._thing_name, False)
        self.coordinator.async_update_listeners()
