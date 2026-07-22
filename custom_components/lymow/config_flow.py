"""Config flow for Lymow integration."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import html
import json
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiohttp
import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import LymowApiClient
from .auth import LymowAuth, LymowAuthError
from .const import (
    AUTH_METHOD_GOOGLE,
    AUTH_METHOD_PASSWORD,
    COGNITO_DOMAINS,
    CONF_AUTH_METHOD,
    CONF_BLE_ADDRESS,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_RTSP_PATH,
    CONF_RTSP_PORT,
    CONF_USERNAME,
    DOMAIN,
    REGION_AUTO,
    REGION_CHOICES,
    RTSP_PORT,
    normalize_rtsp_path,
)

OAUTH_REDIRECT_URI = "myapp://callback/"
OAUTH_RESULT = "oauth_result"
OAUTH_START_URL = "oauth_start_url"
OAUTH_STATE = "oauth_state"
_OAUTH_VIEW_REGISTERED_KEY = f"{DOMAIN}_oauth_view_registered"


def _region_selector(options: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="region",
        )
    )


def _user_schema(
    auth_method: str = AUTH_METHOD_PASSWORD,
    region: str = REGION_AUTO,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_AUTH_METHOD, default=auth_method): SelectSelector(
                SelectSelectorConfig(
                    options=[AUTH_METHOD_PASSWORD, AUTH_METHOD_GOOGLE],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="auth_method",
                )
            ),
            vol.Required(CONF_REGION, default=region): _region_selector(REGION_CHOICES),
        }
    )


STEP_USER_SCHEMA = _user_schema()

STEP_PASSWORD_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _google_schema(auth_url: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(OAUTH_START_URL, default=auth_url): TextSelector(
                TextSelectorConfig(read_only=True, type=TextSelectorType.URL)
            ),
            vol.Required(OAUTH_RESULT): str,
            vol.Optional(OAUTH_STATE): str,
        }
    )


class LymowConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._auth_method = AUTH_METHOD_PASSWORD
        self._region = REGION_AUTO
        self._oauth_state: str | None = None
        self._pkce_verifier: str | None = None
        self._pkce_challenge: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._auth_method = user_input[CONF_AUTH_METHOD]
            self._region = user_input[CONF_REGION]
            if self._auth_method == AUTH_METHOD_GOOGLE:
                if self._region in COGNITO_DOMAINS:
                    self._prepare_oauth()
                    return await self.async_step_google()
                errors[CONF_REGION] = "region_required"
            else:
                return await self.async_step_password()

        data_schema = _user_schema(self._auth_method, self._region) if errors else STEP_USER_SCHEMA
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_password(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            auth = LymowAuth(session)
            try:
                if self._region == REGION_AUTO:
                    tokens = await auth.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                else:
                    tokens = await auth.login_region(
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD],
                        self._region,
                    )
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_AUTH_METHOD: AUTH_METHOD_PASSWORD,
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_REGION: tokens["region"],
                        "refresh_token": tokens["RefreshToken"],
                    },
                )

        return self.async_show_form(step_id="password", data_schema=STEP_PASSWORD_SCHEMA, errors=errors)

    async def async_step_google(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._prepare_oauth()
        self._register_oauth_view()
        auth_url = self._oauth_start_url()
        errors: dict[str, str] = {}

        if user_input is not None:
            code, error = self._parse_oauth_result(
                user_input.get(OAUTH_RESULT, ""),
                user_input.get(OAUTH_STATE, ""),
            )
            if error:
                errors["base"] = error
            else:
                result = await self._async_complete_google(code)
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    return result

        return self.async_show_form(
            step_id="google",
            data_schema=_google_schema(auth_url),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        entry_id = self.context.get("entry_id")
        self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        self._region = entry_data.get(CONF_REGION, REGION_AUTO)
        self._auth_method = entry_data.get(CONF_AUTH_METHOD, AUTH_METHOD_PASSWORD)
        if self._auth_method == AUTH_METHOD_GOOGLE:
            if self._region == REGION_AUTO:
                return self.async_abort(reason="region_required")
            return await self.async_step_google()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        errors: dict[str, str] = {}
        if user_input is not None:
            username = self._reauth_entry.data[CONF_USERNAME]
            auth = LymowAuth(async_get_clientsession(self.hass))
            try:
                if self._region == REGION_AUTO:
                    tokens = await auth.login(username, user_input[CONF_PASSWORD])
                else:
                    tokens = await auth.login_region(username, user_input[CONF_PASSWORD], self._region)
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_auth"
            else:
                data = {
                    **self._reauth_entry.data,
                    CONF_AUTH_METHOD: AUTH_METHOD_PASSWORD,
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_REGION: tokens["region"],
                    "refresh_token": tokens["RefreshToken"],
                }
                return await self._async_update_reauth_entry(data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={"username": self._reauth_entry.data[CONF_USERNAME]},
        )

    async def _async_complete_google(self, code: str) -> ConfigFlowResult | str:
        if self._pkce_verifier is None:
            raise RuntimeError("OAuth PKCE verifier is missing")

        session = async_get_clientsession(self.hass)
        auth = LymowAuth(session)
        try:
            tokens = await auth.exchange_oauth_code(
                region=self._region,
                code=code,
                redirect_uri=OAUTH_REDIRECT_URI,
                code_verifier=self._pkce_verifier,
            )
            refresh_token = tokens.get("RefreshToken")
            if not isinstance(refresh_token, str) or not refresh_token:
                return "missing_refresh_token"
            creds = await auth.get_aws_credentials(tokens["IdToken"], self._region)
            identity_id = creds.get("identity_id")
            if not isinstance(identity_id, str) or not identity_id:
                return "invalid_oauth_code"
            client = LymowApiClient(
                session=session,
                access_token=tokens["AccessToken"],
                region=self._region,
                identity_id=identity_id,
            )
            devices = await client.get_devices()
        except LymowAuthError:
            return "invalid_oauth_code"
        except (aiohttp.ClientError, TimeoutError):
            return "cannot_connect"
        except (KeyError, TypeError, ValueError):
            return "invalid_oauth_code"

        if not isinstance(devices, list) or not all(
            isinstance(device, dict) and isinstance(device.get("deviceThingName"), str) for device in devices
        ):
            return "invalid_oauth_code"
        if not devices:
            return self.async_abort(reason="no_devices")

        data = {
            CONF_AUTH_METHOD: AUTH_METHOD_GOOGLE,
            CONF_REGION: self._region,
            "refresh_token": refresh_token,
        }
        if self._reauth_entry is not None:
            return await self._async_update_reauth_entry({**self._reauth_entry.data, **data})

        await self.async_set_unique_id(identity_id)
        self._abort_if_unique_id_configured()
        email = _jwt_claim(tokens["IdToken"], "email")
        title = email if isinstance(email, str) and email else f"Lymow Google ({self._region})"
        return self.async_create_entry(title=title, data=data)

    async def _async_update_reauth_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        if self._reauth_entry is None:
            raise RuntimeError("Reauthentication entry is missing")
        return self.async_update_reload_and_abort(
            self._reauth_entry,
            data_updates=data,
            reason="reauth_successful",
        )

    def _prepare_oauth(self) -> None:
        if self._oauth_state is not None:
            return
        self._oauth_state = secrets.token_urlsafe(32)
        self._pkce_verifier = secrets.token_urlsafe(64)
        self._pkce_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self._pkce_verifier.encode()).digest()).rstrip(b"=").decode()
        )

    def _parse_oauth_result(self, raw_result: str, supplied_state: str) -> tuple[str, str | None]:
        raw_result = raw_result.strip()
        code = raw_result
        returned_state = supplied_state.strip()
        if raw_result.startswith("myapp://"):
            parsed = urlparse(raw_result)
            if parsed.scheme != "myapp" or parsed.netloc != "callback":
                return "", "oauth_code_missing"
            params = parse_qs(parsed.query)
            code = params.get("code", [""])[0]
            returned_state = params.get("state", [""])[0]

        if not code or len(code) > 4096:
            return "", "oauth_code_missing"
        if (
            self._oauth_state is None
            or not returned_state
            or not hmac.compare_digest(returned_state, self._oauth_state)
        ):
            return "", "oauth_state_mismatch"
        return code, None

    def _register_oauth_view(self) -> None:
        if self.hass.data.get(_OAUTH_VIEW_REGISTERED_KEY):
            return
        self.hass.http.register_view(LymowOAuthStartView)
        self.hass.data[_OAUTH_VIEW_REGISTERED_KEY] = True

    def _oauth_start_url(self) -> str:
        if self._oauth_state is None or self._pkce_challenge is None:
            raise RuntimeError("OAuth state is missing")
        base_url = self.hass.config.internal_url or "http://homeassistant.local:8123"
        query = urlencode(
            {
                "region": self._region,
                "state": self._oauth_state,
                "code_challenge": self._pkce_challenge,
            }
        )
        return f"{base_url.rstrip('/')}{LymowOAuthStartView.url}?{query}"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LymowOptionsFlow:
        return LymowOptionsFlow()


class LymowOAuthStartView(HomeAssistantView):
    """Show instructions for starting Cognito Google sign-in."""

    url = "/api/lymow/oauth/start"
    name = "api:lymow:oauth:start"
    requires_auth = False

    async def get(self, request: web.Request) -> web.StreamResponse:
        region = request.query.get("region", "")
        state = request.query.get("state", "")
        code_challenge = request.query.get("code_challenge", "")
        if (
            region not in COGNITO_DOMAINS
            or not _is_urlsafe_token(state, 32, 128)
            or not _is_urlsafe_token(code_challenge, 43, 128)
        ):
            raise web.HTTPBadRequest(text="Invalid OAuth start request")

        hass = request.app["hass"]
        auth = LymowAuth(async_get_clientsession(hass))
        authorize_url = auth.get_oauth_authorize_url(
            region=region,
            redirect_uri=OAUTH_REDIRECT_URI,
            state=state,
            code_challenge=code_challenge,
        )
        safe_authorize_url = html.escape(authorize_url, quote=True)
        page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connect Lymow with Google</title>
  <style>
    body {{ background:#111; color:#eee; font:16px system-ui,sans-serif; margin:0; }}
    main {{ max-width:720px; margin:48px auto; padding:32px; }}
    h1 {{ margin-top:0; }}
    li {{ margin:14px 0; line-height:1.5; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:12px; margin:24px 0; }}
    a {{ display:inline-block; color:#fff; font-weight:700; padding:14px 20px;
         border:2px solid #03a9d9; border-radius:8px; text-decoration:none; }}
    .primary {{ background:#03a9d9; }}
    code {{ overflow-wrap:anywhere; }}
  </style>
</head>
<body>
  <main>
    <h1>Connect Lymow with Google</h1>
    <ol>
      <li>Select <strong>Sign in with Google</strong> and finish signing in in the new tab.
          Close that tab after it reaches the <code>myapp://callback/</code> address.</li>
      <li>On this page, open your browser's DevTools, select the Network panel, and
          enable <strong>Preserve log</strong>.</li>
      <li>Select <strong>Get authorization code</strong>. This page will navigate to
          Google while DevTools remains open.</li>
      <li>In the Network panel, find the request containing <code>callback</code> and
          copy its complete URL.</li>
      <li>Return to Home Assistant, paste that URL into the authorization field, and
          submit it promptly.</li>
    </ol>
    <div class="actions">
      <a href="{safe_authorize_url}" target="_blank" rel="noopener noreferrer">Sign in with Google</a>
      <a class="primary" href="{safe_authorize_url}">Get authorization code</a>
    </div>
  </main>
</body>
</html>"""
        return web.Response(
            text=page,
            content_type="text/html",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )


class LymowOptionsFlow(OptionsFlow):
    """Options: the robot's BLE MAC for local manual drive and the camera RTSP path/port."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_BLE_ADDRESS: user_input.get(CONF_BLE_ADDRESS, "").strip(),
                    CONF_RTSP_PATH: normalize_rtsp_path(user_input.get(CONF_RTSP_PATH)),
                    CONF_RTSP_PORT: user_input.get(CONF_RTSP_PORT, RTSP_PORT),
                }
            )

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(CONF_BLE_ADDRESS, default=options.get(CONF_BLE_ADDRESS, "")): str,
                vol.Optional(CONF_RTSP_PATH, default=options.get(CONF_RTSP_PATH, "")): str,
                vol.Optional(CONF_RTSP_PORT, default=options.get(CONF_RTSP_PORT, RTSP_PORT)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _is_urlsafe_token(value: str, minimum: int, maximum: int) -> bool:
    allowed = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
    return minimum <= len(value) <= maximum and all(character in allowed for character in value)


def _jwt_claim(token: str, claim: str) -> Any:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (binascii.Error, IndexError, UnicodeDecodeError, ValueError):
        return None
    return claims.get(claim) if isinstance(claims, dict) else None
