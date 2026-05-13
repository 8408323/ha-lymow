"""Lymow REST API client."""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import REGION_CONFIG


def _api_url(region: str, gateway_key: str, path: str) -> str:
    gw_id = REGION_CONFIG[region][gateway_key]
    assert isinstance(gw_id, str)
    return f"https://{gw_id}.execute-api.{region}.amazonaws.com{path}"


class LymowApiClient:
    def __init__(self, session: aiohttp.ClientSession, access_token: str, region: str, identity_id: str) -> None:
        self._session = session
        self._access_token = access_token
        self._region = region
        self._identity_id = identity_id

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._access_token}

    def update_tokens(self, access_token: str) -> None:
        self._access_token = access_token

    async def get_devices(self) -> list[dict[str, Any]]:
        url = _api_url(self._region, "api_device_list", "/prod/device-list-query")
        params = {"p": "devices", "identityId": self._identity_id}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_device_info(self, thing_name: str) -> dict[str, Any]:
        url = _api_url(self._region, "api_device_info", "/prod/get-device-info")
        params = {"deviceThingName": thing_name}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_device_feature(self, thing_name: str) -> dict[str, Any]:
        url = _api_url(self._region, "api_device_info", "/prod/get-device-feature")
        params = {"deviceThingName": thing_name}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)
