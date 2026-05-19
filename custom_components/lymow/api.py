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

    async def update_device_feature(self, thing_name: str, **fields: Any) -> dict[str, Any]:
        """PATCH /prod/update-device-feature with arbitrary feature fields.

        Known fields: theftDetectionSwitch, theftLock, findRobotSwitch,
        mobileNotificationSwitch, geoFence.

        The explicit ``thing_name`` argument always wins — any
        ``deviceThingName`` key passed via ``fields`` is silently dropped
        so a caller can't accidentally PATCH a different device.
        """
        url = _api_url(self._region, "api_device_info", "/prod/update-device-feature")
        body = {**{k: v for k, v in fields.items() if k != "deviceThingName"}, "deviceThingName": thing_name}
        async with self._session.patch(url, headers=self._headers, json=body) as resp:
            resp.raise_for_status()
            try:
                result = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                return {}
            return result if isinstance(result, dict) else {}

    async def start_video_session(self, thing_name: str) -> dict[str, Any]:
        """POST /prod/kvs/cmd with action="start".

        Response:
            {"credentials": {"accessKeyId", "secretAccessKey",
                             "sessionToken", "expiration"},
             "channelARN": "arn:aws:kinesisvideo:<region>:<acct>:channel/<thing>_stream_channel/<ts>",
             "region": "<region>",
             "deviceThingName": "<thing>"}

        Credentials expire in ~15 minutes; callers must complete the
        WebRTC handshake within that window.
        """
        if not REGION_CONFIG[self._region].get("api_kvs"):
            raise NotImplementedError(f"Kinesis Video gateway not configured for region {self._region!r}")
        url = _api_url(self._region, "api_kvs", "/prod/kvs/cmd")
        body = {"deviceThingName": thing_name, "action": "start"}
        async with self._session.post(url, headers=self._headers, json=body) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def check_update(self, thing_name: str) -> dict[str, Any]:
        """GET /prod/check-update — latest firmware metadata for one device.

        Real response (confirmed eu-west-1 capture 2026-05-19):
            {"latestVersion": "v2.1.48_20260518",
             "prefix": "",
             "releaseNote": "...\\n..."}

        objectKey for the install endpoint is built as ``prefix + latestVersion``.
        """
        url = _api_url(self._region, "api_ota_check", "/prod/check-update")
        params = {"deviceThingName": thing_name}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def create_ota_job(self, thing_name: str, object_key: str) -> dict[str, Any]:
        """GET /prod/create-ota-job — start an OTA install for the given object key.

        ``object_key`` is ``prefix + latestVersion`` from check_update.
        Returns ``{"jobId": "<id>", ...}`` on success. The robot rejects
        with ``OTA_ROBOT_NOT_IN_WAIT`` when actively mowing.
        """
        url = _api_url(self._region, "api_ota_job", "/prod/create-ota-job")
        params = {"deviceThingName": thing_name, "objectKey": object_key}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_ota_job_summary(self, thing_name: str, job_id: str) -> dict[str, Any]:
        """GET /prod/get-ota-job-summary — poll the status of an OTA job."""
        url = _api_url(self._region, "api_ota_job", "/prod/get-ota-job-summary")
        params = {"deviceThingName": thing_name, "jobId": job_id}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_clean_history(self, thing_name: str, page: int = 1, page_size: int = 10) -> Any:
        url = _api_url(self._region, "api_map", "/prod/get-clean-history-collect")
        params = {"deviceThingName": thing_name, "page": page, "pageSize": page_size}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def get_backup_map_list(self, thing_name: str) -> list[dict]:
        """Return the full backup-map list (newest-first) from /get-backup-map.

        Each entry has at minimum ``map_file`` (S3 key), ``backup_time`` (Unix
        epoch seconds), and ``name`` (often empty). Returns an empty list for
        any shape we don't recognise so callers don't have to defend against
        non-dict envelopes or non-list ``mapList`` values.
        """
        url = _api_url(self._region, "api_map", "/prod/get-backup-map")
        params = {"deviceThingName": thing_name}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            return []
        items = data.get("mapList")
        if not isinstance(items, list):
            return []
        return [entry for entry in items if isinstance(entry, dict)]

    async def get_backup_map_key(self, thing_name: str) -> str | None:
        """Return the S3 object key for the most recent saved map, or None if none exists.

        Real response (confirmed eu-west-1 capture 2026-05-19):
            {"mapList": [
                {"map_file": "device_<mac>/map/map_<ts>.pb",
                 "name": "",
                 "backup_time": 1778768592},
                ...
            ]}
        Entries are returned newest-first; backup_time is a Unix epoch.
        """
        url = _api_url(self._region, "api_map", "/prod/get-backup-map")
        params = {"deviceThingName": thing_name}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        map_list = data.get("mapList") or []
        if not map_list:
            return None
        # mapList is newest-first per the captured response — take entry[0].
        # Older guesses (key/backupMapUrl/mapKey/url) are kept as fallbacks in
        # case different regions or app versions use a different field name.
        entry = map_list[0]
        for field in ("map_file", "key", "backupMapUrl", "mapKey", "url"):
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
