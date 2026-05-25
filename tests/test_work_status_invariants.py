"""Structural invariants on WORK_STATUS_* constants and the LawnMowerActivity mapping."""

from __future__ import annotations

import sys

import pytest
from lymow.const import (
    WORK_STATUS_DOCKED_GROUP,
    WORK_STATUS_ERROR_GROUP,
    WORK_STATUS_MOWING_GROUP,
    WORK_STATUS_OFFLINE,
    WORK_STATUS_PAUSED_GROUP,
    WORK_STATUS_RETURNING_GROUP,
)

# The conftest preloads ``lymow.const`` via importlib without setting up a
# parent ``lymow`` package, so ``from lymow import const`` would fail. Grab
# the loaded module directly out of sys.modules and use it to introspect.
const = sys.modules["lymow.const"]

# All defined WORK_STATUS_* numeric values, by name. Built dynamically so
# *adding* a new constant in const.py automatically pulls it into the
# invariant tests below — there's no name list to forget to update.
_ALL_STATUSES: dict[str, int] = {
    name: getattr(const, name)
    for name in dir(const)
    if name.startswith("WORK_STATUS_") and not name.endswith("_GROUP") and isinstance(getattr(const, name), int)
}

_ALL_GROUPS: dict[str, frozenset[int]] = {
    "MOWING": WORK_STATUS_MOWING_GROUP,
    "RETURNING": WORK_STATUS_RETURNING_GROUP,
    "DOCKED": WORK_STATUS_DOCKED_GROUP,
    "PAUSED": WORK_STATUS_PAUSED_GROUP,
    "ERROR": WORK_STATUS_ERROR_GROUP,
}

# Statuses intentionally left out of all groups — they fall through to the
# default ``LawnMowerActivity.ERROR`` in :py:meth:`LymowMower.activity`. If you
# add a new value to this set you're declaring "user will see this as ERROR
# in HA". For OFFLINE that's the documented behavior (no MQTT shadow). For RTT
# / AGING_TEST these are factory states a customer should never see.
_INTENTIONALLY_UNASSIGNED: set[int] = {
    WORK_STATUS_OFFLINE,  # -1 — coordinator hasn't received MQTT state yet
    const.WORK_STATUS_RTT,  # 15 — factory radio test
    const.WORK_STATUS_AGING_TEST,  # 16 — factory burn-in
}


# ---------------------------------------------------------------------------
# Partition invariants
# ---------------------------------------------------------------------------


def test_no_status_is_in_more_than_one_group() -> None:
    """Overlap makes activity() ambiguous (depends on if-chain order) — forbid it."""
    seen: dict[int, str] = {}
    overlaps: list[tuple[int, str, str]] = []
    for group_name, members in _ALL_GROUPS.items():
        for ws in members:
            if ws in seen:
                overlaps.append((ws, seen[ws], group_name))
            else:
                seen[ws] = group_name
    assert not overlaps, (
        f"workStatus values found in multiple groups (activity() result depends on iteration order): {overlaps}"
    )


def test_every_status_is_grouped_or_intentionally_unassigned() -> None:
    """Unassigned WORK_STATUS_* falls silently to ERROR — force an explicit choice."""
    grouped = set().union(*_ALL_GROUPS.values())
    overlap = grouped & _INTENTIONALLY_UNASSIGNED
    assert not overlap, f"WORK_STATUS_* values cannot be both grouped and intentionally unassigned: {sorted(overlap)}"
    accounted_for = grouped | _INTENTIONALLY_UNASSIGNED
    orphans = {name: val for name, val in _ALL_STATUSES.items() if val not in accounted_for}
    assert not orphans, (
        "WORK_STATUS_* constants neither in a group nor in _INTENTIONALLY_UNASSIGNED — "
        "they will show up as ERROR in HA. Add them to the appropriate group, "
        f"or to _INTENTIONALLY_UNASSIGNED with a why: {orphans}"
    )


def test_all_status_values_are_unique() -> None:
    """Duplicate int values would silently shadow each other across the codebase."""
    by_value: dict[int, list[str]] = {}
    for name, val in _ALL_STATUSES.items():
        by_value.setdefault(val, []).append(name)
    dupes = {val: names for val, names in by_value.items() if len(names) > 1}
    assert not dupes, f"workStatus int values are not unique: {dupes}"


