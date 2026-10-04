"""Config flow for Lymow integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .auth import LymowAuth
from .const import (
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

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_REGION, default=REGION_AUTO): SelectSelector(
            SelectSelectorConfig(
                options=REGION_CHOICES,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="region",
            )
        ),
    }
)


class LymowConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            auth = LymowAuth(session)
            region_override = user_input.get(CONF_REGION, REGION_AUTO)
            try:
                if region_override == REGION_AUTO:
                    tokens = await auth.login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                else:
                    tokens = await auth.login_region(
                        user_input[CONF_USERNAME],
                        user_input[CONF_PASSWORD],
                        region_override,
                    )
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
                        CONF_REGION: tokens["region"],
                        "refresh_token": tokens["RefreshToken"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> LymowOptionsFlow:
        return LymowOptionsFlow()


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
