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

GW_OTA_CHECK = REGION_CONFIG[REGION]["api_ota_check"]
GW_OTA_JOB = REGION_CONFIG[REGION]["api_ota_job"]
RE_OTA_CHECK = re.compile(r"https://" + re.escape(GW_OTA_CHECK) + r"\.execute-api\..+/prod/check-update")
RE_OTA_CREATE = re.compile(r"https://" + re.escape(GW_OTA_JOB) + r"\.execute-api\..+/prod/create-ota-job")
RE_OTA_SUMMARY = re.compile(r"https://" + re.escape(GW_OTA_JOB) + r"\.execute-api\..+/prod/get-ota-job-summary")


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
_GW_MAP = re.escape(REGION_CONFIG[REGION]["api_map"])
RE_RESTORE_MAP = re.compile(r"https://" + _GW_MAP + r"\.execute-api\..+/prod/restore-map-v2")
RE_DELETE_MAP = re.compile(r"https://" + _GW_MAP + r"\.execute-api\..+/prod/delete-backup-map")
RE_RENAME_MAP = re.compile(r"https://" + _GW_MAP + r"\.execute-api\..+/prod/update-backup-map-metadata")


RE_DEVICE_UPDATE = re.compile(
    r"https://" + re.escape(REGION_CONFIG[REGION]["api_device_list"]) + r"\.execute-api\..+/prod/device-update"
)


class TestRenameDevice:
    async def test_patches_name(self, client):
        with aioresponses() as m:
            m.patch(RE_DEVICE_UPDATE, payload={"ok": True})
            result = await client.rename_device("mower-001", "Garden Bot")
            req = list(m.requests.values())[0][0]
        assert result == {"ok": True}
        assert req.kwargs["json"] == {"deviceThingName": "mower-001", "deviceName": "Garden Bot"}

    async def test_non_json_returns_empty(self, client):
        with aioresponses() as m:
            m.patch(RE_DEVICE_UPDATE, body="OK", content_type="text/plain")
            assert await client.rename_device("mower-001", "x") == {}

    async def test_non_dict_returns_empty(self, client):
        with aioresponses() as m:
            m.patch(RE_DEVICE_UPDATE, payload=[1])
            assert await client.rename_device("mower-001", "x") == {}

    async def test_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.patch(RE_DEVICE_UPDATE, status=500)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.rename_device("mower-001", "x")


_CREDS = {"accessKeyId": "AKIATESTKVS", "secretAccessKey": "secretkvs", "sessionToken": "tokkvs"}
RE_KVS_ENDPOINT = re.compile(
    r"https://kinesisvideo\." + re.escape(REGION) + r"\.amazonaws\.com/getSignalingChannelEndpoint"
)
RE_KVS_ICE = re.compile(
    r"https://r-[a-z0-9]+\.kinesisvideo\." + re.escape(REGION) + r"\.amazonaws\.com/v1/get-ice-server-config"
)


