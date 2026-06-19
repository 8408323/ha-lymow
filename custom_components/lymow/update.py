"""UpdateEntity for Lymow firmware OTA."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    entities: list[UpdateEntity] = [LymowFirmwareUpdate(coordinator, device) for device in coordinator.devices]
    if entities:
        async_add_entities(entities)


class LymowFirmwareUpdate(CoordinatorEntity[LymowCoordinator], UpdateEntity):
    """Firmware update entity backed by check-update / create-ota-job."""

    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    _attr_icon = "mdi:cog-refresh"
    _attr_has_entity_name = True

    def __init__(self, coordinator: LymowCoordinator, device: dict) -> None:
        super().__init__(coordinator)
        self._thing_name: str = device["deviceThingName"]
        self._attr_name = "Firmware"
        self._attr_unique_id = f"{self._thing_name}_firmware_update"
        self._attr_device_info = lymow_device_info(self.coordinator, device)

    @property
    def _device_data(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._thing_name) or {}

    @property
    def installed_version(self) -> str | None:
        return self._device_data.get("softwareVersion")

    @property
    def latest_version(self) -> str | None:
        # Fall back to installed_version so HA doesn't show "update available"
        # before the first check_update has populated latestVersion.
        raw = self._device_data.get("latestVersion") or self.installed_version
        # The OTA API returns versions like "v2.1.48.1_20260528" (base version + date
        # suffix). Strip the suffix so HA compares against the installed "v2.1.48.1"
        # without showing a false update-available notification.
        if raw and "_" in raw:
            raw = raw.split("_")[0]
        return raw

    @property
    def in_progress(self) -> bool:
        return bool(self._device_data.get("otaJobId"))

    @property
    def release_summary(self) -> str | None:
        # release_summary is the short blurb (HA caps it at 255 chars). The
        # full text is delivered through async_release_notes — keeping both
        # avoids the "Unknown error" the frontend raises when an entity
        # advertises UpdateEntityFeature.RELEASE_NOTES without overriding
        # async_release_notes.
        return self._formatted_release_note()

    async def async_release_notes(self) -> str | None:
        # Full release notes for the modal in HA's UI; the RELEASE_NOTES
        # feature flag enables this code path and the frontend calls it
        # whenever the entity card is opened.
        return self._formatted_release_note()

    def _formatted_release_note(self) -> str | None:
        # otaReleaseNote arrives with literal "\n" escape sequences — render
        # them as real newlines so HA renders multi-line text properly.
        note = self._device_data.get("otaReleaseNote")
        if not isinstance(note, str):
            return None
        return note.replace("\\n", "\n")

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Install the latest firmware.

        HA passes ``version`` as a target-version string, but create-ota-job
        expects the ``objectKey`` from check_update — not a version string.
        Using ``version`` directly would start an invalid OTA, so if we
        haven't cached a check_update response we raise rather than guess.
        """
        latest = self._device_data.get("latestVersion")
        if not latest:
            raise HomeAssistantError(
                "No firmware-update info cached yet — wait for the next "
                "coordinator OTA refresh (within 6 h) before installing."
            )
        prefix = self._device_data.get("otaPrefix") or ""
        await self.coordinator.async_install_firmware_update(self._thing_name, f"{prefix}{latest}")
