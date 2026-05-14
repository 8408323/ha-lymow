"""Tests for Lymow API client."""

from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import aioresponses
from lymow.api import LymowApiClient
from lymow.const import REGION_CONFIG

REGION = "eu-west-1"
GW_DEVICE_LIST = REGION_CONFIG[REGION]["api_device_list"]
GW_DEVICE_INFO = REGION_CONFIG[REGION]["api_device_info"]
# Use regex patterns so query-string encoding differences don't matter.
RE_LIST = re.compile(r"https://" + re.escape(GW_DEVICE_LIST) + r"\.execute-api\..+/prod/device-list-query")
RE_INFO = re.compile(r"https://" + re.escape(GW_DEVICE_INFO) + r"\.execute-api\..+/prod/get-device-info")
RE_FEATURE = re.compile(r"https://" + re.escape(GW_DEVICE_INFO) + r"\.execute-api\..+/prod/get-device-feature")


@pytest.fixture
async def client():
    async with aiohttp.ClientSession() as session:
        yield LymowApiClient(
            session=session,
            access_token="test-access-token",
            region=REGION,
            identity_id="test-identity-id",
        )


class TestGetDevices:
    async def test_returns_device_list(self, client):
        payload = [{"thingName": "mower-001", "deviceName": "My Mower"}]

        with aioresponses() as m:
            m.get(RE_LIST, payload=payload)
            devices = await client.get_devices()

        assert devices == payload

    async def test_sends_auth_header(self, client):
        with aioresponses() as m:
            m.get(RE_LIST, payload=[])
            await client.get_devices()
            request = list(m.requests.values())[0][0]

        assert request.kwargs["headers"]["Authorization"] == "test-access-token"

    async def test_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.get(RE_LIST, status=401)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.get_devices()


class TestGetDeviceInfo:
    async def test_returns_device_info(self, client):
        payload = {"thingName": "mower-001", "battery": 85, "status": "docked"}

        with aioresponses() as m:
            m.get(RE_INFO, payload=payload)
            info = await client.get_device_info("mower-001")

        assert info == payload

    async def test_token_update(self, client):
        client.update_tokens("new-access-token")

        with aioresponses() as m:
            m.get(RE_LIST, payload=[])
            await client.get_devices()
            request = list(m.requests.values())[0][0]

        assert request.kwargs["headers"]["Authorization"] == "new-access-token"


class TestGetDeviceFeature:
    async def test_returns_feature_data(self, client):
        payload = {"thingName": "mower-001", "featureVersion": "1.0", "features": []}

        with aioresponses() as m:
            m.get(RE_FEATURE, payload=payload)
            feature = await client.get_device_feature("mower-001")

        assert feature == payload

    async def test_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.get(RE_FEATURE, status=403)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.get_device_feature("mower-001")
