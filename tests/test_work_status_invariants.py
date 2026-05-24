"""Structural invariants on WORK_STATUS_* constants and the LawnMowerActivity mapping.

Two failure modes these catch that the existing tests don't:

1. **Partition drift.** Each WORK_STATUS_* value should live in *exactly one*
   `_GROUP` (or be intentionally unassigned). A new status added to the enum
   but forgotten in the groups falls silently into ``LawnMowerActivity.ERROR``;
   a status accidentally listed in two groups makes activity-mapping ambiguous
   depending on the order of the `if ws in ...` checks in
   :py:meth:`LymowMower.activity`.

2. **Activity-mapping snapshot.** The mapping from workStatus value → HA
   :class:`LawnMowerActivity` is a load-bearing contract — frontend dashboards
   and automations key off these values. We snapshot every documented
   workStatus → activity so a future refactor of the groups can't silently
   reclassify a state (e.g. a charging robot accidentally showing up as MOWING).
"""

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
    """A status in two groups makes activity() ambiguous — the first
    matching ``if ws in ...`` wins, but that's just an implementation
    detail. Forbid overlap so the mapping is unambiguous."""
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
    """A new WORK_STATUS_* added without a group assignment will silently
    fall to ``LawnMowerActivity.ERROR``. Force the author to either put it
    in a group or add it to ``_INTENTIONALLY_UNASSIGNED`` with a comment."""
    grouped = set().union(*_ALL_GROUPS.values())
    accounted_for = grouped | _INTENTIONALLY_UNASSIGNED
    orphans = {name: val for name, val in _ALL_STATUSES.items() if val not in accounted_for}
    assert not orphans, (
        "WORK_STATUS_* constants neither in a group nor in _INTENTIONALLY_UNASSIGNED — "
        "they will show up as ERROR in HA. Add them to the appropriate group, "
        f"or to _INTENTIONALLY_UNASSIGNED with a why: {orphans}"
    )


def test_all_status_values_are_unique() -> None:
    """Two WORK_STATUS_* names with the same int would silently shadow each
    other through the entire codebase (group membership tests, switch/if
    chains, coordinator dispatch)."""
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
    """The snapshot above must mention every WORK_STATUS_* defined in
    const.py — so adding a new status forces the author to declare what HA
    activity it should map to. Without this guard a new constant could pass
    every other test (no group overlap, value unique) but slip through
    unnoticed by frontends."""
    missing = set(_ALL_STATUSES) - set(_EXPECTED_ACTIVITY)
    extra = set(_EXPECTED_ACTIVITY) - set(_ALL_STATUSES)
    assert not missing, f"_EXPECTED_ACTIVITY is missing entries for: {sorted(missing)}"
    assert not extra, f"_EXPECTED_ACTIVITY mentions unknown statuses: {sorted(extra)}"


@pytest.mark.parametrize("status_name,expected_activity", sorted(_EXPECTED_ACTIVITY.items()))
def test_activity_mapping_snapshot(status_name: str, expected_activity: str) -> None:
    """Pin the workStatus → LawnMowerActivity mapping under the real
    `LymowMower.activity` property. Any change to the groups in const.py that
    moves a status across activity boundaries (e.g. CHARGING → MOWING) will
    surface as a failed parametrised case naming the exact status that moved."""
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
    """``isOnline=False`` must clamp to ERROR before the workStatus mapping
    runs — even if MQTT delivered a fresh MOWING state, the device being
    offline should override it. Documents the precedence of the two checks."""
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
    """No workStatus key at all (e.g. coordinator has only REST data, no MQTT
    yet) defaults to WORK_STATUS_OFFLINE → ERROR. Locks in the safer of two
    plausible defaults: "ERROR until we know" beats "DOCKED until we know"."""
    from unittest.mock import MagicMock

    from lymow.lawn_mower import LymowMower

    coord = MagicMock()
    coord.data = {"thing-x": {"isOnline": True}}  # no workStatus key
    coord.devices = [{"deviceThingName": "thing-x", "deviceName": "Mower"}]
    entity = LymowMower(coord, {"deviceThingName": "thing-x", "deviceName": "Mower"})
    assert entity.activity.name == "ERROR"
