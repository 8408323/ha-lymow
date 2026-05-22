"""Shared device-registry info so all Lymow entities group under one device."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .coordinator import LymowCoordinator


def lymow_device_info(coordinator: LymowCoordinator, device: dict[str, Any]) -> DeviceInfo:
    """Build the DeviceInfo for a robot, enriched from live coordinator data when available."""
    thing_name = device["deviceThingName"]
    data = (coordinator.data or {}).get(thing_name) or {}
    name = device.get("deviceName") or data.get("deviceName") or device.get("sn") or thing_name
    info = DeviceInfo(
        identifiers={(DOMAIN, thing_name)},
        name=name,
        manufacturer="Lymow",
        model=data.get("model") or device.get("model") or "Robotic Lawn Mower",
    )
    if sn := (device.get("sn") or data.get("sn")):
        info["serial_number"] = sn
    if fw := (data.get("fwVersion") or device.get("fwVersion")):
        info["sw_version"] = fw
    return info
