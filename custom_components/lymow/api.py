"""Lymow REST API client."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import REGION_CONFIG


def _api_url(region: str, gateway_key: str, path: str) -> str:
    gw_id = REGION_CONFIG[region][gateway_key]
    assert isinstance(gw_id, str)
    return f"https://{gw_id}.execute-api.{region}.amazonaws.com{path}"


def _hmac_sha256(key: bytes, data: str) -> bytes:
    return hmac.new(key, data.encode(), hashlib.sha256).digest()


def _s3_sigv4_headers(
    region: str,
    bucket: str,
    key: str,
    access_key: str,
    secret_key: str,
    session_token: str,
) -> dict[str, str]:
    """Build SigV4 request headers for a GET on an S3 object."""
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_str = now.strftime("%Y%m%d")
    service = "s3"
    host = f"{bucket}.s3.{region}.amazonaws.com"
    uri = f"/{quote(key, safe='/')}"
    payload_hash = hashlib.sha256(b"").hexdigest()

    canonical = (
        f"GET\n{uri}\n\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n\n"
        f"host;x-amz-content-sha256;x-amz-date;x-amz-security-token\n"
        f"{payload_hash}"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date;x-amz-security-token"
    scope = f"{date_str}/{region}/{service}/aws4_request"
    sts = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"

    k = _hmac_sha256(("AWS4" + secret_key).encode(), date_str)
    k = _hmac_sha256(k, region)
    k = _hmac_sha256(k, service)
    k = _hmac_sha256(k, "aws4_request")
    signature = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()

    auth = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    return {
        "Authorization": auth,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "x-amz-security-token": session_token,
    }


class LymowApiClient:
    def __init__(self, session: aiohttp.ClientSession, access_token: str, region: str, identity_id: str) -> None:
        self._session = session
        self._access_token = access_token
        self._region = region
        self._identity_id = identity_id

        # Temporary AWS credentials — set via update_aws_credentials() after Cognito identity exchange
        self._aws_access_key: str = ""
        self._aws_secret_key: str = ""
        self._aws_session_token: str = ""

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._access_token}

    def update_tokens(self, access_token: str) -> None:
        self._access_token = access_token

    def update_aws_credentials(self, access_key: str, secret_key: str, session_token: str) -> None:
        self._aws_access_key = access_key
        self._aws_secret_key = secret_key
        self._aws_session_token = session_token

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

    async def get_clean_history(self, thing_name: str, page: int = 1, page_size: int = 10) -> Any:
        url = _api_url(self._region, "api_map", "/prod/get-clean-history-collect")
        params = {"deviceThingName": thing_name, "page": page, "pageSize": page_size}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_backup_map_key(self, thing_name: str) -> str | None:
        """Return the S3 object key for the most recent saved map, or None if none exists.

        Response format: {"mapList": [{"key": "<s3-key>", ...}, ...]}
        The list is empty when no map has been saved yet.
        """
        url = _api_url(self._region, "api_map", "/prod/get-backup-map")
        params = {"deviceThingName": thing_name}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        map_list = data.get("mapList") or []
        if not map_list:
            return None
        # Most recent map is the last entry; try common key field names
        entry = map_list[-1]
        for field in ("key", "backupMapUrl", "mapKey", "url"):
            if field in entry:
                return entry[field]
        return None

    async def download_map_bytes(self, s3_key: str) -> bytes:
        """Download raw protobuf map bytes from S3 using SigV4-signed credentials."""
        bucket = REGION_CONFIG[self._region]["s3_bucket"]
        if bucket is None:
            raise NotImplementedError(f"S3 bucket not yet confirmed for region {self._region!r}")
        headers = _s3_sigv4_headers(
            region=self._region,
            bucket=bucket,
            key=s3_key,
            access_key=self._aws_access_key,
            secret_key=self._aws_secret_key,
            session_token=self._aws_session_token,
        )
        url = f"https://{bucket}.s3.{self._region}.amazonaws.com/{quote(s3_key, safe='/')}"
        async with self._session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.read()