# ---------------------------------------------------------------------------
# Activity-mapping snapshot
# ---------------------------------------------------------------------------
#
# Frozen mapping from workStatus name → expected HA LawnMowerActivity name.
# Updating this is a deliberate act — any commit that changes a value here is
# documenting a behavioural change visible to every HA user of this integration.

_EXPECTED_ACTIVITY: dict[str, str] = {
    # MOWING_GROUP
    "WORK_STATUS_MOWING": "MOWING",
    "WORK_STATUS_RESUME": "MOWING",
    "WORK_STATUS_ZONE_PARTITION": "MOWING",
    # RETURNING_GROUP
    "WORK_STATUS_DOCKING": "RETURNING",
    "WORK_STATUS_PAUSE_DOCKING": "RETURNING",
    "WORK_STATUS_ESCAPING": "RETURNING",
    # DOCKED_GROUP
    "WORK_STATUS_NONE": "DOCKED",
    "WORK_STATUS_WAITING": "DOCKED",
    "WORK_STATUS_CHARGING": "DOCKED",
    "WORK_STATUS_CHARGING_FULL": "DOCKED",
    "WORK_STATUS_UPDATING": "DOCKED",
    # PAUSED_GROUP
    "WORK_STATUS_PAUSE": "PAUSED",
    "WORK_STATUS_REMOTE_CONTROL": "PAUSED",
    # ERROR_GROUP (real errors)
    "WORK_STATUS_ERROR": "ERROR",
    "WORK_STATUS_EMERGENCY_STOP": "ERROR",
    # Unassigned — fall through to ERROR by design (see _INTENTIONALLY_UNASSIGNED).
    "WORK_STATUS_OFFLINE": "ERROR",
    "WORK_STATUS_RTT": "ERROR",
    "WORK_STATUS_AGING_TEST": "ERROR",
}


def test_activity_mapping_covers_every_defined_status() -> None:
    """Snapshot must mention every WORK_STATUS_* so new ones can't slip through."""
    missing = set(_ALL_STATUSES) - set(_EXPECTED_ACTIVITY)
    extra = set(_EXPECTED_ACTIVITY) - set(_ALL_STATUSES)
    assert not missing, f"_EXPECTED_ACTIVITY is missing entries for: {sorted(missing)}"
    assert not extra, f"_EXPECTED_ACTIVITY mentions unknown statuses: {sorted(extra)}"


@pytest.mark.parametrize("status_name,expected_activity", sorted(_EXPECTED_ACTIVITY.items()))
def test_activity_mapping_snapshot(status_name: str, expected_activity: str) -> None:
    """Pin workStatus → LawnMowerActivity so cross-group moves fail with named case."""
    from unittest.mock import MagicMock

    from lymow.lawn_mower import LymowMower

    ws = _ALL_STATUSES[status_name]
    coord = MagicMock()
    coord.data = {"thing-x": {"workStatus": ws, "isOnline": True}}
    coord.devices = [{"deviceThingName": "thing-x", "deviceName": "Mower"}]

    entity = LymowMower(coord, {"deviceThingName": "thing-x", "deviceName": "Mower"})
    assert entity.activity.name == expected_activity, (
        f"{status_name} (value {ws}) mapped to {entity.activity.name}, expected {expected_activity}"
    )


def test_offline_short_circuits_to_error_regardless_of_work_status() -> None:
    """isOnline=False clamps to ERROR before workStatus mapping — locks precedence."""
    from unittest.mock import MagicMock

    from lymow.lawn_mower import LymowMower

    coord = MagicMock()
    coord.data = {
        "thing-x": {
            "workStatus": const.WORK_STATUS_MOWING,  # would say MOWING on its own
            "isOnline": False,
        }
    }
    coord.devices = [{"deviceThingName": "thing-x", "deviceName": "Mower"}]
    entity = LymowMower(coord, {"deviceThingName": "thing-x", "deviceName": "Mower"})
    assert entity.activity.name == "ERROR"


def test_missing_work_status_falls_to_offline_default_then_error() -> None:
    """Missing workStatus key defaults to OFFLINE → ERROR (safer than DOCKED)."""
    from unittest.mock import MagicMock

    from lymow.lawn_mower import LymowMower

    coord = MagicMock()
    coord.data = {"thing-x": {"isOnline": True}}  # no workStatus key
    coord.devices = [{"deviceThingName": "thing-x", "deviceName": "Mower"}]
    entity = LymowMower(coord, {"deviceThingName": "thing-x", "deviceName": "Mower"})
    assert entity.activity.name == "ERROR"