class TestKvsWebRTC:
    async def test_signaling_endpoint_returns_protocol_map(self, client):
        client.update_aws_credentials("AK", "SK", "ST")
        payload = {
            "ResourceEndpointList": [
                {"Protocol": "WSS", "ResourceEndpoint": "wss://v-1.kinesisvideo.%s.amazonaws.com" % REGION},
                {"Protocol": "HTTPS", "ResourceEndpoint": "https://r-1.kinesisvideo.%s.amazonaws.com" % REGION},
            ]
        }
        with aioresponses() as m:
            m.post(RE_KVS_ENDPOINT, payload=payload)
            eps = await client.get_signaling_channel_endpoint("arn:test", _CREDS)
            req = list(m.requests.values())[0][0]
        assert eps["WSS"].startswith("wss://") and eps["HTTPS"].startswith("https://")
        assert req.kwargs["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIATESTKVS/")
        assert "x-amz-security-token" in req.kwargs["headers"]

    async def test_signaling_endpoint_skips_malformed_entries(self, client):
        with aioresponses() as m:
            m.post(RE_KVS_ENDPOINT, payload={"ResourceEndpointList": [{"Protocol": "WSS"}, "junk", {}]})
            eps = await client.get_signaling_channel_endpoint("arn:test", _CREDS)
        assert eps == {}

    async def test_signaling_endpoint_non_dict_returns_empty(self, client):
        with aioresponses() as m:
            m.post(RE_KVS_ENDPOINT, payload=[1, 2, 3])
            assert await client.get_signaling_channel_endpoint("arn:test", _CREDS) == {}

    async def test_ice_server_config_returns_list(self, client):
        ice = [{"Uris": ["turn:1.2.3.4:443"], "Username": "u", "Password": "p"}]
        with aioresponses() as m:
            m.post(RE_KVS_ICE, payload={"IceServerList": ice})
            out = await client.get_ice_server_config(
                "arn:test", "https://r-d1.kinesisvideo.%s.amazonaws.com" % REGION, _CREDS
            )
            req = list(m.requests.values())[0][0]
        assert out == ice
        assert req.kwargs["headers"]["Authorization"].startswith("AWS4-HMAC-SHA256")

    async def test_ice_server_config_non_dict_returns_empty(self, client):
        with aioresponses() as m:
            m.post(RE_KVS_ICE, payload=[1, 2, 3])
            assert (
                await client.get_ice_server_config(
                    "arn:test", "https://r-d1.kinesisvideo.%s.amazonaws.com" % REGION, _CREDS
                )
                == []
            )

    async def test_ice_server_config_non_list_returns_empty(self, client):
        with aioresponses() as m:
            m.post(RE_KVS_ICE, payload={"IceServerList": "nope"})
            assert (
                await client.get_ice_server_config(
                    "arn:test", "https://r-d1.kinesisvideo.%s.amazonaws.com" % REGION, _CREDS
                )
                == []
            )

    async def test_signaling_endpoint_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.post(RE_KVS_ENDPOINT, status=403)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.get_signaling_channel_endpoint("arn:test", _CREDS)

    def test_presign_viewer_url_signs_query_with_client_id(self, client):
        wss = "wss://v-1.kinesisvideo.%s.amazonaws.com" % REGION
        url = client.presign_signaling_url(wss, "arn:test:chan", "ha-lymow-123", _CREDS)
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(url)
        q = parse_qs(parsed.query)
        assert parsed.scheme == "wss" and parsed.netloc.startswith("v-1.kinesisvideo")
        # VIEWER carries the client id; the session token is part of the signed query.
        assert q["X-Amz-ClientId"] == ["ha-lymow-123"]
        assert q["X-Amz-ChannelARN"] == ["arn:test:chan"]
        assert q["X-Amz-Security-Token"] == ["tokkvs"]
        assert q["X-Amz-Credential"][0].endswith("/%s/kinesisvideo/aws4_request" % REGION)
        assert q["X-Amz-SignedHeaders"] == ["host"]
        assert len(q["X-Amz-Signature"][0]) == 64  # hex sha256

    def test_presign_master_url_omits_client_id(self, client):
        wss = "wss://m-1.kinesisvideo.%s.amazonaws.com" % REGION
        url = client.presign_signaling_url(wss, "arn:test:chan", "ignored", _CREDS, role="MASTER")
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(url).query)
        assert "X-Amz-ClientId" not in q
        assert q["X-Amz-Signature"][0]

    def test_presign_rejects_invalid_role(self, client):
        wss = "wss://m-1.kinesisvideo.%s.amazonaws.com" % REGION
        with pytest.raises(ValueError, match="role must be VIEWER or MASTER"):
            client.presign_signaling_url(wss, "arn:test:chan", "ignored", _CREDS, role="INVALID")

    def test_presign_preserves_endpoint_path(self, client):
        wss = "wss://v-1.kinesisvideo.%s.amazonaws.com/signaling/connect" % REGION
        from urllib.parse import urlparse

        url = client.presign_signaling_url(wss, "arn:test:chan", "ha-lymow-123", _CREDS)
        assert urlparse(url).path == "/signaling/connect"

    def test_presign_signature_changes_with_secret(self, client):
        wss = "wss://v-1.kinesisvideo.%s.amazonaws.com" % REGION
        from urllib.parse import parse_qs, urlparse

        url_a = client.presign_signaling_url(wss, "arn", "c", _CREDS, expires=60)
        url_b = client.presign_signaling_url(wss, "arn", "c", {**_CREDS, "secretAccessKey": "other"}, expires=60)
        url_c = client.presign_signaling_url(wss, "arn", "c", {**_CREDS, "sessionToken": "other-token"}, expires=60)
        sig_a = parse_qs(urlparse(url_a).query)["X-Amz-Signature"][0]
        sig_b = parse_qs(urlparse(url_b).query)["X-Amz-Signature"][0]
        sig_c = parse_qs(urlparse(url_c).query)["X-Amz-Signature"][0]
        assert sig_a != sig_b
        assert sig_a != sig_c
        assert parse_qs(urlparse(url_a).query)["X-Amz-Expires"] == ["60"]

    def test_presign_session_token_is_signed_not_just_appended(self, client):
        # Changing the session token must change the signature — proving it's
        # part of the signed canonical query, not merely appended to the URL.
        wss = "wss://v-1.kinesisvideo.%s.amazonaws.com" % REGION
        from urllib.parse import parse_qs, urlparse

        url_a = client.presign_signaling_url(wss, "arn", "c", _CREDS, expires=60)
        url_b = client.presign_signaling_url(wss, "arn", "c", {**_CREDS, "sessionToken": "different"}, expires=60)
        sig_a = parse_qs(urlparse(url_a).query)["X-Amz-Signature"][0]
        sig_b = parse_qs(urlparse(url_b).query)["X-Amz-Signature"][0]
        assert sig_a != sig_b

    def test_presign_preserves_endpoint_path_without_double_slash(self, client):
        from urllib.parse import urlparse

        # No path → single slash; trailing slash → not doubled.
        for endpoint, expected_path in (
            ("wss://v-1.kinesisvideo.%s.amazonaws.com" % REGION, "/"),
            ("wss://v-1.kinesisvideo.%s.amazonaws.com/" % REGION, "/"),
            ("wss://v-1.kinesisvideo.%s.amazonaws.com/signal" % REGION, "/signal"),
        ):
            url = client.presign_signaling_url(endpoint, "arn", "c", _CREDS)
            assert urlparse(url).path == expected_path

    def test_presign_rejects_unknown_role(self, client):
        with pytest.raises(ValueError, match="VIEWER"):
            client.presign_signaling_url("wss://x", "arn", "c", _CREDS, role="OOPS")


