"""Tests for event.py — the session-completed event entity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.conftest import _load_lymow_module

_load_lymow_module("event")

from lymow.const import DOMAIN  # noqa: E402
from lymow.event import LymowSessionCompletedEvent, async_setup_entry  # noqa: E402

THING = "mower-001"
DEVICE = {"deviceThingName": THING, "deviceName": "Mower 1"}


def _make_entity() -> LymowSessionCompletedEvent:
    e = LymowSessionCompletedEvent(MagicMock(), DEVICE)
    e.hass = MagicMock()
    e.async_write_ha_state = MagicMock()
    return e


def test_entity_metadata() -> None:
    e = _make_entity()
    assert e._attr_unique_id == f"{THING}_session_completed"
    assert e._attr_name == "Last mow session"
    assert e._attr_event_types == ["session_completed"]


async def test_async_setup_entry_adds_one_per_device() -> None:
    coord = MagicMock()
    coord.devices = [DEVICE, {"deviceThingName": "m2", "deviceName": "M2"}]
    hass = MagicMock()
    hass.data = {DOMAIN: {"e1": coord}}
    entry = MagicMock()
    entry.entry_id = "e1"

    added: list = []
    await async_setup_entry(hass, entry, lambda ents: added.extend(ents))
    assert len(added) == 2
    assert all(isinstance(e, LymowSessionCompletedEvent) for e in added)


async def test_added_to_hass_subscribes_to_the_session_event() -> None:
    e = _make_entity()
    e.async_on_remove = MagicMock()
    await e.async_added_to_hass()
    e.hass.bus.async_listen.assert_called_once()
    assert e.hass.bus.async_listen.call_args[0][0] == "lymow_session_completed"


def test_handle_event_triggers_for_matching_thing() -> None:
    e = _make_entity()
    event = SimpleNamespace(data={"thing_name": THING, "area_m2": 12.3, "end_battery_pct": 80})
    e._handle_bus_event(event)
    assert e._last_event_type == "session_completed"
    assert e._last_event_attributes["area_m2"] == 12.3
    e.async_write_ha_state.assert_called_once()


def test_handle_event_ignores_other_thing() -> None:
    e = _make_entity()
    e._handle_bus_event(SimpleNamespace(data={"thing_name": "other", "area_m2": 1}))
    e.async_write_ha_state.assert_not_called()
