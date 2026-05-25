"""Parity between services.yaml and the services registered by async_setup_entry."""

from __future__ import annotations

import os
import re
from collections import Counter
from unittest.mock import MagicMock

import pytest

_SERVICES_YAML = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow", "services.yaml")

# A top-level service name in services.yaml is a line that begins at column 0,
# is composed of snake_case identifier characters, and ends with ":". Nested
# keys are always indented, so this won't match them.
_TOP_LEVEL_KEY_RE = re.compile(r"^([a-z][a-z0-9_]*):\s*$", re.MULTILINE)


def _yaml_service_names_in_order() -> list[str]:
    with open(_SERVICES_YAML, encoding="utf-8") as f:
        return _TOP_LEVEL_KEY_RE.findall(f.read())


def _yaml_service_names() -> set[str]:
    return set(_yaml_service_names_in_order())


async def _registered_service_names() -> set[str]:
    """Drive async_setup_entry with a mock hass and capture registered names."""
    from lymow.const import DOMAIN
    from lymow.lawn_mower import async_setup_entry

    names: set[str] = set()

    def _register(domain, service, handler, schema=None, supports_response=False):
        # Only the lymow domain is interesting; the lawn_mower platform doesn't
        # currently register cross-domain services but guard anyway.
        if domain == DOMAIN:
            names.add(service)

    coord = MagicMock()
    coord.devices = [{"deviceThingName": "thing-1", "deviceName": "Mower"}]

    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-1": coord}}
    hass.services = MagicMock()
    hass.services.async_register.side_effect = _register

    entry = MagicMock()
    entry.entry_id = "entry-1"

    await async_setup_entry(hass, entry, lambda entities: None)
    return names


@pytest.mark.asyncio
async def test_every_yaml_service_is_registered() -> None:
    declared = _yaml_service_names()
    registered = await _registered_service_names()
    missing = declared - registered
    assert not missing, f"services.yaml declares services with no async_register call: {sorted(missing)}"


@pytest.mark.asyncio
async def test_every_registered_service_is_documented() -> None:
    declared = _yaml_service_names()
    registered = await _registered_service_names()
    orphans = registered - declared
    assert not orphans, f"async_setup_entry registers services not documented in services.yaml: {sorted(orphans)}"


def test_yaml_parser_finds_known_services() -> None:
    """Sanity-check the regex parser against a few names we know are in the file."""
    declared = _yaml_service_names()
    for name in ("delete_zone", "ble_drive", "set_schedules", "pin_and_go", "resume"):
        assert name in declared, f"regex parser missed known service {name!r}"


def test_services_yaml_has_no_duplicate_top_level_keys() -> None:
    names = _yaml_service_names_in_order()
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    assert not duplicates, f"services.yaml contains duplicate top-level service keys: {duplicates}"
