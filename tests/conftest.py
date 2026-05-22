"""Pytest configuration — make lymow submodules importable without the HA stack."""

from __future__ import annotations

import importlib.util
import os
import sys
import types

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
_load_lymow_module("geometry")
_load_lymow_module("bluetooth")

# If homeassistant is installed (system Python), pre-load real HA modules so
# that test_coordinator.py's setdefault stubs don't shadow the real package,
# and load the HA platform modules so their tests can import them.
try:
    import homeassistant.components.binary_sensor  # noqa: F401
    import homeassistant.components.button  # noqa: F401
    import homeassistant.components.device_tracker  # noqa: F401
    import homeassistant.components.lawn_mower  # noqa: F401
    import homeassistant.components.number  # noqa: F401
    import homeassistant.components.sensor  # noqa: F401
    import homeassistant.components.switch  # noqa: F401
    import homeassistant.components.update  # noqa: F401
    import homeassistant.config_entries  # noqa: F401
    import homeassistant.core  # noqa: F401
    import homeassistant.exceptions  # noqa: F401
    import homeassistant.helpers.aiohttp_client  # noqa: F401
    import homeassistant.helpers.entity_platform  # noqa: F401
    import homeassistant.helpers.selector  # noqa: F401
    import homeassistant.helpers.update_coordinator  # noqa: F401
    from homeassistant.components import camera as _ha_camera  # noqa: F401

    _load_lymow_module("coordinator")
    _load_lymow_module("config_flow")
    _load_lymow_module("sensor")
    _load_lymow_module("number")
    _load_lymow_module("switch")
    _load_lymow_module("binary_sensor")
    _load_lymow_module("button")
    _load_lymow_module("camera")
    _load_lymow_module("device_tracker")
    _load_lymow_module("lawn_mower")
    _load_lymow_module("update")
