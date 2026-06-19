"""Transition-matrix tests for LymowCoordinator user-visible side effects."""

from __future__ import annotations

import pytest

from tests.test_coordinator import THING, _make_coordinator  # noqa: F401

# ---------------------------------------------------------------------------
# _check_work_status_transition — event bus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_without_work_status_fires_no_event_or_notification() -> None:
    """Patch without workStatus key must short-circuit before bus / notifications."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}
    coord._prev_work_status[THING] = 5

    coord.on_mqtt_state(THING, {"battery": 42})

    coord.hass.bus.async_fire.assert_not_called()
    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_event_fires_for_no_op_transitions_too() -> None:
    """Event must fire even on same→same workStatus (heartbeat for automations)."""
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
    """Two ERROR states fire only one notification (entry, not stay) to avoid spam."""
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
    """ERROR → EMERGENCY_STOP is within ERROR_GROUP (a stay) — no re-notify."""
    from lymow.const import WORK_STATUS_EMERGENCY_STOP, WORK_STATUS_ERROR

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_ERROR

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_EMERGENCY_STOP})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_first_observation_in_error_fires_notification() -> None:
    """First-ever push reporting ERROR must notify — prev defaults to OFFLINE (≠ERROR)."""
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
    """CHARGING → CHARGING_FULL is intra-DOCKED churn, not a mow-finished event."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_CHARGING_FULL

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_CHARGING

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING_FULL})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_error_to_docked_does_not_fire_done() -> None:
    """ERROR → DOCKED is recovery, not completion — prev must not match MOWING/RETURNING."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_ERROR

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_ERROR

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_paused_to_docked_does_not_fire_done() -> None:
    """User paused mid-mow then docked manually is not a mow completion."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_PAUSE

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_PAUSE

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})

    coord.hass.components.persistent_notification.async_create.assert_not_called()


@pytest.mark.asyncio
async def test_returning_to_docked_fires_done() -> None:
    """RETURNING_GROUP → DOCKED is the mow-done signal."""
    from lymow.const import WORK_STATUS_CHARGING, WORK_STATUS_DOCKING

    coord, _, _ = _make_coordinator()
    coord._prev_work_status[THING] = WORK_STATUS_DOCKING

    coord.on_mqtt_state(THING, {"workStatus": WORK_STATUS_CHARGING})

    coord.hass.components.persistent_notification.async_create.assert_called_once()
    kwargs = coord.hass.components.persistent_notification.async_create.call_args[1]
    assert "done" in kwargs.get("title", "").lower() or "finished" in kwargs.get("message", "").lower()


@pytest.mark.asyncio
async def test_notification_ids_differ_between_error_and_done() -> None:
    """Error and done must use distinct notification_ids — HA dedupes by id."""
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
    """Default guard disabled — RTK drop must not pause the robot."""
    from lymow.const import WORK_STATUS_MOWING

    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": WORK_STATUS_MOWING}}
    assert coord.is_rtk_guard_enabled(THING) is False  # default off

    coord.on_mqtt_state(THING, {"rtkStatus": 0})  # below default threshold 1

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_ignores_patches_without_rtk_status() -> None:
    """Patches lacking rtkStatus must not trigger guard — stale value would spuriously pause."""
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
    """Low RTK while docked is irrelevant — guard must not pause a stationary robot."""
    from lymow.const import WORK_STATUS_CHARGING

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": WORK_STATUS_CHARGING}}

    coord.on_mqtt_state(THING, {"rtkStatus": 0})

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_rtk_guard_does_not_resume_user_initiated_pause() -> None:
    """Guard never resumes a user-initiated pause (active_pause=False is sacred)."""
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
    """Guard resumes when active_pause=True, RTK recovers, robot still in PAUSED_GROUP."""
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
    """Malformed rtkStatus must not raise — guard no-ops so rest of on_mqtt_state runs."""
    from lymow.const import WORK_STATUS_MOWING

    coord, _, _ = _make_coordinator()
    coord.set_rtk_guard_enabled(THING, True)
    coord.data = {THING: {"workStatus": WORK_STATUS_MOWING}}
    # Suppress the unrelated mow-path auto-poll (also async_create_task) so this
    # asserts only that the RTK guard itself does not act on a garbage value.
    coord._path_poll_pending[THING] = True

    # Patch is otherwise legal — workStatus also moves so we know on_mqtt_state ran.
    coord.on_mqtt_state(THING, {"rtkStatus": "garbage", "workStatus": WORK_STATUS_MOWING})

    coord.hass.async_create_task.assert_not_called()


@pytest.mark.asyncio
async def test_disabling_guard_clears_active_pause_flag() -> None:
    """Disabling guard must clear active_pause to prevent stale state on re-enable."""
    coord, _, _ = _make_coordinator()
    coord._rtk_guard_active_pause[THING] = True
    coord.set_rtk_guard_enabled(THING, True)
    assert coord._rtk_guard_active_pause[THING] is True  # not cleared by enabling

    coord.set_rtk_guard_enabled(THING, False)

    assert coord._rtk_guard_active_pause[THING] is False


# ---------------------------------------------------------------------------
# on_mqtt_online — offline-notification transition guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_offline_observation_fires_notification() -> None:
    """First offline message ever — prev is None, must fire."""
    coord, _, _ = _make_coordinator()
    coord.on_mqtt_online(THING, False)
    coord.hass.components.persistent_notification.async_create.assert_called_once()


@pytest.mark.asyncio
async def test_online_to_offline_transition_fires_notification() -> None:
    """User saw online, robot went offline — should notify."""
    coord, _, _ = _make_coordinator()
    coord.on_mqtt_online(THING, True)  # seed online
    coord.on_mqtt_online(THING, False)
    coord.hass.components.persistent_notification.async_create.assert_called_once()


@pytest.mark.asyncio
async def test_consecutive_offline_messages_do_not_re_notify() -> None:
    """Broker re-asserting offline must not re-create a dismissed notification.

    Without the prev_online guard, a user dismissing the "X has gone offline"
    notification would see it pop right back on the next periodic offline
    push. The transition guard mirrors the work-status notification pattern
    (one notification per entry into the state, not per assertion of it).
    """
    coord, _, _ = _make_coordinator()
    coord.on_mqtt_online(THING, False)
    assert coord.hass.components.persistent_notification.async_create.call_count == 1
    coord.on_mqtt_online(THING, False)
    coord.on_mqtt_online(THING, False)
    assert coord.hass.components.persistent_notification.async_create.call_count == 1


@pytest.mark.asyncio
async def test_offline_after_recovery_fires_notification_again() -> None:
    """User saw offline → robot came back online → went offline again. Each
    fresh entry into offline-state notifies — only intra-state assertions
    are deduped."""
    coord, _, _ = _make_coordinator()
    coord.on_mqtt_online(THING, False)
    coord.on_mqtt_online(THING, True)  # recovery
    coord.on_mqtt_online(THING, False)  # offline again
    assert coord.hass.components.persistent_notification.async_create.call_count == 2
