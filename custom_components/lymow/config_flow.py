"""Config flow for Lymow integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import LymowAuth
from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class LymowConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            auth = LymowAuth(session)
            try:
                tokens = await auth.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            except Exception:
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "region": tokens["region"],
                        "refresh_token": tokens["RefreshToken"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
