"""Transition-matrix tests for LymowCoordinator's user-visible side effects.

Two state-transition state machines run in :py:meth:`on_mqtt_state`:

  1. ``_check_work_status_transition`` — fires HA event-bus events for every
     workStatus change and persistent notifications for **only** the
     mow-finished and entered-error transitions.
  2. ``_check_rtk_guard`` — auto-pauses the mower when GPS quality falls
     below a user-configured threshold, and auto-resumes once it recovers
     (but only if *we* paused it — never resumes a user-initiated pause).

Existing tests prove the happy path fires. These pin the **negative cases**
(when notifications must *not* fire, when resume must *not* trigger) — those
are the regressions a careless refactor causes that a happy-path test misses.

Reuses ``_make_coordinator`` and ``THING`` from test_coordinator.py to avoid
re-duplicating the HA-stub bootstrap.
"""

from __future__ import annotations

import pytest

from tests.test_coordinator import THING, _make_coordinator  # noqa: F401

# ---------------------------------------------------------------------------
# _check_work_status_transition — event bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_without_work_status_fires_no_event_or_notification() -> None:
    """A pboutput that decoded only e.g. battery — no workStatus key — must
    short-circuit before touching the event bus or persistent notifications."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}
    coord._prev_work_status[THING] = 5

    coord.on_mqtt_state(THING, {"battery": 42})

    coord.hass.bus.async_fire.assert_not_called()
    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_event_fires_for_no_op_transitions_too() -> None:
    """The event bus event must fire on every workStatus value seen, even
    same → same — automations may depend on heartbeat semantics."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2}}
    coord._prev_work_status[THING] = 2

    coord.on_mqtt_state(THING, {"workStatus": 2})

    coord.hass.bus.async_fire.assert_called_once()
    args = coord.hass.bus.async_fire.call_args[0]
    assert args[0] == "lymow_work_status_changed"
    assert args[1]["work_status"] == 2
    assert args[1]["prev_work_status"] == 2


# ---------------------------------------------------------------------------
# Error notification — fires only on entry, not on stay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consecutive_error_states_do_not_re_notify() -> None:
    """Two pboutputs both carrying WORK_STATUS_ERROR must produce ONE
    persistent notification (on the entry transition), not two. Otherwise the
    user sees a notification spam every 30 s while the robot is in error."""
    from lymow.const import WORK_STATUS_ERROR

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 2}}
    coord._prev_work_status[THING] = 2  # MOWING — outside ERROR_GROUP

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_ERROR})
    assert coord.hass.components.persistent_notification.async_create.call_count == 1

    # Second push, still ERROR — must NOT add a second notification.
    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_ERROR})
    assert coord.hass.components.persistent_notification.async_create.call_count == 1


@pytest.mark.asyncio
async def test_emergency_stop_after_error_does_not_re_notify() -> None:
    """Both WORK_STATUS_ERROR and WORK_STATUS_EMERGENCY_STOP live in
    ERROR_GROUP — switching between them is a *stay*, not an entry. The
    user already knows there's a problem; don't double-notify."""
    from lymow.const import WORK_STATUS_EMERGENCY_STOP, WORK_STATUS_ERROR

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_ERROR

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_EMERGENCY_STOP})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_first_observation_in_error_fires_notification() -> None:
    """First MQTT push for a device defaults prev_work_status to -1 (OFFLINE),
    which is NOT in ERROR_GROUP. So a freshly-online robot reporting ERROR
    must fire the entry notification — the user wasn't previously aware."""
    from lymow.const import WORK_STATUS_ERROR

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {}}
    # No _prev_work_status entry — i.e. we've never seen this device before.
    assert THING not in coord._prev_work_status

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_ERROR})
    coord.hass.components.persistent_notification.async_create.assert_called_once()
    kwargs = coord.hass.components.persistent_notification.async_create.call_args[1]
    assert "error" in kwargs.get("title", "").lower()


