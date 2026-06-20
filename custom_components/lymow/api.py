"""Lymow REST API client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

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


def _kvs_sigv4_headers(
    method: str,
    host: str,
    uri: str,
    body: bytes,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str,
    service: str = "kinesisvideo",
) -> dict[str, str]:
    """Build SigV4 request headers for a kinesisvideo (or other AWS) JSON POST.

    Uses the temporary credentials returned by /prod/kvs/cmd. Unlike the S3
    helper this signs over a JSON body and does not send x-amz-content-sha256.
    """
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_str = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    signed_headers = "host;x-amz-date;x-amz-security-token"
    canonical = (
        f"{method}\n{uri}\n\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )
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
        "x-amz-security-token": session_token,
        "Content-Type": "application/json",
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

    async def get_signaling_channel_endpoint(
        self, channel_arn: str, creds: dict[str, str], *, role: str = "VIEWER", region: str | None = None
    ) -> dict[str, str]:
        """Resolve a KVS signaling channel's endpoints (SigV4-signed, KVS temp creds).

        Returns ``{"WSS": "wss://…", "HTTPS": "https://…"}`` from the
        ResourceEndpointList. ``creds`` is the ``credentials`` object from
        :meth:`start_video_session` (accessKeyId/secretAccessKey/sessionToken).
        """
        region = region or self._region
        host = f"kinesisvideo.{region}.amazonaws.com"
        payload = json.dumps(
            {
                "ChannelARN": channel_arn,
                "SingleMasterChannelEndpointConfiguration": {"Protocols": ["WSS", "HTTPS"], "Role": role},
            }
        ).encode()
        headers = _kvs_sigv4_headers(
            "POST",
            host,
            "/getSignalingChannelEndpoint",
            payload,
            region,
            creds["accessKeyId"],
            creds["secretAccessKey"],
            creds["sessionToken"],
        )
        async with self._session.post(
            f"https://{host}/getSignalingChannelEndpoint", headers=headers, data=payload
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        endpoints = {}
        items = data.get("ResourceEndpointList", []) if isinstance(data, dict) else []
        for ep in items if isinstance(items, list) else []:
            if isinstance(ep, dict) and ep.get("Protocol") and ep.get("ResourceEndpoint"):
                endpoints[ep["Protocol"]] = ep["ResourceEndpoint"]
        return endpoints

    async def get_ice_server_config(
        self, channel_arn: str, https_endpoint: str, creds: dict[str, str], *, region: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch TURN/STUN servers for the channel (SigV4-signed, KVS temp creds).

        ``https_endpoint`` is the HTTPS entry from
        :meth:`get_signaling_channel_endpoint`. Returns the IceServerList.
        """
        region = region or self._region
        host = urlparse(https_endpoint).netloc
        payload = json.dumps({"ChannelARN": channel_arn}).encode()
        headers = _kvs_sigv4_headers(
            "POST",
            host,
            "/v1/get-ice-server-config",
            payload,
            region,
            creds["accessKeyId"],
            creds["secretAccessKey"],
            creds["sessionToken"],
        )
        async with self._session.post(
            f"{https_endpoint}/v1/get-ice-server-config", headers=headers, data=payload
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        servers = data.get("IceServerList", []) if isinstance(data, dict) else []
        return servers if isinstance(servers, list) else []

    def presign_signaling_url(
        self,
        wss_endpoint: str,
        channel_arn: str,
        client_id: str,
        creds: dict[str, str],
        *,
        role: str = "VIEWER",
        region: str | None = None,
        expires: int = 299,
    ) -> str:
        """SigV4-presign the KVS signaling WebSocket connect URL.

        The query carries X-Amz-ChannelARN plus, for a VIEWER, X-Amz-ClientId
        alongside the standard SigV4 params; unlike the IoT MQTT presign the
        session token is part of the *signed* canonical query string. A MASTER
        connects without a client id. ``creds`` is the ``credentials`` object
        from :meth:`start_video_session`.
        """
        if role not in {"VIEWER", "MASTER"}:
            raise ValueError("role must be VIEWER or MASTER")

        parsed_endpoint = urlparse(wss_endpoint)
        region = region or self._region
        host = parsed_endpoint.netloc
        path = parsed_endpoint.path or "/"
        now = datetime.now(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_str = now.strftime("%Y%m%d")
        scope = f"{date_str}/{region}/kinesisvideo/aws4_request"
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-ChannelARN": channel_arn,
            "X-Amz-Credential": f"{creds['accessKeyId']}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-Security-Token": creds["sessionToken"],
            "X-Amz-SignedHeaders": "host",
        }
        if role == "VIEWER":
            query["X-Amz-ClientId"] = client_id
        canonical_qs = "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(query.items()))
        canonical = f"GET\n{path}\n{canonical_qs}\nhost:{host}\n\nhost\n{hashlib.sha256(b'').hexdigest()}"
        sts = f"AWS4-HMAC-SHA256\n{amz_date}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}"
        k = _hmac_sha256(("AWS4" + creds["secretAccessKey"]).encode(), date_str)
        k = _hmac_sha256(k, region)
        k = _hmac_sha256(k, "kinesisvideo")
        k = _hmac_sha256(k, "aws4_request")
        signature = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
        return urlunparse(
            (
                parsed_endpoint.scheme,
                parsed_endpoint.netloc,
                path,
                parsed_endpoint.params,
                f"{canonical_qs}&X-Amz-Signature={signature}",
                parsed_endpoint.fragment,
            )
        )

    def viewer_client_id(self) -> str:
        """Build a unique KVS viewer client id carrying the account's Cognito sub.

        The robot's master answers a viewer whose id ends in ``_userId_<sub>``
        (the app uses the same shape); the random prefix keeps concurrent HA
        viewers distinct so they don't collide on the signaling channel.
        """
        sub = ""
        try:
            payload = self._access_token.split(".")[1]
            payload += "=" * ((4 - len(payload) % 4) % 4)
            sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub", "")
        except (ValueError, IndexError, KeyError):
            sub = ""
        return f"ha-lymow_{secrets.token_hex(4)}_userId_{sub}"

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

    async def _post_map(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST to the map gateway and resiliently parse the JSON reply."""
        url = _api_url(self._region, "api_map", path)
        async with self._session.post(url, headers=self._headers, json=body) as resp:
            resp.raise_for_status()
            try:
                result = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                return {}
            return result if isinstance(result, dict) else {}

    async def rename_device(self, thing_name: str, device_name: str) -> dict[str, Any]:
        """Set the robot's cloud display name (PATCH /prod/device-update)."""
        url = _api_url(self._region, "api_device_list", "/prod/device-update")
        body = {"deviceThingName": thing_name, "deviceName": device_name}
        async with self._session.patch(url, headers=self._headers, json=body) as resp:
            resp.raise_for_status()
            try:
                result = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                return {}
            return result if isinstance(result, dict) else {}

    async def restore_backup_map(self, thing_name: str, from_key: str) -> dict[str, Any]:
        """Restore a saved backup map onto the device (POST /prod/restore-map-v2)."""
        return await self._post_map("/prod/restore-map-v2", {"fromKey": from_key, "toThingName": thing_name})

    async def delete_backup_map(self, object_key: str) -> dict[str, Any]:
        """Delete a saved backup map (POST /prod/delete-backup-map)."""
        return await self._post_map("/prod/delete-backup-map", {"objectKey": object_key})

    async def rename_backup_map(self, object_key: str, name: str) -> dict[str, Any]:
        """Rename a saved backup map (POST /prod/update-backup-map-metadata)."""
        return await self._post_map("/prod/update-backup-map-metadata", {"objectKey": object_key, "name": name})

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

    async def get_backup_map_download_url(self, thing_name: str, object_key: str) -> str | None:
        """Return a short-lived presigned S3 URL for a backup map via /prod/get-s3-object.

        The Cognito identity role has no direct s3:GetObject on backup objects, so the
        backend issues a presigned URL (valid ~10 min) instead.
        """
        url = _api_url(self._region, "api_map", "/prod/get-s3-object")
        params = {"deviceThingName": thing_name, "objectKey": object_key}
        async with self._session.get(url, headers=self._headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        if isinstance(data, dict):
            download_url = data.get("downloadUrl")
            if isinstance(download_url, str) and download_url:
                return download_url
        return None

    async def download_backup_map(self, thing_name: str, object_key: str) -> bytes | None:
        """Download a backup map's raw protobuf via a backend-issued presigned URL."""
        download_url = await self.get_backup_map_download_url(thing_name, object_key)
        if not download_url:
            return None
        async with self._session.get(download_url) as resp:
            resp.raise_for_status()
            return await resp.read()
