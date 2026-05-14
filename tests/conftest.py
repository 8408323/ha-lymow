"""Pytest configuration — make lymow submodules importable without the HA stack."""

from __future__ import annotations

import importlib.util
import os
import sys

_BASE = os.path.join(os.path.dirname(__file__), "..", "custom_components", "lymow")


def _load_lymow_module(name: str) -> None:
    """Load a lymow submodule directly, bypassing lymow/__init__.py."""
    if f"lymow.{name}" in sys.modules:
        return
    path = os.path.join(_BASE, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"lymow.{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"lymow.{name}"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]


# Pre-load modules that tests need so `from lymow.auth import ...` works.
_load_lymow_module("const")
_load_lymow_module("auth")
_load_lymow_module("api")
_load_lymow_module("mqtt")
_load_lymow_module("protocol")

# If homeassistant is installed (system Python), pre-load real HA modules so
# that test_coordinator.py's setdefault stubs don't shadow the real package,
# and load the HA platform modules so their tests can import them.
try:
    import homeassistant.components.lawn_mower  # noqa: F401
    import homeassistant.components.number  # noqa: F401
    import homeassistant.components.sensor  # noqa: F401
    import homeassistant.components.switch  # noqa: F401
    import homeassistant.config_entries  # noqa: F401
    import homeassistant.core  # noqa: F401
    import homeassistant.exceptions  # noqa: F401
    import homeassistant.helpers.aiohttp_client  # noqa: F401
    import homeassistant.helpers.entity_platform  # noqa: F401
    import homeassistant.helpers.selector  # noqa: F401
    import homeassistant.helpers.update_coordinator  # noqa: F401

    _load_lymow_module("coordinator")
    _load_lymow_module("config_flow")
    _load_lymow_module("sensor")
    _load_lymow_module("number")
    _load_lymow_module("switch")
    _load_lymow_module("lawn_mower")
except ImportError:
    pass
