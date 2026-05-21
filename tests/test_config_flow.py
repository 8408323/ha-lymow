"""Tests for config_flow.py — LymowConfigFlow."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

from lymow.config_flow import LymowConfigFlow
from lymow.const import (
    CONF_PASSWORD,
    CONF_REGION,
    CONF_USERNAME,
    REGION_AUTO,
)

_config_flow_mod = sys.modules["lymow.config_flow"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_flow() -> LymowConfigFlow:
    """Create a LymowConfigFlow instance bypassing ConfigFlow.__init__."""
    flow = LymowConfigFlow.__new__(LymowConfigFlow)
    flow.hass = MagicMock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(return_value={"type": "form", "errors": {}})
    return flow


_TOKENS = {
    "AccessToken": "access",
    "IdToken": "id-token",
    "RefreshToken": "refresh-token",
    "region": "eu-west-1",
}


# ---------------------------------------------------------------------------
# async_step_user — None input (show form)
# ---------------------------------------------------------------------------


async def test_step_user_none_shows_form() -> None:
    flow = _make_flow()
    result = await flow.async_step_user(None)
    flow.async_show_form.assert_called_once()
    assert result["type"] == "form"


async def test_step_user_none_no_errors() -> None:
    flow = _make_flow()
    await flow.async_step_user(None)
    _, kwargs = flow.async_show_form.call_args
    assert kwargs.get("errors", {}) == {} or flow.async_show_form.call_args[1].get("errors") == {}


# ---------------------------------------------------------------------------
# async_step_user — successful login with REGION_AUTO
# ---------------------------------------------------------------------------


async def test_step_user_valid_credentials_creates_entry() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_REGION: REGION_AUTO,
    }

    mock_auth = MagicMock()
    mock_auth.login = AsyncMock(return_value=_TOKENS)
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            result = await flow.async_step_user(user_input)

    flow.async_create_entry.assert_called_once()
    assert result["type"] == "create_entry"


async def test_step_user_valid_entry_data_contains_refresh_token() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_REGION: REGION_AUTO,
    }

    mock_auth = MagicMock()
    mock_auth.login = AsyncMock(return_value=_TOKENS)
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            await flow.async_step_user(user_input)

    _, kwargs = flow.async_create_entry.call_args
    assert kwargs["data"]["refresh_token"] == "refresh-token"


async def test_step_user_valid_entry_sets_region_from_tokens() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_REGION: REGION_AUTO,
    }

    mock_auth = MagicMock()
    mock_auth.login = AsyncMock(return_value=_TOKENS)
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            await flow.async_step_user(user_input)

    _, kwargs = flow.async_create_entry.call_args
    assert kwargs["data"][CONF_REGION] == "eu-west-1"


async def test_step_user_calls_async_set_unique_id() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "User@Example.com",
        CONF_PASSWORD: "secret",
        CONF_REGION: REGION_AUTO,
    }

    mock_auth = MagicMock()
    mock_auth.login = AsyncMock(return_value=_TOKENS)
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            await flow.async_step_user(user_input)

    # unique_id should be lowercase username
    flow.async_set_unique_id.assert_called_once_with("user@example.com")


# ---------------------------------------------------------------------------
# async_step_user — region override (login_region)
# ---------------------------------------------------------------------------


async def test_step_user_with_region_override_calls_login_region() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_REGION: "eu-west-1",
    }

    mock_auth = MagicMock()
    mock_auth.login_region = AsyncMock(return_value=_TOKENS)
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            await flow.async_step_user(user_input)

    mock_auth.login_region.assert_called_once_with("user@example.com", "secret", "eu-west-1")


# ---------------------------------------------------------------------------
# async_step_user — login failure
# ---------------------------------------------------------------------------


async def test_step_user_login_failure_shows_form_with_error() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "wrong",
        CONF_REGION: REGION_AUTO,
    }

    mock_auth = MagicMock()
    mock_auth.login = AsyncMock(side_effect=ValueError("bad creds"))
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            result = await flow.async_step_user(user_input)

    assert result["type"] == "form"
    _, kwargs = flow.async_show_form.call_args
    assert kwargs["errors"].get("base") == "invalid_auth"


async def test_step_user_login_failure_does_not_create_entry() -> None:
    flow = _make_flow()
    user_input = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "wrong",
        CONF_REGION: REGION_AUTO,
    }

    mock_auth = MagicMock()
    mock_auth.login = AsyncMock(side_effect=Exception("network error"))
    with patch("homeassistant.helpers.aiohttp_client.async_get_clientsession", return_value=MagicMock()):
        with patch.object(_config_flow_mod, "LymowAuth", return_value=mock_auth):
            await flow.async_step_user(user_input)

    flow.async_create_entry.assert_not_called()


# ---------------------------------------------------------------------------
# Options flow (BLE address)
# ---------------------------------------------------------------------------


def _make_options_flow(options: dict | None = None):
    from lymow.config_flow import LymowOptionsFlow

    flow = LymowOptionsFlow.__new__(LymowOptionsFlow)
    flow.config_entry = MagicMock()
    flow.config_entry.options = options or {}
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    return flow


def test_async_get_options_flow_returns_options_flow():
    from lymow.config_flow import LymowOptionsFlow

    flow = LymowConfigFlow.async_get_options_flow(MagicMock())
    assert isinstance(flow, LymowOptionsFlow)


async def test_options_flow_shows_form_when_no_input():
    flow = _make_options_flow({"ble_address": "AA:BB"})
    result = await flow.async_step_init(None)
    assert result["type"] == "form"
    flow.async_show_form.assert_called_once()
    assert flow.async_show_form.call_args.kwargs["step_id"] == "init"


async def test_options_flow_saves_ble_address():
    flow = _make_options_flow()
    await flow.async_step_init({"ble_address": "  AA:BB:CC:DD:EE:FF  "})
    flow.async_create_entry.assert_called_once()
    assert flow.async_create_entry.call_args.kwargs["data"]["ble_address"] == "AA:BB:CC:DD:EE:FF"


async def test_options_flow_blank_address_when_omitted():
    flow = _make_options_flow()
    await flow.async_step_init({})
    assert flow.async_create_entry.call_args.kwargs["data"]["ble_address"] == ""
