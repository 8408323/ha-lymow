"""Tests for the Lymow config and options flows."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest
from aiohttp import web
from lymow.auth import LymowAuthError
from lymow.config_flow import (
    OAUTH_RESULT,
    OAUTH_START_URL,
    OAUTH_STATE,
    STEP_USER_SCHEMA,
    LymowConfigFlow,
    LymowOAuthStartView,
    LymowOptionsFlow,
    _is_urlsafe_token,
    _jwt_claim,
)
from lymow.const import (
    AUTH_METHOD_GOOGLE,
    AUTH_METHOD_PASSWORD,
    CONF_AUTH_METHOD,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_USERNAME,
    REGION_AUTO,
)

_config_flow_mod = sys.modules["lymow.config_flow"]

_PASSWORD_TOKENS = {
    "AccessToken": "access",
    "IdToken": "id-token",
    "RefreshToken": "refresh-token",
    "region": "eu-west-1",
}
_GOOGLE_TOKENS = {
    "AccessToken": "google-access",
    "IdToken": "google-id",
    "RefreshToken": "google-refresh",
    "ExpiresIn": 3600,
}
_CREDS = {
    "identity_id": "eu-west-1:stable-identity",
    "credentials": {"AccessKeyId": "key", "SecretKey": "secret", "SessionToken": "session"},
}


def _make_flow() -> LymowConfigFlow:
    flow = LymowConfigFlow.__new__(LymowConfigFlow)
    LymowConfigFlow.__init__(flow)
    flow.hass = MagicMock()
    flow.hass.data = {}
    flow.hass.config.internal_url = "http://ha.local:8123"
    flow.hass.http.register_view = MagicMock()
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=None)
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.hass.config_entries.async_reload = AsyncMock()
    flow.context = {}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: {"type": "create_entry", **kwargs})
    flow.async_show_form = MagicMock(side_effect=lambda **kwargs: {"type": "form", **kwargs})
    flow.async_abort = MagicMock(side_effect=lambda **kwargs: {"type": "abort", **kwargs})
    flow.async_update_reload_and_abort = MagicMock(
        side_effect=lambda _entry, **kwargs: {"type": "abort", "reason": kwargs["reason"]}
    )
    return flow


async def _select_password(flow: LymowConfigFlow, region: str = REGION_AUTO) -> None:
    result = await flow.async_step_user({CONF_AUTH_METHOD: AUTH_METHOD_PASSWORD, CONF_REGION: region})
    assert result["step_id"] == "password"


async def _select_google(flow: LymowConfigFlow, region: str = "eu-west-1") -> dict[str, Any]:
    result = await flow.async_step_user({CONF_AUTH_METHOD: AUTH_METHOD_GOOGLE, CONF_REGION: region})
    assert result["step_id"] == "google"
    return result


def _oauth_dependencies(
    *,
    tokens: dict[str, Any] | None = None,
    creds: dict[str, Any] | None = None,
    devices: Any = None,
) -> tuple[MagicMock, MagicMock]:
    auth = MagicMock()
    auth.exchange_oauth_code = AsyncMock(return_value=tokens or _GOOGLE_TOKENS)
    auth.get_aws_credentials = AsyncMock(return_value=creds or _CREDS)
    client = MagicMock()
    client.get_devices = AsyncMock(return_value=[{"deviceThingName": "thing-1"}] if devices is None else devices)
    return auth, client


def _callback(flow: LymowConfigFlow, *, state: str | None = None, code: str = "oauth-code") -> str:
    callback_state = flow._oauth_state if state is None else state
    return f"myapp://callback/?code={code}&state={callback_state}"


def _schema_field(result: dict[str, Any], field: str) -> tuple[Any, Any]:
    return next(
        (marker, validator) for marker, validator in result["data_schema"].schema.items() if marker.schema == field
    )


async def test_user_step_shows_auth_method_and_region_selection() -> None:
    flow = _make_flow()
    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    assert [marker.schema for marker in STEP_USER_SCHEMA.schema] == [CONF_AUTH_METHOD, CONF_REGION]


async def test_user_step_routes_to_password() -> None:
    flow = _make_flow()
    await _select_password(flow)
    assert flow._auth_method == AUTH_METHOD_PASSWORD


async def test_user_step_requires_an_explicit_google_region() -> None:
    flow = _make_flow()
    result = await flow.async_step_user({CONF_AUTH_METHOD: AUTH_METHOD_GOOGLE, CONF_REGION: REGION_AUTO})
    auth_method_marker, _ = _schema_field(result, CONF_AUTH_METHOD)
    region_marker, _ = _schema_field(result, CONF_REGION)

    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_REGION: "region_required"}
    assert auth_method_marker.default() == AUTH_METHOD_GOOGLE
    assert region_marker.default() == REGION_AUTO


def test_translation_files_ship_google_link_and_field_labels() -> None:
    integration_dir = Path(__file__).parents[1] / "custom_components" / "lymow"
    translation_text = (integration_dir / "translations" / "en.json").read_text()
    assert (integration_dir / "strings.json").read_text() == translation_text

    config = json.loads(translation_text)["config"]
    google_step = config["step"]["google"]
    assert "[Open Google sign-in]" not in google_step["description"]
    assert "Open a new browser tab" in google_step["description"]
    assert google_step["data"][OAUTH_START_URL] == "Google sign-in URL"
    assert "Read-only" in google_step["data_description"][OAUTH_START_URL]
    assert google_step["data"][OAUTH_RESULT] == "Authorization code or callback URL"
    assert google_step["data"][OAUTH_STATE] == "OAuth state"
    assert config["step"]["user"]["data"][CONF_REGION] == "AWS Region"
    assert json.loads(translation_text)["selector"]["region"]["options"]["auto"] == (
        "Auto-detect (password login only)"
    )
    assert "google_region" not in config["step"]
    assert "google_authorize" not in config["step"]


async def test_password_auto_region_creates_compatible_entry() -> None:
    flow = _make_flow()
    await _select_password(flow)
    auth = MagicMock()
    auth.login = AsyncMock(return_value=_PASSWORD_TOKENS)

    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
    ):
        result = await flow.async_step_password(
            {CONF_USERNAME: "User@Example.com", CONF_PASSWORD: "password-value", CONF_REGION: REGION_AUTO}
        )

    assert result["type"] == "create_entry"
    assert result["title"] == "User@Example.com"
    assert result["data"] == {
        CONF_AUTH_METHOD: AUTH_METHOD_PASSWORD,
        CONF_USERNAME: "User@Example.com",
        CONF_PASSWORD: "password-value",
        CONF_REGION: "eu-west-1",
        "refresh_token": "refresh-token",
    }
    flow.async_set_unique_id.assert_awaited_once_with("user@example.com")
    auth.login.assert_awaited_once_with("User@Example.com", "password-value")


async def test_password_explicit_region_uses_login_region() -> None:
    flow = _make_flow()
    await _select_password(flow, "us-east-2")
    tokens = {**_PASSWORD_TOKENS, "region": "us-east-2"}
    auth = MagicMock()
    auth.login_region = AsyncMock(return_value=tokens)

    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
    ):
        await flow.async_step_password(
            {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "password-value", CONF_REGION: "us-east-2"}
        )

    auth.login_region.assert_awaited_once_with("user@example.com", "password-value", "us-east-2")


async def test_password_failure_keeps_existing_error_behavior() -> None:
    flow = _make_flow()
    await _select_password(flow)
    auth = MagicMock()
    auth.login = AsyncMock(side_effect=ValueError("bad credentials"))

    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
    ):
        result = await flow.async_step_password(
            {CONF_USERNAME: "user@example.com", CONF_PASSWORD: "wrong-value", CONF_REGION: REGION_AUTO}
        )

    assert result["errors"] == {"base": "invalid_auth"}
    flow.async_create_entry.assert_not_called()


async def test_google_step_generates_pkce_once_and_registers_view_once() -> None:
    flow = _make_flow()
    first = await _select_google(flow)
    state = flow._oauth_state
    verifier = flow._pkce_verifier
    challenge = flow._pkce_challenge
    second = await flow.async_step_google()
    first_url_marker, first_url_selector = _schema_field(first, OAUTH_START_URL)
    second_url_marker, _ = _schema_field(second, OAUTH_START_URL)

    assert len(state or "") >= 32
    assert len(verifier or "") >= 64
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert flow._oauth_state == state
    assert first_url_marker.default() == second_url_marker.default()
    assert first_url_selector.config["read_only"] is True
    assert first_url_selector.config["type"] == "url"
    flow.hass.http.register_view.assert_called_once_with(LymowOAuthStartView)


async def test_google_start_link_uses_internal_url_and_flow_values() -> None:
    flow = _make_flow()
    result = await _select_google(flow, "ap-east-1")
    url_marker, _ = _schema_field(result, OAUTH_START_URL)
    parsed = urlparse(url_marker.default())
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}" == "http://ha.local:8123"
    assert parsed.path == "/api/lymow/oauth/start"
    assert query == {
        "region": ["ap-east-1"],
        "state": [flow._oauth_state],
        "code_challenge": [flow._pkce_challenge],
    }


async def test_google_start_link_falls_back_when_internal_url_missing() -> None:
    flow = _make_flow()
    flow.hass.config.internal_url = None
    result = await _select_google(flow)
    url_marker, _ = _schema_field(result, OAUTH_START_URL)
    assert url_marker.default().startswith("http://homeassistant.local:8123/")


async def test_google_callback_url_creates_entry_without_password() -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, client = _oauth_dependencies(tokens={**_GOOGLE_TOKENS, "IdToken": _jwt(email="owner@example.com")})

    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})

    assert result == {
        "type": "create_entry",
        "title": "owner@example.com",
        "data": {
            CONF_AUTH_METHOD: AUTH_METHOD_GOOGLE,
            CONF_REGION: "eu-west-1",
            "refresh_token": "google-refresh",
        },
    }
    assert CONF_PASSWORD not in result["data"]
    flow.async_set_unique_id.assert_awaited_once_with("eu-west-1:stable-identity")
    auth.exchange_oauth_code.assert_awaited_once_with(
        region="eu-west-1",
        code="oauth-code",
        redirect_uri="myapp://callback/",
        code_verifier=flow._pkce_verifier,
    )
    auth.get_aws_credentials.assert_awaited_once_with(_jwt(email="owner@example.com"), "eu-west-1")
    client.get_devices.assert_awaited_once()


async def test_google_bare_code_with_matching_state_succeeds() -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, client = _oauth_dependencies()
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: "bare-code", OAUTH_STATE: flow._oauth_state})
    assert result["type"] == "create_entry"


@pytest.mark.parametrize(
    ("oauth_result", "oauth_state", "expected_error"),
    [
        ("", "", "oauth_code_missing"),
        ("x" * 4097, "state", "oauth_code_missing"),
        ("bare-code", "", "oauth_state_mismatch"),
        ("bare-code", "wrong-state", "oauth_state_mismatch"),
        ("myapp://wrong/?code=value&state=value", "", "oauth_code_missing"),
        ("myapp://callback/?state=value", "", "oauth_code_missing"),
    ],
)
async def test_google_rejects_missing_code_or_invalid_state(
    oauth_result: str, oauth_state: str, expected_error: str
) -> None:
    flow = _make_flow()
    await _select_google(flow)
    result = await flow.async_step_google({OAUTH_RESULT: oauth_result, OAUTH_STATE: oauth_state})
    assert result["errors"] == {"base": expected_error}


async def test_google_rejects_callback_state_mismatch_before_exchange() -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, _ = _oauth_dependencies()
    with patch.object(_config_flow_mod, "LymowAuth", return_value=auth):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow, state="wrong-state")})
    assert result["errors"] == {"base": "oauth_state_mismatch"}
    auth.exchange_oauth_code.assert_not_awaited()


@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (LymowAuthError("expired"), "invalid_oauth_code"),
        (aiohttp.ClientConnectionError(), "cannot_connect"),
        (KeyError("missing"), "invalid_oauth_code"),
    ],
)
async def test_google_exchange_failures_show_specific_error(side_effect: Exception, expected_error: str) -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, client = _oauth_dependencies()
    auth.exchange_oauth_code.side_effect = side_effect
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})
    assert result["errors"] == {"base": expected_error}


async def test_google_missing_refresh_token_is_rejected() -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, client = _oauth_dependencies(tokens={**_GOOGLE_TOKENS, "RefreshToken": None})
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})
    assert result["errors"] == {"base": "missing_refresh_token"}


async def test_google_missing_identity_is_rejected() -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, client = _oauth_dependencies(creds={"identity_id": "", "credentials": {}})
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})
    assert result["errors"] == {"base": "invalid_oauth_code"}


@pytest.mark.parametrize("devices", [[], {"unexpected": "shape"}, [None]])
async def test_google_rejects_no_devices_or_malformed_device_response(devices: Any) -> None:
    flow = _make_flow()
    await _select_google(flow)
    auth, client = _oauth_dependencies(devices=devices)
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})
    if devices == []:
        assert result == {"type": "abort", "reason": "no_devices"}
    else:
        assert result["errors"] == {"base": "invalid_oauth_code"}


async def test_google_invalid_id_token_uses_region_title() -> None:
    flow = _make_flow()
    await _select_google(flow, "us-east-2")
    auth, client = _oauth_dependencies()
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})
    assert result["title"] == "Lymow Google (us-east-2)"


async def test_password_reauth_updates_existing_entry_and_reloads() -> None:
    flow = _make_flow()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "old-password",
        CONF_REGION: "eu-west-1",
    }
    flow.context = {"entry_id": entry.entry_id}
    flow.hass.config_entries.async_get_entry.return_value = entry
    auth = MagicMock()
    auth.login_region = AsyncMock(return_value=_PASSWORD_TOKENS)

    first = await flow.async_step_reauth(entry.data)
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
    ):
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "new-password"})

    assert first["step_id"] == "reauth_confirm"
    assert result == {"type": "abort", "reason": "reauth_successful"}
    updated = flow.async_update_reload_and_abort.call_args.kwargs["data_updates"]
    assert updated[CONF_PASSWORD] == "new-password"
    assert updated[CONF_AUTH_METHOD] == AUTH_METHOD_PASSWORD
    assert updated["refresh_token"] == "refresh-token"
    flow.async_update_reload_and_abort.assert_called_once_with(
        entry,
        data_updates=updated,
        reason="reauth_successful",
    )


async def test_password_reauth_auto_region_and_failure_paths() -> None:
    flow = _make_flow()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {CONF_USERNAME: "user@example.com", CONF_REGION: REGION_AUTO}
    flow.context = {"entry_id": entry.entry_id}
    flow.hass.config_entries.async_get_entry.return_value = entry
    await flow.async_step_reauth(entry.data)
    auth = MagicMock()
    auth.login = AsyncMock(side_effect=ValueError("bad credentials"))
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
    ):
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: "wrong-password"})
    assert result["errors"] == {"base": "invalid_auth"}
    auth.login.assert_awaited_once()


async def test_google_reauth_replaces_token_without_creating_duplicate() -> None:
    flow = _make_flow()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {
        CONF_AUTH_METHOD: AUTH_METHOD_GOOGLE,
        CONF_REGION: "eu-west-1",
        "refresh_token": "old-refresh",
    }
    flow.context = {"entry_id": entry.entry_id}
    flow.hass.config_entries.async_get_entry.return_value = entry
    first = await flow.async_step_reauth(entry.data)
    auth, client = _oauth_dependencies()
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
        patch.object(_config_flow_mod, "LymowApiClient", return_value=client),
    ):
        result = await flow.async_step_google({OAUTH_RESULT: _callback(flow)})

    assert first["type"] == "form"
    assert first["step_id"] == "google"
    assert result == {"type": "abort", "reason": "reauth_successful"}
    updated = flow.async_update_reload_and_abort.call_args.kwargs["data_updates"]
    assert updated["refresh_token"] == "google-refresh"
    flow.async_create_entry.assert_not_called()
    flow.async_set_unique_id.assert_not_awaited()


async def test_reauth_missing_entry_and_invalid_google_region_abort() -> None:
    missing = _make_flow()
    missing.context = {"entry_id": "missing"}
    assert await missing.async_step_reauth({}) == {"type": "abort", "reason": "reauth_entry_missing"}
    assert await missing.async_step_reauth_confirm() == {"type": "abort", "reason": "reauth_entry_missing"}

    google = _make_flow()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {CONF_AUTH_METHOD: AUTH_METHOD_GOOGLE, CONF_REGION: REGION_AUTO}
    google.context = {"entry_id": entry.entry_id}
    google.hass.config_entries.async_get_entry.return_value = entry
    assert await google.async_step_reauth(entry.data) == {"type": "abort", "reason": "region_required"}


async def test_internal_google_guards_raise_when_state_missing() -> None:
    flow = _make_flow()
    flow._region = "eu-west-1"
    with pytest.raises(RuntimeError, match="state"):
        flow._oauth_start_url()
    with pytest.raises(RuntimeError, match="verifier"):
        await flow._async_complete_google("code")
    with pytest.raises(RuntimeError, match="entry"):
        await flow._async_update_reauth_entry({})


async def test_oauth_start_view_shows_reference_callback_recovery_flow() -> None:
    assert LymowOAuthStartView.requires_auth is False

    request = MagicMock()
    request.query = {
        "region": "eu-west-1",
        "state": "s" * 43,
        "code_challenge": "c" * 43,
    }
    hass = MagicMock()
    request.app = {"hass": hass}
    auth = MagicMock()
    auth.get_oauth_authorize_url.return_value = "https://eu-auth.lymow.com/oauth2/authorize?safe=true"
    with (
        patch.object(_config_flow_mod, "async_get_clientsession", return_value=MagicMock()),
        patch.object(_config_flow_mod, "LymowAuth", return_value=auth),
    ):
        response = await LymowOAuthStartView().get(request)

    assert response.status == 200
    assert "Connect Lymow with Google" in response.text
    assert "Sign in with Google" in response.text
    assert "Get authorization code" in response.text
    assert "Preserve log" in response.text
    assert "DevTools" in response.text
    assert response.text.count('href="https://eu-auth.lymow.com/oauth2/authorize?safe=true"') == 2
    hass.config_entries.flow.async_configure.assert_not_called()
    auth.get_oauth_authorize_url.assert_called_once_with(
        region="eu-west-1",
        redirect_uri="myapp://callback/",
        state="s" * 43,
        code_challenge="c" * 43,
    )


@pytest.mark.parametrize(
    "query",
    [
        {"region": "invalid", "state": "s" * 43, "code_challenge": "c" * 43},
        {"region": "eu-west-1", "state": "short", "code_challenge": "c" * 43},
        {"region": "eu-west-1", "state": "s" * 43, "code_challenge": "bad value"},
    ],
)
async def test_oauth_start_view_rejects_untrusted_query_values(query: dict[str, str]) -> None:
    request = MagicMock()
    request.query = query
    request.app = {"hass": MagicMock()}
    with pytest.raises(web.HTTPBadRequest) as exc_info:
        await LymowOAuthStartView().get(request)
    assert exc_info.value.text == "Invalid OAuth start request"


def test_oauth_helpers_validate_tokens_and_jwt_claims() -> None:
    assert _is_urlsafe_token("aB0-_" * 9, 32, 128)
    assert not _is_urlsafe_token("bad value", 1, 128)
    assert _jwt_claim(_jwt(sub="stable-sub"), "sub") == "stable-sub"
    assert _jwt_claim("not-a-jwt", "sub") is None
    assert _jwt_claim("header.a.signature", "sub") is None
    assert _jwt_claim(_jwt_payload(["not", "a", "mapping"]), "sub") is None


def _jwt(**claims: str) -> str:
    return _jwt_payload(claims)


def _jwt_payload(payload: Any) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.signature"


def _make_options_flow(options: dict[str, Any] | None = None) -> LymowOptionsFlow:
    flow = LymowOptionsFlow.__new__(LymowOptionsFlow)
    flow.config_entry = MagicMock()
    flow.config_entry.options = options or {}
    flow.async_create_entry = MagicMock(side_effect=lambda **kwargs: {"type": "create_entry", **kwargs})
    flow.async_show_form = MagicMock(side_effect=lambda **kwargs: {"type": "form", **kwargs})
    return flow


def test_async_get_options_flow_returns_options_flow() -> None:
    assert isinstance(LymowConfigFlow.async_get_options_flow(MagicMock()), LymowOptionsFlow)


async def test_options_flow_shows_current_values() -> None:
    flow = _make_options_flow({"ble_address": "AA:BB", "rtsp_path": "main", "rtsp_port": 10023})
    result = await flow.async_step_init()
    assert result["type"] == "form"
    assert result["step_id"] == "init"


@pytest.mark.parametrize(
    ("input_path", "expected_path"),
    [("  h264ESVideoMain  ", "h264ESVideoMain"), ("  ///h264ESVideoMain  ", "h264ESVideoMain"), ("///", "")],
)
async def test_options_flow_normalizes_values(input_path: str, expected_path: str) -> None:
    flow = _make_options_flow()
    result = await flow.async_step_init(
        {"ble_address": "  AA:BB:CC:DD:EE:FF  ", "rtsp_path": input_path, "rtsp_port": 10023}
    )
    assert result["data"] == {
        "ble_address": "AA:BB:CC:DD:EE:FF",
        "rtsp_path": expected_path,
        "rtsp_port": 10023,
    }


async def test_options_flow_uses_defaults_for_missing_values() -> None:
    flow = _make_options_flow()
    result = await flow.async_step_init({})
    assert result["data"] == {"ble_address": "", "rtsp_path": "", "rtsp_port": 10022}
