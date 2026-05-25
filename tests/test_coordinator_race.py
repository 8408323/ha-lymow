"""Concurrency / race tests for LymowCoordinator state-machine side effects."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

# Importing test_coordinator triggers _make_ha_stubs() at import time, so
# `from lymow.coordinator import LymowCoordinator` works after this.
from tests.test_coordinator import THING, _make_coordinator

# ---------------------------------------------------------------------------
# MQTT push arriving while _async_update_data is mid-await
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mqtt_push_during_rest_poll_lands_in_final_merged_state() -> None:
    """MQTT push mid-poll must be visible in merged result (snapshot is post-await)."""
    coord, mqtt, api = _make_coordinator()

    push_during_poll = asyncio.Event()
    push_done = asyncio.Event()

    async def _slow_get_device_info(thing_name: str) -> dict[str, Any]:
        # Signal the test that we're mid-poll, then wait for the MQTT push to land.
        push_during_poll.set()
        await push_done.wait()
        return {"workStatus": 5, "battery": 100}

    api.get_device_info = AsyncMock(side_effect=_slow_get_device_info)

    async def _push_when_ready() -> None:
        await push_during_poll.wait()
        # The mqtt callback runs synchronously from aiomqtt's loop in real life;
        # we simulate that with a direct call.
        coord.on_mqtt_state(THING, {"battery": 42, "wifiSignalQuality": 88})
        push_done.set()

    pusher = asyncio.create_task(_push_when_ready())
    result = await coord._async_update_data()
    await pusher

    # MQTT values win over REST (the coordinator does **rest first then mqtt last,
    # so mqtt overlays rest).
    assert result[THING]["battery"] == 42
    # REST-only fields survive.
    assert result[THING]["workStatus"] == 5
    # MQTT-only fields propagate.
    assert result[THING]["wifiSignalQuality"] == 88


@pytest.mark.asyncio
async def test_two_mqtt_pushes_in_sequence_accumulate_in_mqtt_state() -> None:
    """Successive patches union in _mqtt_state — neither overwrites the other's keys."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "battery": 100}}

    coord.on_mqtt_state(THING, {"battery": 90, "wifiSignalQuality": 70})
    coord.on_mqtt_state(THING, {"isCharging": True, "lteSignalQuality": 50})

    snap = coord._mqtt_state[THING]
    assert snap["battery"] == 90
    assert snap["wifiSignalQuality"] == 70
    assert snap["isCharging"] is True
    assert snap["lteSignalQuality"] == 50


