"""Tests for the ERROR / WARNING name + description tables extracted from the APK."""

from __future__ import annotations

from lymow.const import (
    ERROR_DESCRIPTIONS,
    ERROR_NAMES,
    WARNING_DESCRIPTIONS,
    WARNING_NAMES,
)


def test_error_table_covers_0_through_90() -> None:
    """ERROR_NAMES is the canonical map from the APK; 0..90 are all defined
    (PbOutput.fromObject's verify-and-cast loop enumerates every value)."""
    assert set(ERROR_NAMES) == set(range(91))


def test_error_descriptions_cover_every_named_code() -> None:
    """Every entry in ERROR_NAMES must have a user-facing description — the
    sensor uses ERROR_DESCRIPTIONS.get(code, f"Unknown ({code})") and we
    don't want any known code to slip through to "Unknown"."""
    missing = set(ERROR_NAMES) - set(ERROR_DESCRIPTIONS)
    assert not missing, f"ERROR_DESCRIPTIONS missing labels for {sorted(missing)}"


def test_error_descriptions_have_no_extras() -> None:
    """No description for a code that isn't actually defined by the robot."""
    extras = set(ERROR_DESCRIPTIONS) - set(ERROR_NAMES)
    assert not extras, f"ERROR_DESCRIPTIONS has stray entries: {sorted(extras)}"


def test_specific_known_error_codes_match_apk() -> None:
    """Spot-check a handful of codes against the APK identifiers to catch
    accidental drift if the dict is ever re-extracted."""
    assert ERROR_NAMES[0] == "ERROR_NONE"
    assert ERROR_NAMES[31] == "ERROR_LOW_BATTERY"
    assert ERROR_NAMES[55] == "ERROR_CHARGE_STATION_NOT_FOUND"
    assert ERROR_NAMES[64] == "ERROR_ROBOT_IN_NOGO"
    assert ERROR_NAMES[90] == "ERROR_CODE_MAX"


def test_warning_table_skips_value_57_intentionally() -> None:
    """Value 57 is absent on the wire — the APK enum has a gap there. Every
    other value in 0..63 is defined."""
    expected = set(range(64)) - {57}
    assert set(WARNING_NAMES) == expected


def test_warning_descriptions_cover_every_named_code() -> None:
    missing = set(WARNING_NAMES) - set(WARNING_DESCRIPTIONS)
    assert not missing, f"WARNING_DESCRIPTIONS missing labels for {sorted(missing)}"


def test_warning_descriptions_have_no_extras() -> None:
    extras = set(WARNING_DESCRIPTIONS) - set(WARNING_NAMES)
    assert not extras, f"WARNING_DESCRIPTIONS has stray entries: {sorted(extras)}"


def test_specific_known_warning_codes_match_apk() -> None:
    assert WARNING_NAMES[0] == "WARNING_NONE"
    assert WARNING_NAMES[19] == "WARNING_LOC_RTK_SIGNAL_BAD"
    # APK identifier typo "WARING" preserved verbatim — don't silently fix it
    assert WARNING_NAMES[34] == "WARING_PP_LATERAL_ERROR_LARGE"
    assert WARNING_NAMES[36] == "WARING_PP_EXECUTION"
    assert 57 not in WARNING_NAMES  # explicit gap


def test_descriptions_are_short_one_liners() -> None:
    """Sensor attribute values render in HA UI; keep them <80 chars to avoid
    wrapping in the device card. ERROR and WARNING tables share numeric keys
    (0..63 overlap), so iterate each separately — a dict-merge would mask any
    long WARNING_* description that collides with an ERROR_* key."""
    from itertools import chain

    for d in chain(ERROR_DESCRIPTIONS.values(), WARNING_DESCRIPTIONS.values()):
        assert "\n" not in d
        assert len(d) <= 80, f"description too long ({len(d)} chars): {d!r}"