except ImportError:
    # HA not installed (uv/Python 3.13 CI env) — inject minimal stubs so all
    # lymow platform modules can be loaded and their tests can run.
    import enum
    from enum import IntEnum, IntFlag

    # ── homeassistant root ────────────────────────────────────────────────────
    _ha = types.ModuleType("homeassistant")
    sys.modules.setdefault("homeassistant", _ha)

    # ── homeassistant.const ───────────────────────────────────────────────────
    _ha_const = types.ModuleType("homeassistant.const")
    _ha_const.PERCENTAGE = "%"  # type: ignore[attr-defined]

    class _UnitOfArea:
        SQUARE_METERS = "m²"

    class _UnitOfLength:
        METERS = "m"
        CENTIMETERS = "cm"
        MILLIMETERS = "mm"

    class _UnitOfTime:
        SECONDS = "s"
        MINUTES = "min"
        HOURS = "h"

    _ha_const.UnitOfArea = _UnitOfArea  # type: ignore[attr-defined]
    _ha_const.UnitOfLength = _UnitOfLength  # type: ignore[attr-defined]
    _ha_const.UnitOfTime = _UnitOfTime  # type: ignore[attr-defined]
    _ha_const.DEGREE = "°"  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.const", _ha_const)

    # ── homeassistant.core ────────────────────────────────────────────────────
    _ha_core = types.ModuleType("homeassistant.core")

    class _HomeAssistant:
        pass

    class _ServiceCall:
        pass

    def _callback(func):  # type: ignore[return]
        return func

    class _SupportsResponse(str, enum.Enum):
        NONE = "none"
        OPTIONAL = "optional"
        ONLY = "only"

    _ha_core.HomeAssistant = _HomeAssistant  # type: ignore[attr-defined]
    _ha_core.ServiceCall = _ServiceCall  # type: ignore[attr-defined]
    _ha_core.callback = _callback  # type: ignore[attr-defined]
    _ha_core.SupportsResponse = _SupportsResponse  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.core", _ha_core)

    # ── homeassistant.exceptions ──────────────────────────────────────────────
    _ha_exc = types.ModuleType("homeassistant.exceptions")

    class _HomeAssistantError(Exception):
        pass

    class _ServiceValidationError(_HomeAssistantError):
        pass

    _ha_exc.HomeAssistantError = _HomeAssistantError  # type: ignore[attr-defined]
    _ha_exc.ServiceValidationError = _ServiceValidationError  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.exceptions", _ha_exc)

    # ── homeassistant.config_entries ──────────────────────────────────────────
    _ha_ce = types.ModuleType("homeassistant.config_entries")

    class _ConfigEntry:
        pass

    class _ConfigFlow:
        def __init_subclass__(cls, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)

    class _OptionsFlow:
        pass

    _ConfigFlowResult = dict
    _ha_ce.ConfigEntry = _ConfigEntry  # type: ignore[attr-defined]
    _ha_ce.ConfigFlow = _ConfigFlow  # type: ignore[attr-defined]
    _ha_ce.ConfigFlowResult = _ConfigFlowResult  # type: ignore[attr-defined]
    _ha_ce.OptionsFlow = _OptionsFlow  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.config_entries", _ha_ce)

    # ── homeassistant.helpers (namespace) ────────────────────────────────────
    _ha_helpers = types.ModuleType("homeassistant.helpers")
    sys.modules.setdefault("homeassistant.helpers", _ha_helpers)

    # ── homeassistant.helpers.update_coordinator ─────────────────────────────
    _ha_uc = types.ModuleType("homeassistant.helpers.update_coordinator")

    class _CoordinatorEntity:
        def __init__(self, coordinator, *args, **kwargs):
            self.coordinator = coordinator
            self.entity_id: str | None = None

        def __class_getitem__(cls, item):
            return cls

    class _DataUpdateCoordinator:
        def __init__(self, hass=None, logger=None, *, name=None, update_interval=None, **kwargs):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data: dict | None = None
            self._listeners: list = []

        def __class_getitem__(cls, item):
            return cls

        async def async_shutdown(self):
            pass

        def async_set_updated_data(self, data):
            self.data = data

    class _UpdateFailed(Exception):
        pass

    _ha_uc.CoordinatorEntity = _CoordinatorEntity  # type: ignore[attr-defined]
    _ha_uc.DataUpdateCoordinator = _DataUpdateCoordinator  # type: ignore[attr-defined]
    _ha_uc.UpdateFailed = _UpdateFailed  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.helpers.update_coordinator", _ha_uc)

    # ── homeassistant.helpers.entity_platform ─────────────────────────────────
    _ha_ep = types.ModuleType("homeassistant.helpers.entity_platform")
    _ha_ep.AddEntitiesCallback = None  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.helpers.entity_platform", _ha_ep)

    # ── homeassistant.helpers.aiohttp_client ─────────────────────────────────
    _ha_ac = types.ModuleType("homeassistant.helpers.aiohttp_client")

    def _async_get_clientsession(hass):  # type: ignore[return]
        pass

    _ha_ac.async_get_clientsession = _async_get_clientsession  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.helpers.aiohttp_client", _ha_ac)

    # ── homeassistant.helpers.selector ────────────────────────────────────────
    _ha_sel = types.ModuleType("homeassistant.helpers.selector")

    class _SelectSelectorMode(str, enum.Enum):
        DROPDOWN = "dropdown"
        LIST = "list"

    class _SelectSelectorConfig:
        def __init__(self, **kwargs):
            pass

    class _SelectSelector:
        def __init__(self, config=None):
            pass

        def __call__(self, value):
            return value

    _ha_sel.SelectSelector = _SelectSelector  # type: ignore[attr-defined]
    _ha_sel.SelectSelectorConfig = _SelectSelectorConfig  # type: ignore[attr-defined]
    _ha_sel.SelectSelectorMode = _SelectSelectorMode  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.helpers.selector", _ha_sel)

    # ── homeassistant.helpers.config_validation ───────────────────────────────
    _ha_cv = types.ModuleType("homeassistant.helpers.config_validation")
    _ha_cv.string = str  # type: ignore[attr-defined]
    _ha_cv.entity_ids = lambda v: v  # type: ignore[attr-defined]
    _ha_cv.ensure_list = lambda v: v if isinstance(v, list) else [v]  # type: ignore[attr-defined]
    _ha_cv.boolean = lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.helpers.config_validation", _ha_cv)

    # ── homeassistant.components (namespace) ──────────────────────────────────
    _ha_comp = types.ModuleType("homeassistant.components")
    sys.modules.setdefault("homeassistant.components", _ha_comp)

    # ── homeassistant.components.lawn_mower ───────────────────────────────────
    _ha_lm = types.ModuleType("homeassistant.components.lawn_mower")

    class LawnMowerActivity(IntEnum):
        MOWING = 1
        RETURNING = 2
        DOCKED = 3
        PAUSED = 4
        ERROR = 5

    class LawnMowerEntityFeature(IntFlag):
        START_MOWING = 1
        PAUSE = 2
        DOCK = 4

    class _LawnMowerEntity:
        pass

    _ha_lm.LawnMowerActivity = LawnMowerActivity  # type: ignore[attr-defined]
    _ha_lm.LawnMowerEntityFeature = LawnMowerEntityFeature  # type: ignore[attr-defined]
    _ha_lm.LawnMowerEntity = _LawnMowerEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.lawn_mower", _ha_lm)

    # ── homeassistant.components.sensor ───────────────────────────────────────
    _ha_sensor = types.ModuleType("homeassistant.components.sensor")

    class _SensorDeviceClass(str, enum.Enum):
        POWER = "power"
        ENERGY = "energy"
        BATTERY = "battery"
        SIGNAL_STRENGTH = "signal_strength"
        DURATION = "duration"
        TIMESTAMP = "timestamp"

    class _SensorStateClass(str, enum.Enum):
        MEASUREMENT = "measurement"
        TOTAL_INCREASING = "total_increasing"

    from dataclasses import dataclass as _dataclass

    @_dataclass(frozen=True)
    class _SensorEntityDescription:
        key: str = ""
        name: str | None = None
        native_unit_of_measurement: str | None = None
        device_class: str | None = None
        state_class: str | None = None
        icon: str | None = None
        entity_registry_enabled_default: bool = True
        entity_category: str | None = None
        suggested_display_precision: int | None = None

    class _SensorEntity:
        pass

    _ha_sensor.SensorDeviceClass = _SensorDeviceClass  # type: ignore[attr-defined]
    _ha_sensor.SensorStateClass = _SensorStateClass  # type: ignore[attr-defined]
    _ha_sensor.SensorEntityDescription = _SensorEntityDescription  # type: ignore[attr-defined]
    _ha_sensor.SensorEntity = _SensorEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.sensor", _ha_sensor)

    # ── homeassistant.components.number ───────────────────────────────────────
    _ha_number = types.ModuleType("homeassistant.components.number")

    class _NumberDeviceClass(str, enum.Enum):
        DISTANCE = "distance"

    class _NumberMode(str, enum.Enum):
        BOX = "box"
        SLIDER = "slider"

    class _NumberEntity:
        pass

    _ha_number.NumberDeviceClass = _NumberDeviceClass  # type: ignore[attr-defined]
    _ha_number.NumberMode = _NumberMode  # type: ignore[attr-defined]
    _ha_number.NumberEntity = _NumberEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.number", _ha_number)

    # ── homeassistant.components.switch ───────────────────────────────────────
    _ha_switch = types.ModuleType("homeassistant.components.switch")

    class _SwitchEntity:
        pass

    _ha_switch.SwitchEntity = _SwitchEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.switch", _ha_switch)

    # ── homeassistant.components.device_tracker ───────────────────────────────
    _ha_dt = types.ModuleType("homeassistant.components.device_tracker")

    class _SourceType(str, enum.Enum):
        GPS = "gps"
        ROUTER = "router"
        BLUETOOTH = "bluetooth"

    class _TrackerEntity:
        pass

    _ha_dt.SourceType = _SourceType  # type: ignore[attr-defined]
    _ha_dt.TrackerEntity = _TrackerEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.device_tracker", _ha_dt)

    # ── homeassistant.components.binary_sensor ────────────────────────────────
    _ha_bs = types.ModuleType("homeassistant.components.binary_sensor")

    class _BinarySensorDeviceClass(str, enum.Enum):
        BATTERY_CHARGING = "battery_charging"
        TAMPER = "tamper"
        CONNECTIVITY = "connectivity"
        PROBLEM = "problem"
        LOCK = "lock"

    class _BinarySensorEntity:
        pass

    _ha_bs.BinarySensorDeviceClass = _BinarySensorDeviceClass  # type: ignore[attr-defined]
    _ha_bs.BinarySensorEntity = _BinarySensorEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.binary_sensor", _ha_bs)

    # ── homeassistant.components.button ───────────────────────────────────────
    _ha_button = types.ModuleType("homeassistant.components.button")

    class _ButtonEntity:
        pass

    _ha_button.ButtonEntity = _ButtonEntity  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.button", _ha_button)

    # ── homeassistant.components.camera ───────────────────────────────────────
    _ha_camera = types.ModuleType("homeassistant.components.camera")

    class _Camera:
        def __init__(self):
            pass

    class _CameraEntityFeature(IntFlag):
        ON_OFF = 1
        STREAM = 2

    _ha_camera.Camera = _Camera  # type: ignore[attr-defined]
    _ha_camera.CameraEntityFeature = _CameraEntityFeature  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.camera", _ha_camera)

    # ── homeassistant.components.ffmpeg ───────────────────────────────────────
    _ha_ffmpeg = types.ModuleType("homeassistant.components.ffmpeg")

    async def _async_get_image(hass, input_source, **kwargs):  # type: ignore[no-untyped-def]
        return b""

    _ha_ffmpeg.async_get_image = _async_get_image  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.ffmpeg", _ha_ffmpeg)

    # ── homeassistant.components.bluetooth ────────────────────────────────────
    _ha_bt = types.ModuleType("homeassistant.components.bluetooth")

    def _async_discovered_service_info(hass, connectable=True):  # type: ignore[no-untyped-def]
        return []

    _ha_bt.async_discovered_service_info = _async_discovered_service_info  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.bluetooth", _ha_bt)

    # ── homeassistant.components.update ───────────────────────────────────────
    _ha_update = types.ModuleType("homeassistant.components.update")

    class _UpdateEntityFeature(IntFlag):
        INSTALL = 1
        RELEASE_NOTES = 2

    class _UpdateEntity:
        pass

    _ha_update.UpdateEntity = _UpdateEntity  # type: ignore[attr-defined]
    _ha_update.UpdateEntityFeature = _UpdateEntityFeature  # type: ignore[attr-defined]
    sys.modules.setdefault("homeassistant.components.update", _ha_update)

    # Now load the platform modules that depend on the above stubs.
    _load_lymow_module("coordinator")
    _load_lymow_module("config_flow")
    _load_lymow_module("sensor")
    _load_lymow_module("number")
    _load_lymow_module("switch")
    _load_lymow_module("device_tracker")
    _load_lymow_module("binary_sensor")
    _load_lymow_module("button")
    _load_lymow_module("camera")
    _load_lymow_module("lawn_mower")
    _load_lymow_module("update")
