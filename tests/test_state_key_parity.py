"""Parity between sensor / binary-sensor consumer keys and their producers."""

from __future__ import annotations

import os
import re

import pytest

_LYMOW_DIR = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")

# Modules that may write into coordinator.data[thing]. A consumer key found
# as a string literal in any of these is "covered".
_PRODUCER_FILES = (
    "coordinator.py",
    "protocol.py",
    "api.py",
    "mqtt.py",
)

# Keys that originate from the cloud REST API responses (get_device_info,
# get_device_feature, etc.) and therefore don't appear as string literals
# anywhere in the Python source. Adding to this list is a deliberate act
# documenting: "yes, this key only exists because the AWS gateway sent it."
_REST_API_KEYS: set[str] = {
    # /device-info response fields
    "softwareVersion",
    "mcuVersion",
    "ipAddress",
    "wifiSsid",
    "rtkSn",
    "wheelVer",
    "knifeVer",
    # /device-feature response fields (theft, stolen status etc.)
    "stolenStatus",
    # /device-list-query static-device fields written by coordinator's
    # _static_device_fields helper — those ARE in coordinator.py but the test
    # below picks them up automatically; keep this comment as a pointer.
}


def _read(name: str) -> str:
    with open(os.path.join(_LYMOW_DIR, name), encoding="utf-8") as f:
        return f.read()


def _producer_haystack() -> str:
    """Concatenate all producer files into one grep target for the parity check."""
    parts: list[str] = []
    for name in _PRODUCER_FILES:
        path = os.path.join(_LYMOW_DIR, name)
        if os.path.exists(path):
            parts.append(_read(name))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sensor value_key parity
# ---------------------------------------------------------------------------


_VALUE_KEY_RE = re.compile(r'value_key="([^"]+)"')


def _sensor_value_keys() -> set[str]:
    return set(_VALUE_KEY_RE.findall(_read("sensor.py")))


def test_sensor_parser_finds_known_keys() -> None:
    """Sanity: regex parser picks up the canonical value_keys."""
    keys = _sensor_value_keys()
    for name in ("battery", "errorCode", "lastCleanAt", "totalCleanTimeMin", "robotState"):
        assert name in keys


@pytest.mark.parametrize("key", sorted(_sensor_value_keys()))
def test_every_sensor_value_key_has_a_producer_or_is_rest_api(key: str) -> None:
    """Every sensor value_key must appear as literal in a producer or be in REST allow-list."""
    if key in _REST_API_KEYS:
        return  # documented exception
    # Nested value_keys (e.g. "networkInfo.cellularIp", "rtkL1.gnssSatellites",
    # "robotConfig.lcdPin") are resolved by walking into a dict the producer
    # writes under the ROOT key (state["networkInfo"] = {...}); the leaf is filled
    # inside that sub-dict, so verify the root segment is produced.
    key = key.split(".", 1)[0]
    if key in _REST_API_KEYS:
        return
    haystack = _producer_haystack()
    # Match the key as a quoted string literal — both single- and double-quoted.
    needle = f'"{key}"'
    needle_alt = f"'{key}'"
    assert needle in haystack or needle_alt in haystack, (
        f"sensor value_key={key!r} is not produced by any module in {_PRODUCER_FILES} "
        f"and is not in _REST_API_KEYS. The sensor will silently show Unknown."
    )


# ---------------------------------------------------------------------------
# Binary-sensor _field parity
# ---------------------------------------------------------------------------


_BS_FIELD_RE = re.compile(r'^\s*_field\s*=\s*"([^"]+)"', re.MULTILINE)


def _binary_sensor_fields() -> set[str]:
    return set(_BS_FIELD_RE.findall(_read("binary_sensor.py")))


def test_binary_sensor_parser_finds_known_fields() -> None:
    fields = _binary_sensor_fields()
    for name in ("isCharging", "isRecharging", "wifiWorking"):
        assert name in fields


@pytest.mark.parametrize("field", sorted(_binary_sensor_fields()))
def test_every_binary_sensor_field_has_a_producer_or_is_rest_api(field: str) -> None:
    """Same parity rule as sensors — every _field must be produced somewhere."""
    if field in _REST_API_KEYS:
        return
    haystack = _producer_haystack()
    needle = f'"{field}"'
    needle_alt = f"'{field}'"
    assert needle in haystack or needle_alt in haystack, (
        f"binary_sensor _field={field!r} is not produced by any module in {_PRODUCER_FILES} "
        f"and is not in _REST_API_KEYS. The binary sensor will silently stay None."
    )


# ---------------------------------------------------------------------------
# Sensor unique_id-suffix uniqueness within the platform
# ---------------------------------------------------------------------------


_KEY_RE = re.compile(r'\bkey="([a-z][a-z0-9_]*)"')


def test_sensor_description_keys_are_unique() -> None:
    """Duplicate description keys would collide unique_ids and silently drop one entity."""
    sensor_src = _read("sensor.py")
    keys = _KEY_RE.findall(sensor_src)
    # Only count keys defined inside SensorEntityDescription(...) — the regex
    # above is naive (matches all key="..."). Filter to only keys that appear
    # ON THE SAME LINE OR JUST AFTER a SensorEntityDescription( opening.
    # For now we accept the naive count; if two descriptions share a key it
    # WILL show up as a dup here.
    seen: dict[str, int] = {}
    for k in keys:
        seen[k] = seen.get(k, 0) + 1
    dupes = {k: count for k, count in seen.items() if count > 1}
    assert not dupes, f"duplicate SensorEntityDescription key values in sensor.py: {dupes}"


# ---------------------------------------------------------------------------
# Binary-sensor _field is read via the same path everywhere
# ---------------------------------------------------------------------------


def test_binary_sensor_fields_are_unique_per_class() -> None:
    """Two classes sharing a _field produce duplicate state — usually a copy-paste bug."""
    fields_in_order = _BS_FIELD_RE.findall(_read("binary_sensor.py"))
    seen: dict[str, int] = {}
    for f in fields_in_order:
        seen[f] = seen.get(f, 0) + 1
    dupes = {f: count for f, count in seen.items() if count > 1}
    assert not dupes, f"duplicate _field assignments in binary_sensor.py: {dupes}"
