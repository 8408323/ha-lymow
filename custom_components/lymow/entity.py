"""Shared device-registry info so all Lymow entities group under one device."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import LymowCoordinator

# Suffixes of per-zone entity unique_ids (``{thing}_{hashId}_<suffix>``). Used to
# spot and prune entities for zones that have been deleted from the map.
_ZONE_UNIQUE_ID_SUFFIXES = ("_cut_height", "_enabled")


@callback
def async_prune_stale_zone_entities(hass: HomeAssistant, thing_name: str, valid_hash_ids: set[str]) -> None:
    """Remove registry entries for per-zone entities whose zone is gone from the map.

    Deleting a zone (in the app or HA) leaves its switch/number behind as
    permanently-unavailable registry clutter. Once the map is known (non-empty
    ``valid_hash_ids``), drop any zone entity whose hashId isn't in it. The map is
    replaced atomically, so the live set is always complete — no risk of pruning a
    valid zone mid-update."""
    if not valid_hash_ids:
        return
    registry = er.async_get(hass)
    prefix = f"{thing_name}_"
    for entry in list(registry.entities.values()):
        if entry.platform != DOMAIN or not entry.unique_id.startswith(prefix):
            continue
        rest = entry.unique_id[len(prefix) :]
        for suffix in _ZONE_UNIQUE_ID_SUFFIXES:
            if rest.endswith(suffix):
                hash_id = rest[: -len(suffix)]
                if hash_id and hash_id not in valid_hash_ids:
                    registry.async_remove(entry.entity_id)
                break


def lymow_device_info(coordinator: LymowCoordinator, device: dict[str, Any]) -> DeviceInfo:
    """Build the DeviceInfo for a robot, enriched from live coordinator data when available."""
    thing_name = device["deviceThingName"]
    data = (coordinator.data or {}).get(thing_name) or {}
    name = device.get("deviceName") or data.get("deviceName") or device.get("sn") or thing_name
    # coordinator.data uses merged keys (deviceType / serialNumber / softwareVersion|fwVersion);
    # the raw device-list entry uses sn / deviceType. Check both.
    info = DeviceInfo(
        identifiers={(DOMAIN, thing_name)},
        name=name,
        manufacturer="Lymow",
        model=data.get("deviceType") or device.get("deviceType") or "Robotic Lawn Mower",
    )
    if sn := (data.get("serialNumber") or device.get("sn")):
        info["serial_number"] = sn
    if fw := (data.get("softwareVersion") or data.get("fwVersion")):
        info["sw_version"] = fw
    return info
