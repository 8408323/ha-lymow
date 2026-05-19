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
RE_UPDATE_FEATURE = re.compile(
    r"https://" + re.escape(GW_DEVICE_INFO) + r"\.execute-api\..+/prod/update-device-feature"
)

GW_KVS = REGION_CONFIG[REGION]["api_kvs"]
RE_KVS_CMD = re.compile(r"https://" + re.escape(GW_KVS) + r"\.execute-api\..+/prod/kvs/cmd")


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


class TestUpdateDeviceFeature:
    async def test_sends_patch_with_fields(self, client):
        with aioresponses() as m:
            m.patch(RE_UPDATE_FEATURE, payload={"ok": True})
            result = await client.update_device_feature(
                "mower-001",
                theftDetectionSwitch=True,
                findRobotSwitch=False,
            )

            request = list(m.requests.values())[0][0]
            sent_body = request.kwargs["json"]

        assert result == {"ok": True}
        assert sent_body == {
            "deviceThingName": "mower-001",
            "theftDetectionSwitch": True,
            "findRobotSwitch": False,
        }
        assert request.kwargs["headers"]["Authorization"] == "test-access-token"

    async def test_returns_empty_dict_on_non_json_response(self, client):
        with aioresponses() as m:
            m.patch(RE_UPDATE_FEATURE, body="", status=204, content_type="text/plain")
            result = await client.update_device_feature("mower-001", theftLock=True)

        assert result == {}

    async def test_returns_empty_dict_on_malformed_json(self, client):
        with aioresponses() as m:
            m.patch(RE_UPDATE_FEATURE, body="not-json", status=200, content_type="text/plain")
            result = await client.update_device_feature("mower-001", theftLock=True)

        assert result == {}

    async def test_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.patch(RE_UPDATE_FEATURE, status=400)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.update_device_feature("mower-001", theftLock=True)

    async def test_explicit_thing_name_overrides_fields_key(self, client):
        """A caller cannot poison the request by passing deviceThingName in **fields."""
        with aioresponses() as m:
            m.patch(RE_UPDATE_FEATURE, payload={})
            await client.update_device_feature(
                "mower-001",
                deviceThingName="mower-evil",  # type: ignore[arg-type]
                theftLock=True,
            )
            request = list(m.requests.values())[0][0]
            sent_body = request.kwargs["json"]
        assert sent_body["deviceThingName"] == "mower-001"
        assert sent_body["theftLock"] is True


RE_HISTORY = re.compile(
    r"https://" + re.escape(REGION_CONFIG[REGION]["api_map"]) + r"\.execute-api\..+/prod/get-clean-history-collect"
)
RE_BACKUP_MAP = re.compile(
    r"https://" + re.escape(REGION_CONFIG[REGION]["api_map"]) + r"\.execute-api\..+/prod/get-backup-map"
)


class TestGetCleanHistory:
    async def test_returns_history(self, client):
        payload = [{"date": "2026-05-14", "duration": 3600}]

        with aioresponses() as m:
            m.get(RE_HISTORY, payload=payload)
            result = await client.get_clean_history("mower-001")

        assert result == payload

    async def test_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.get(RE_HISTORY, status=500)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.get_clean_history("mower-001")


class TestGetBackupMapKey:
    async def test_returns_map_file_from_real_response(self, client):
        """Real response shape (captured 2026-05-19, eu-west-1):
        {"mapList": [{"map_file": "...", "name": "", "backup_time": <epoch>}, ...]}.
        Entries are newest-first.
        """
        payload = {
            "mapList": [
                {
                    "map_file": "device_7890838300cd/map/map_20260514T142312Z.pb",
                    "name": "",
                    "backup_time": 1778768592,
                },
                {
                    "map_file": "device_7890838300cd/map/map_20260514T110146Z.pb",
                    "name": "",
                    "backup_time": 1778756506,
                },
            ]
        }

        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=payload)
            result = await client.get_backup_map_key("mower-001")

        # Newest entry (entry[0]) is returned
        assert result == "device_7890838300cd/map/map_20260514T142312Z.pb"

    async def test_returns_none_when_list_empty(self, client):
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload={"mapList": []})
            result = await client.get_backup_map_key("mower-001")

        assert result is None

    async def test_returns_none_when_map_list_missing(self, client):
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload={})
            result = await client.get_backup_map_key("mower-001")

        assert result is None

    async def test_falls_back_to_alternative_key_fields(self, client):
        """If a future app version drops back to one of the older guesses, we
        still recover the key."""
        payload = {"mapList": [{"backupMapUrl": "maps/device_001/backup.pb"}]}

        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=payload)
            result = await client.get_backup_map_key("mower-001")

        assert result == "maps/device_001/backup.pb"

    async def test_returns_none_when_no_recognised_field(self, client):
        payload = {"mapList": [{"unknownField": "value"}]}

        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=payload)
            result = await client.get_backup_map_key("mower-001")

        assert result is None

    async def test_map_file_wins_over_legacy_field(self, client):
        """When both map_file and a legacy field are present, map_file is preferred."""
        payload = {
            "mapList": [
                {
                    "map_file": "device_7890838300cd/map/new.pb",
                    "key": "legacy.pb",
                }
            ]
        }

        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=payload)
            result = await client.get_backup_map_key("mower-001")

        assert result == "device_7890838300cd/map/new.pb"


