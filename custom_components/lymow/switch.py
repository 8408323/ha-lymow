"""Switch entities for Lymow: per-zone enable + device feature toggles."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
                RtkAutoPauseSwitch(coordinator, device),
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
        await self.coordinator.async_set_device_feature(self._thing_name, **{self._feature_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
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
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_robot_config(self._thing_name, **{self._config_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_robot_config(self._thing_name, **{self._config_key: False})


class VehicleLedSwitch(_RobotConfigBoolSwitch):
    """Mower's status LED (the app's Device Settings → Vehicle LED toggle).

    Wire: PbRobotConfig.isOpenLed (field 7, bool).
    """

    _config_key = "isOpenLed"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator, device, "Vehicle LED", "mdi:led-on")


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
