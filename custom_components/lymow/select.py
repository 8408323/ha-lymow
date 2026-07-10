"""Select entities for the Lymow Device Settings page."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
# Mowing pattern (globalZoneConfig.cleanMode, field 7). Values pinned to the
# user-selectable codes in const.CLEAN_MODES (0=NONE is not offered).
_MOW_PATTERN_OPTIONS: dict[str, int] = {
    "Zigzag": 1,
    "Adaptive zigzag": 2,
    "Chessboard": 3,
    "Perimeter laps only": 4,
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
        entities.append(MowPatternSelect(coordinator, device))
        entities.append(BackupMapRestoreSelect(coordinator, device))
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
        # Proto3: an absent field equals its default (enum 0), so a robot that
        # never changed this setting off-default resolves to option 0 rather
        # than unknown. A present-but-unknown enum code (future firmware) or a
        # non-int still shows as unknown — we won't invent a label for it.
        if value is None:
            value = 0
        elif not isinstance(value, int):
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


class MowPatternSelect(CoordinatorEntity[LymowCoordinator], SelectEntity):
    """Mowing pattern — globalZoneConfig.cleanMode (Mowing Settings → Global).

    Reads the current pattern from decoded map data
    (``mapData.globalZoneConfig.cleanMode``) and writes via the global
    mowing-settings path (``async_set_task_config``, userCtrl=49). 0=NONE and
    any unknown/future code show as unknown rather than a made-up label.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:sine-wave"
    _attr_options = list(_MOW_PATTERN_OPTIONS)

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Mowing pattern"
        self._attr_unique_id = f"{self._thing_name}_mow_pattern"
        self._attr_device_info = lymow_device_info(self.coordinator, device)
        self._value_to_label = {v: k for k, v in _MOW_PATTERN_OPTIONS.items()}

    @property
    def current_option(self) -> str | None:
        map_data = (self.coordinator.data or {}).get(self._thing_name, {}).get("mapData")
        gzc = map_data.get("globalZoneConfig") if isinstance(map_data, dict) else None
        value = gzc.get("cleanMode") if isinstance(gzc, dict) else None
        if not isinstance(value, int):
            return None
        return self._value_to_label.get(value)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_task_config(self._thing_name, cleanMode=_MOW_PATTERN_OPTIONS[option])


def _backup_label(entry: dict[str, Any], index: int) -> str:
    """Human label for a backup-map entry: its name, else its timestamp, else the file basename."""
    name = entry.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    ts = entry.get("backupTime")
    if ts is not None:
        try:
            return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        except (TypeError, ValueError, OSError):
            pass
    file_key = entry.get("file")
    if isinstance(file_key, str) and file_key.strip():
        return file_key.rsplit("/", 1)[-1]
    return f"Backup {index + 1}"


class BackupMapRestoreSelect(CoordinatorEntity[LymowCoordinator], SelectEntity):
    """One-click restore of a saved map backup.

    An action select: the options are the available backups (from the decoded
    ``backupMapList``); picking one restores that backup onto the robot. There
    is no persistent "current" selection, so ``current_option`` is always None,
    and the select-navigation services (next/previous/first/last) are blocked so
    a generic automation can't trigger a restore without naming a backup.

    Disabled by default — restoring overwrites the live map. Note: restore-map-v2
    does not reliably re-create a zone's child no-go zones (#111), so a restored
    map may be missing no-go areas until the next full re-map.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:map-clock"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Restore backup map"
        self._attr_unique_id = f"{self._thing_name}_restore_backup_map"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    def _label_to_file(self) -> dict[str, str]:
        entries = (self.coordinator.data or {}).get(self._thing_name, {}).get("backupMapList") or []
        # Only entries whose file key is a non-empty string are restorable.
        valid: list[tuple[str, str]] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            file_key = entry.get("file")
            if not isinstance(file_key, str) or not file_key.strip():
                continue
            valid.append((_backup_label(entry, index), file_key))
        base_counts = Counter(base for base, _ in valid)
        out: dict[str, str] = {}
        for base, file_key in valid:
            # Duplicate display names get a STABLE discriminator (the file basename)
            # rather than an order-dependent ordinal, so a label always resolves to
            # the same backup even if the list refreshes between render and click.
            label = base if base_counts[base] == 1 else f"{base} · {file_key.rsplit('/', 1)[-1]}"
            unique = label
            suffix = 2
            while unique in out:  # last resort if basenames also collide
                unique = f"{label} ({suffix})"
                suffix += 1
            out[unique] = file_key
        return out

    @property
    def options(self) -> list[str]:
        return list(self._label_to_file())

    @property
    def current_option(self) -> str | None:
        return None

    async def async_select_option(self, option: str) -> None:
        file_key = self._label_to_file().get(option)
        if file_key is None:
            raise HomeAssistantError(f"Backup {option!r} is no longer available")
        await self.coordinator.async_restore_backup_map(self._thing_name, file_key)

    async def _blocked_navigation(self) -> None:
        raise HomeAssistantError(
            "Pick a specific backup to restore — select navigation is disabled for this "
            "destructive action so it can't overwrite the live map unintentionally."
        )

    async def async_first(self, *_args: Any, **_kwargs: Any) -> None:
        await self._blocked_navigation()

    async def async_last(self, *_args: Any, **_kwargs: Any) -> None:
        await self._blocked_navigation()

    async def async_next(self, *_args: Any, **_kwargs: Any) -> None:
        await self._blocked_navigation()

    async def async_previous(self, *_args: Any, **_kwargs: Any) -> None:
        await self._blocked_navigation()