class TestGetBackupMapList:
    async def test_returns_full_list_newest_first(self, client):
        payload = {
            "mapList": [
                {"map_file": "a.pb", "name": "", "backup_time": 200},
                {"map_file": "b.pb", "name": "", "backup_time": 100},
            ]
        }
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=payload)
            result = await client.get_backup_map_list("mower-001")
        assert len(result) == 2
        assert result[0]["map_file"] == "a.pb"
        assert result[1]["backup_time"] == 100

    async def test_returns_empty_list_when_missing(self, client):
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload={})
            result = await client.get_backup_map_list("mower-001")
        assert result == []

    async def test_drops_non_dict_entries(self, client):
        payload = {"mapList": [{"map_file": "a.pb"}, "garbage", None]}
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=payload)
            result = await client.get_backup_map_list("mower-001")
        assert result == [{"map_file": "a.pb"}]

    async def test_returns_empty_list_when_payload_not_dict(self, client):
        """If the backend ever returns a bare list/string, we shouldn't crash."""
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload=[1, 2, 3])
            result = await client.get_backup_map_list("mower-001")
        assert result == []

    async def test_returns_empty_list_when_map_list_not_list(self, client):
        """`mapList` arriving as a string/dict shouldn't propagate as an AttributeError."""
        with aioresponses() as m:
            m.get(RE_BACKUP_MAP, payload={"mapList": "oops"})
            result = await client.get_backup_map_list("mower-001")
        assert result == []


class TestStartVideoSession:
    async def test_sends_start_action(self, client):
        payload = {
            "credentials": {
                "accessKeyId": "ASIA...",
                "secretAccessKey": "secret",
                "sessionToken": "tok",
                "expiration": "2026-05-19T15:26:31.000Z",
            },
            "channelARN": "arn:aws:kinesisvideo:eu-west-1:863518414241:channel/device_xxxx_stream_channel/1778088062627",
            "region": "eu-west-1",
            "deviceThingName": "mower-001",
        }
        with aioresponses() as m:
            m.post(RE_KVS_CMD, payload=payload)
            result = await client.start_video_session("mower-001")
            request = list(m.requests.values())[0][0]

        assert result == payload
        assert request.kwargs["json"] == {"deviceThingName": "mower-001", "action": "start"}
        assert request.kwargs["headers"]["Authorization"] == "test-access-token"

    async def test_raises_when_gateway_not_configured(self, client):
        """ap-southeast-2 has api_kvs=None in const.py — should raise NotImplementedError."""
        # Re-create the client pointed at a region without a KVS gateway
        async with aiohttp.ClientSession() as session:
            apse2 = LymowApiClient(
                session=session,
                access_token="t",
                region="ap-southeast-2",
                identity_id="i",
            )
            with pytest.raises(NotImplementedError, match="Kinesis Video"):
                await apse2.start_video_session("mower-001")

    async def test_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.post(RE_KVS_CMD, status=500)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.start_video_session("mower-001")


class TestSigV4Helpers:
    def test_s3_sigv4_headers_returns_expected_keys(self):
        from lymow.api import _s3_sigv4_headers

        headers = _s3_sigv4_headers(
            region="eu-west-1",
            bucket="test-bucket",
            key="maps/test.pb",
            access_key="AKIAIOSFODNN7EXAMPLE",
            secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            session_token="test-session-token",
        )

        assert set(headers.keys()) == {"Authorization", "x-amz-date", "x-amz-content-sha256", "x-amz-security-token"}
        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/")
        assert (
            "x-amz-security-token" in headers["Authorization"]
            or headers["x-amz-security-token"] == "test-session-token"
        )

    async def test_update_aws_credentials_used_in_download(self, client):
        """update_aws_credentials stores values that feed into SigV4 signing."""
        client.update_aws_credentials("AK123", "SK456", "TOKEN789")

        # Temporarily set a known bucket so the full HTTP path executes
        original = REGION_CONFIG[REGION]["s3_bucket"]
        REGION_CONFIG[REGION]["s3_bucket"] = "test-bucket"
        try:
            re_s3 = re.compile(r"https://test-bucket\.s3\.eu-west-1\.amazonaws\.com/.*")
            with aioresponses() as m:
                m.get(re_s3, body=b"\x00\x01\x02")
                result = await client.download_map_bytes("maps/test.pb")
            assert result == b"\x00\x01\x02"
        finally:
            REGION_CONFIG[REGION]["s3_bucket"] = original

    async def test_download_map_bytes_raises_when_bucket_not_configured(self, client):
        """download_map_bytes raises NotImplementedError when s3_bucket is None."""
        with pytest.raises(NotImplementedError, match="S3 bucket not yet confirmed"):
            await client.download_map_bytes("maps/test.pb")