# ---------------------------------------------------------------------------
# Done notification — narrowly conditioned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_charging_to_charging_full_does_not_fire_done() -> None:
    """Both states live in DOCKED_GROUP. Going CHARGING → CHARGING_FULL is
    a routine docked-state churn, not a mow-finished event."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_CHARGING_FULL

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_CHARGING

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING_FULL})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_error_to_docked_does_not_fire_done() -> None:
    """Errored robot gets manually docked — that's a recovery, not a mow
    completion. ``prev_ws in MOWING|RETURNING`` must reject ERROR."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_ERROR

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_ERROR

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_paused_to_docked_does_not_fire_done() -> None:
    """User paused mid-mow, then docked the robot manually. Not a completion."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_PAUSE

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_PAUSE

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_returning_to_docked_fires_done() -> None:
    """The other half of the contract: mower in RETURNING_GROUP (e.g.
    PAUSE_DOCKING or ESCAPING) reaching DOCKED is the mow-done signal."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_DOCKING

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_DOCKING

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})

    coord.hass.components.persistent_notification.async_create.assert_called_once()
    kwargs = coord.hass.components.persistent_notification.async_create.call_args[1]
    assert "done" in kwargs.get("title", "").lower() or "finished" in kwargs.get("message", "").lower()


@pytest.mark.asyncio
async def test_notification_ids_differ_between_error_and_done() -> None:
    """The two notification kinds must use distinct notification_ids — same id
    would let one dismiss the other (HA dedupes by id)."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_DOCKING, WORK_STATUS_ERROR

    # Error path
    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = 2  # MOWING
    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_ERROR})
    error_id = coord.hass.components.persistent_notification.async_create.call_args[1]["notification_id"]

    # Done path (fresh coordinator)
    coord2, _, _ = _make_coordinator()
    coord2._prev_work_status[THING] = WORK_STATUS_DOCKING
    coord2.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})
    done_id = coord2.hass.components.persistent_notification.async_create.call_args[1]["notification_id"]

    assert error_id != done_id


# ---------------------------------------------------------------------------
# Device label fallback chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_uses_device_name_when_present() -> None:
    coord, _, _ = _make_coordinator(devices=[{"deviceThingName": THING, "deviceName": "Backyard Mower"}])
    coord._prev_work_status[THING] = 2
    coord.on_mqtt_state(THING, {"workStatus": 2})
    payload = coord.hass.bus.async_fire.call_args[0][1]
    assert payload["device_name"] == "Backyard Mower"


@pytest.mark.asyncio
async def test_event_falls_back_to_sn_when_device_name_missing() -> None:
    coord, _, _ = _make_coordinator(devices=[{"deviceThingName": THING, "sn": "SN-XYZ"}])
    coord._prev_work_status[THING] = 2
    coord.on_mqtt_state(THING, {"workStatus": 2})
    payload = coord.hass.bus.async_fire.call_args[0][1]
    assert payload["device_name"] == "SN-XYZ"


@pytest.mark.asyncio
async def test_event_falls_back_to_thing_name_when_all_labels_missing() -> None:
    coord, _, _ = _make_coordinator(devices=[{"deviceThingName": THING}])
    coord._prev_work_status[THING] = 2
    coord.on_mqtt_state(THING, {"workStatus": 2})
    payload = coord.hass.bus.async_fire.call_args[0][1]
    assert payload["device_name"] == THING


# ---------------------------------------------------------------------------
# RTK guard transition matrix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rtk_guard_is_a_noop_when_disabled() -> None:
    """Default state: guard disabled. RTK drop must NOT pause the robot."""
    from lymow.const import WORK_STATUS_MOWING

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": WORK_STATUS_MOWING}}
    assert coord.is_rtk_guard_enabled(THING) is False  # default off

    coord.on_mqtt_state(THING, {"rtkStatus": 0})  # below default threshold 1

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_ignores_patches_without_rtk_status() -> None:
    """A patch carrying only e.g. battery must not trigger the guard — the
    last-known rtk could be stale and trigger a spurious pause."""
    from lymow.const import WORK_STATUS_MOWING

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": WORK_STATUS_MOWING, "rtkStatus": 5}}

    coord.on_mqtt_state(THING, {"battery": 80})  # no rtkStatus in patch

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_pauses_when_low_signal_while_mowing() -> None:
    from lymow.const import WORK_STATUS_MOWING

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.set_rtk_guard_threshold(THING, 2)
    coord.data = {THING: {"workStatus": WORK_STATUS_MOWING}}

    coord.on_mqtt_state(THING, {"rtkStatus": 1})  # below threshold 2

    coord.hass.async_create_task.assert_called_once()
    # active_pause flag flips only when the pause coroutine awaits — but the
    # coordinator schedules it eagerly via async_create_task. Verify the
    # scheduled coroutine is the pause helper.
    coro = coord.hass.async_create_task.call_args[0][0]
    assert "_async_rtk_guard_pause" in repr(coro)
    coro.close()  # avoid "coroutine was never awaited"


@pytest.mark.asyncio
async def test_rtk_guard_does_not_pause_when_robot_already_docked() -> None:
    """Low RTK while docked is irrelevant — the robot isn't moving. Pausing
    here would just confuse the next mow-start."""
    from lymow.const import WORK_STATUS_CHARGING

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": WORK_STATUS_CHARGING}}

    coord.on_mqtt_state(THING, {"rtkStatus": 0})

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_does_not_resume_user_initiated_pause() -> None:
    """If the user paused (not the guard), active_pause is False — even if
    RTK recovers, the guard must NOT auto-resume. This is the safety
    property keeping a user's deliberate pause inviolate."""
    from lymow.const import WORK_STATUS_PAUSE

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.set_rtk_guard_threshold(THING, 2)
    coord.data = {THING: {"workStatus": WORK_STATUS_PAUSE}}
    coord._rtk_guard_active_pause[THING] = False  # user paused, not us

    coord.on_mqtt_state(THING, {"rtkStatus": 5})  # well above threshold

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_resumes_only_when_we_were_the_pauser() -> None:
    """The flip side: we DID pause, RTK recovers, robot still in PAUSED_GROUP
    → schedule resume."""
    from lymow.const import WORK_STATUS_PAUSE

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.set_rtk_guard_threshold(THING, 2)
    coord.data = {THING: {"workStatus": WORK_STATUS_PAUSE}}
    coord._rtk_guard_active_pause[THING] = True

    coord.on_mqtt_state(THING, {"rtkStatus": 5})

    coord.hass.async_create_task.assert_called_once()
    coro = coord.hass.async_create_task.call_args[0][0]
    assert "_async_rtk_guard_resume" in repr(coro)
    coro.close()


@pytest.mark.asyncio
async def test_rtk_guard_handles_non_int_rtk_value_as_noop() -> None:
    """A malformed rtkStatus (e.g. broker glitch sending a string) must not
    raise — the guard silently no-ops so the rest of on_mqtt_state still runs."""
    from lymow.const import WORK_STATUS_MOWING

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": WORK_STATUS_MOWING}}

    # Patch is otherwise legal — workStatus also moves so we know on_mqtt_state ran.
    coord.on_mqtt_state(THING, {"rtkStatus": "garbage", "workStatus": WORK_STATUS_MOWING})

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_disabling_guard_clears_active_pause_flag() -> None:
    """When the user toggles the switch off, the active_pause flag must
    reset — otherwise a later natural pause/resume could be mis-attributed,
    and re-enabling the guard would carry stale state."""
    coord, _, _ = _make_coordinator()
    coord._rtk_guard_active_pause[THING] = True
    coord.set_rtk_guard_enabled(THING, True)
    assert coord._rtk_guard_active_pause[THING] is True  # not cleared by enabling

    coord.set_rtk_guard_enabled(THING, False)

    assert coord._rtk_guard_active_pause[THING] is False
