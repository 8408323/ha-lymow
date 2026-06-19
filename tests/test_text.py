"""Tests for text.py — the LCD-screen PIN text entity."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from lymow.text import LymowPinText, async_setup_entry

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _coord(pin=None):
    c = MagicMock()
    c.devices = [DEVICE]
    c.data = {THING: {"robotConfig": {"lcdPin": pin}}} if pin is not None else {THING: {"robotConfig": {}}}
    c.async_set_pin = AsyncMock()
    return c


def test_pin_text_metadata_disabled_and_masked() -> None:
    from homeassistant.components.text import TextMode
    from homeassistant.const import EntityCategory

    e = LymowPinText(_coord(), DEVICE)
    assert e._attr_unique_id == f"{THING}_pin"
    assert e._attr_name == "Screen PIN"
    assert e._attr_entity_registry_enabled_default is False  # opt-in: sensitive
    assert e._attr_entity_category == EntityCategory.CONFIG
    assert e._attr_mode == TextMode.PASSWORD
    assert e._attr_pattern == r"^\d{4}$"
    assert e._attr_native_min == 4 and e._attr_native_max == 4


def test_pin_text_native_value_reads_lcd_pin() -> None:
    assert LymowPinText(_coord("1234"), DEVICE).native_value == "1234"


def test_pin_text_native_value_none_when_absent_or_malformed() -> None:
    assert LymowPinText(_coord(), DEVICE).native_value is None
    assert LymowPinText(_coord("12"), DEVICE).native_value is None  # wrong length
    assert LymowPinText(_coord("abcd"), DEVICE).native_value is None  # non-digit


@pytest.mark.asyncio
async def test_pin_text_set_value_calls_coordinator() -> None:
    coord = _coord()
    await LymowPinText(coord, DEVICE).async_set_value("4321")
    coord.async_set_pin.assert_awaited_once_with(THING, "4321")


async def test_async_setup_entry_adds_one_per_device() -> None:
    coord = _coord()
    hass = MagicMock()
    hass.data = {"lymow": {"e1": coord}}
    entry = MagicMock()
    entry.entry_id = "e1"
    added: list = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert len(added) == 1 and isinstance(added[0], LymowPinText)
