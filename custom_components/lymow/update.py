"""UpdateEntity for Lymow firmware OTA."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LymowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LymowCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[UpdateEntity] = [
        LymowFirmwareUpdate(coordinator, device) for device in coordinator.devices
    ]
    if entities:
        async_add_entities(entities)


class LymowFirmwareUpdate(CoordinatorEntity[LymowCoordinator], UpdateEntity):
    """Firmware update entity backed by check-update / create-ota-job."""

    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_icon = "mdi:cog-refresh"

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        device_label: str = device.get("deviceName") or device.get("sn") or self._thing_name
        self._attr_name = f"{device_label} Firmware"
        self._attr_unique_id = f"{self._thing_name}_firmware_update"

    @property
    def _device_data(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._thing_name) or {}

    @property
    def installed_version(self) -> str | None:
        return self._device_data.get("softwareVersion")

    @property
    def latest_version(self) -> str | None:
        # Populated by async_check_firmware_update; fall back to installed
        # so HA doesn't show a misleading "update available" indicator before
        # the first check has run.
        return self._device_data.get("latestVersion") or self.installed_version

    @property
    def in_progress(self) -> bool:
        return bool(self._device_data.get("otaJobId"))

    async def async_update(self) -> None:
        """Called by HA on the entity's polling cadence."""
        try:
            await self.coordinator.async_check_firmware_update(self._thing_name)
        except Exception:  # noqa: BLE001
            # Don't kill the entity on a transient OTA-check failure; HA will retry.
            pass

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        object_key = self._device_data.get("otaObjectKey") or version
        if not object_key:
            return
        await self.coordinator.async_install_firmware_update(self._thing_name, object_key)