class TestBackupMapManagement:
    async def test_restore_posts_from_and_to(self, client):
        with aioresponses() as m:
            m.post(RE_RESTORE_MAP, payload={"ok": True})
            result = await client.restore_backup_map("mower-001", "dev/map/m1.pb")
            req = list(m.requests.values())[0][0]
        assert result == {"ok": True}
        assert req.kwargs["json"] == {"fromKey": "dev/map/m1.pb", "toThingName": "mower-001"}

    async def test_delete_posts_object_key(self, client):
        with aioresponses() as m:
            m.post(RE_DELETE_MAP, payload={})
            await client.delete_backup_map("dev/map/m1.pb")
            req = list(m.requests.values())[0][0]
        assert req.kwargs["json"] == {"objectKey": "dev/map/m1.pb"}

    async def test_rename_posts_object_key_and_name(self, client):
        with aioresponses() as m:
            m.post(RE_RENAME_MAP, payload={})
            await client.rename_backup_map("dev/map/m1.pb", "Spring")
            req = list(m.requests.values())[0][0]
        assert req.kwargs["json"] == {"objectKey": "dev/map/m1.pb", "name": "Spring"}

    async def test_post_map_non_json_returns_empty(self, client):
        with aioresponses() as m:
            m.post(RE_DELETE_MAP, body="OK", content_type="text/plain")
            assert await client.delete_backup_map("k") == {}

    async def test_post_map_non_dict_returns_empty(self, client):
        with aioresponses() as m:
            m.post(RE_DELETE_MAP, payload=[1, 2, 3])
            assert await client.delete_backup_map("k") == {}

    async def test_restore_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.post(RE_RESTORE_MAP, status=500)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.restore_backup_map("mower-001", "k")


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


class TestOtaEndpoints:
    async def test_check_update_returns_payload(self, client):
        payload = {
            "latestVersion": "v2.1.48_20260518",
            "prefix": "",
            "releaseNote": "Optimized camera...\\nFixed positioning drift...",
        }
        with aioresponses() as m:
            m.get(RE_OTA_CHECK, payload=payload)
            data = await client.check_update("mower-001")
        assert data == payload

    async def test_check_update_sends_thing_param(self, client):
        with aioresponses() as m:
            m.get(RE_OTA_CHECK, payload={})
            await client.check_update("mower-001")
            request = list(m.requests.values())[0][0]
        assert request.kwargs["params"]["deviceThingName"] == "mower-001"

    async def test_check_update_raises_on_http_error(self, client):
        with aioresponses() as m:
            m.get(RE_OTA_CHECK, status=500)
            with pytest.raises(aiohttp.ClientResponseError):
                await client.check_update("mower-001")

    async def test_create_ota_job_sends_object_key_and_returns_job_id(self, client):
        with aioresponses() as m:
            m.get(RE_OTA_CREATE, payload={"jobId": "JOB-123"})
            data = await client.create_ota_job("mower-001", "v2.1.48_20260518")
            request = list(m.requests.values())[0][0]
        assert data == {"jobId": "JOB-123"}
        assert request.kwargs["params"]["objectKey"] == "v2.1.48_20260518"
        assert request.kwargs["params"]["deviceThingName"] == "mower-001"

    async def test_get_ota_job_summary_sends_job_id(self, client):
        with aioresponses() as m:
            m.get(RE_OTA_SUMMARY, payload={"status": "OTA_IN_PROGRESS"})
            data = await client.get_ota_job_summary("mower-001", "JOB-123")
            request = list(m.requests.values())[0][0]
        assert data == {"status": "OTA_IN_PROGRESS"}
        assert request.kwargs["params"]["jobId"] == "JOB-123"


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