@pytest.mark.asyncio
async def test_mqtt_push_does_not_blow_away_unmentioned_keys() -> None:
    """Partial push must not erase unmentioned keys (would flash sensors to unknown)."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "battery": 100, "wifiSignalQuality": 70}}

    coord.on_mqtt_state(THING, {"battery": 42})

    assert coord.data[THING]["battery"] == 42  # updated
    assert coord.data[THING]["workStatus"] == 5  # preserved
    assert coord.data[THING]["wifiSignalQuality"] == 70  # preserved


# ---------------------------------------------------------------------------
# robotConfig deep-merge race — partial-replies pattern from reference_robotconfig_wire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_robotconfig_partial_reply_does_not_wipe_sibling_fields() -> None:
    """robotConfig deep-merge: toggling one field must preserve sibling fields."""
    coord, _, _ = _make_coordinator()
    coord.data = {
        THING: {
            "robotConfig": {
                "audioVolume": 80,
                "isOpenLed": True,
                "metric_4g": False,
                "rrConfig": {"enableRr": True, "rechargeBat": 20, "resumeBat": 80},
            }
        }
    }
    coord._mqtt_state[THING] = dict(coord.data[THING])

    # Partial push: only metric_4g changes.
    coord.on_mqtt_state(THING, {"robotConfig": {"metric_4g": True}})

    rc = coord.data[THING]["robotConfig"]
    assert rc["metric_4g"] is True  # updated
    assert rc["audioVolume"] == 80  # preserved
    assert rc["isOpenLed"] is True  # preserved
    # rrConfig nested dict survives intact.
    assert rc["rrConfig"] == {"enableRr": True, "rechargeBat": 20, "resumeBat": 80}


@pytest.mark.asyncio
async def test_robotconfig_partial_rrconfig_does_not_wipe_other_rr_keys() -> None:
    """Two-level deep merge: flipping rrConfig.enableRr preserves nested siblings."""
    coord, _, _ = _make_coordinator()
    coord.data = {
        THING: {
            "robotConfig": {
                "rrConfig": {
                    "enableRr": False,
                    "rechargeBat": 20,
                    "resumeBat": 80,
                    "resumePeriodStart": {"hour": 9, "minute": 0},
                }
            }
        }
    }
    coord._mqtt_state[THING] = {
        "robotConfig": {
            "rrConfig": {
                "enableRr": False,
                "rechargeBat": 20,
                "resumeBat": 80,
                "resumePeriodStart": {"hour": 9, "minute": 0},
            }
        }
    }

    coord.on_mqtt_state(THING, {"robotConfig": {"rrConfig": {"enableRr": True}}})

    rr = coord.data[THING]["robotConfig"]["rrConfig"]
    assert rr["enableRr"] is True
    assert rr["rechargeBat"] == 20
    assert rr["resumeBat"] == 80
    assert rr["resumePeriodStart"] == {"hour": 9, "minute": 0}


# ---------------------------------------------------------------------------
# Concurrent commands — two sync_map calls in flight
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_concurrent_async_sync_map_calls_both_publish() -> None:
    """Concurrent map edits both reach broker — contract is "both go out", no serialising lock."""
    coord, mqtt, _ = _make_coordinator()

    published_in_order: list[bytes] = []
    publish_finished = asyncio.Event()

    async def _slow_publish(thing_name: str, payload: bytes) -> None:
        published_in_order.append(payload)
        # First publish blocks; second can pass it.
        if len(published_in_order) == 1:
            await publish_finished.wait()

    mqtt.async_publish_command = AsyncMock(side_effect=_slow_publish)

    map_a = {"goZones": [{"hashId": "AAAAAAAA", "polygon": []}], "nogoZones": []}
    map_b = {"goZones": [{"hashId": "BBBBBBBB", "polygon": []}], "nogoZones": []}

    task_a = asyncio.create_task(coord.async_sync_map(THING, map_a))
    # Yield so task_a starts and parks inside the slow publish.
    await asyncio.sleep(0)
    assert not task_a.done()

    # Second call must enter publish WHILE task_a is still parked. A regression
    # that serialised commands (e.g. wrapped publish in an asyncio.Lock) would
    # still pass the "both got through eventually" assertion once we release
    # the first one — so the critical proof is: both payloads in the list AND
    # task_a still blocked, simultaneously.
    task_b = asyncio.create_task(coord.async_sync_map(THING, map_b))
    await asyncio.sleep(0)

    # Concurrency proof, taken BEFORE we release the first publish: task_b
    # reached publish (its payload landed in the list) without task_a having
    # returned. A serialising implementation would have task_b parked outside
    # `_slow_publish` here, so the list would still hold only one entry.
    assert len(published_in_order) == 2, (
        "second async_sync_map call did NOT reach publish while the first was parked "
        f"— got {len(published_in_order)} entries, expected 2. Likely a regression "
        "that serialises commands (e.g. added a Lock around publish)."
    )
    assert not task_a.done(), "task_a should still be parked on publish_finished"

    publish_finished.set()
    await asyncio.gather(task_a, task_b)

    # Order is deterministic given how we awaited.
    assert published_in_order[0] != published_in_order[1]


# ---------------------------------------------------------------------------
# Malformed MQTT push doesn't crash on_mqtt_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_mqtt_state_with_empty_patch_is_a_noop() -> None:
    """Empty patch must not crash and must leave merged state unchanged for the thing."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5, "battery": 100}}
    pushed: list = []
    real_set = coord.async_set_updated_data

    def _capture(data):
        pushed.append(data)
        real_set(data)

    coord.async_set_updated_data = _capture  # type: ignore[method-assign]

    coord.on_mqtt_state(THING, {})

    # The merge happens regardless (we can't tell {} from "no useful keys"),
    # but the resulting state for THING must equal the previous one.
    assert pushed and pushed[-1][THING]["workStatus"] == 5
    assert pushed[-1][THING]["battery"] == 100


@pytest.mark.asyncio
async def test_on_mqtt_state_for_unknown_thing_does_not_push_to_ha() -> None:
    """Push for unknown thing updates _mqtt_state but skips async_set_updated_data."""
    coord, _, _ = _make_coordinator()
    coord.data = {THING: {"workStatus": 5}}
    pushed: list = []
    coord.async_set_updated_data = lambda data: pushed.append(data)  # type: ignore[method-assign]

    coord.on_mqtt_state("unknown-thing", {"battery": 42})

    assert pushed == []  # no HA update for an unknown device
    # But the state IS captured so a later device-list refresh can pick it up.
    assert coord._mqtt_state["unknown-thing"] == {"battery": 42}
