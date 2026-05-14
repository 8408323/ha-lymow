"""Tests for Lymow auth module."""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses
from lymow.auth import N_HEX, LymowAuth, SRPClient, _hash_sha256, _hex_hash, _hex_to_long, _pad_hex

_COGNITO_IDP_EU = "https://cognito-idp.eu-west-1.amazonaws.com/"
_COGNITO_IDENTITY_EU = "https://cognito-identity.eu-west-1.amazonaws.com/"
_SRP_B_HEX = _pad_hex(2)  # B=2 is non-zero mod N (large prime)
_FAKE_CHALLENGE = {
    "ChallengeParameters": {
        "USER_ID_FOR_SRP": "user@example.com",
        "SALT": "aa" * 16,
        "SRP_B": _SRP_B_HEX,
        "SECRET_BLOCK": base64.b64encode(b"test-secret-block-data-123456789").decode(),
    }
}
_FAKE_AUTH_RESULT = {
    "AuthenticationResult": {
        "AccessToken": "access",
        "IdToken": "id-token",
        "RefreshToken": "refresh",
    }
}

POOL_ID = "eu-west-1_6qNPbnrrd"


class TestPadHex:
    def test_even_length(self):
        # 0xFF starts with 'f' so _pad_hex adds the 00 prefix to keep it positive
        assert _pad_hex(0xFF) == "00ff"

    def test_odd_length_gets_leading_zero(self):
        assert _pad_hex(0xF) == "0f"

    def test_high_byte_gets_00_prefix(self):
        # Values starting with 8-f need 00 prefix to stay positive in two's complement
        result = _pad_hex(0x80)
        assert result.startswith("00")

    def test_value_starting_with_7_no_prefix(self):
        result = _pad_hex(0x7F)
        assert not result.startswith("00")


class TestHashes:
    def test_hex_hash_returns_hex_string(self):
        result = _hex_hash("00")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_sha256_returns_hex_string(self):
        result = _hash_sha256(b"hello")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert result == expected


class TestSRPClient:
    def setup_method(self):
        self.client = SRPClient("user@example.com", "password123", POOL_ID)

    def test_pool_name_extracted(self):
        assert self.client.pool_name == "6qNPbnrrd"

    def test_srp_a_is_hex_string(self):
        a = self.client.srp_a
        assert isinstance(a, str)
        assert all(c in "0123456789abcdef" for c in a)

    def test_srp_a_nonzero(self):
        assert _hex_to_long(self.client.srp_a) > 0

    def test_process_challenge_returns_base64(self):
        B = pow(2, 256, self.client.N)  # definitely nonzero mod N
        srp_b_hex = _pad_hex(B)
        salt_hex = "aabbcc" + "00" * 13  # 16 bytes of salt
        secret_block = base64.b64encode(b"fake_secret_block_data").decode()
        timestamp = "Mon Jan 1 00:00:00 UTC 2024"

        sig = self.client.process_challenge("user@example.com", salt_hex, srp_b_hex, secret_block, timestamp)

        # Must be valid base64
        decoded = base64.b64decode(sig)
        assert len(decoded) == 32  # SHA-256 HMAC output

    def test_process_challenge_raises_on_zero_B(self):
        zero_b = _pad_hex(self.client.N)  # B == N → B % N == 0
        with pytest.raises(ValueError, match="SRP B is invalid"):
            self.client.process_challenge("user@example.com", "00" * 16, zero_b, base64.b64encode(b"x").decode(), "ts")

    def test_deterministic_for_same_inputs(self):
        B = pow(2, 256, self.client.N)
        srp_b_hex = _pad_hex(B)
        salt_hex = "aabbcc" + "00" * 13
        secret_block = base64.b64encode(b"fake_secret_block_data").decode()
        timestamp = "Mon Jan 1 00:00:00 UTC 2024"

        sig1 = self.client.process_challenge("user@example.com", salt_hex, srp_b_hex, secret_block, timestamp)
        sig2 = self.client.process_challenge("user@example.com", salt_hex, srp_b_hex, secret_block, timestamp)
        assert sig1 == sig2


class TestSRPClientEdgeCases:
    def test_init_raises_on_invalid_a(self):
        """Force A % N == 0 by patching pow to return N (the SRP prime)."""
        N = _hex_to_long(N_HEX)
        with patch("builtins.pow", return_value=N):
            with pytest.raises(ValueError, match="SRP A is invalid"):
                SRPClient("user", "pass", "eu-west-1_XXXXXXXX")

    def test_process_challenge_raises_on_zero_u(self):
        """Force u == 0 by patching _hex_hash to return all-zeros."""
        import sys

        srp = SRPClient("user", "pass", "eu-west-1_6qNPbnrrd")
        auth_mod = sys.modules["lymow.auth"]
        with patch.object(auth_mod, "_hex_hash", return_value="0" * 64):
            with pytest.raises(ValueError, match="U cannot be zero"):
                srp.process_challenge(
                    "user@example.com",
                    "aa" * 16,
                    _SRP_B_HEX,
                    base64.b64encode(b"secret").decode(),
                    "Mon Jan 1 00:00:00 UTC 2024",
                )


@pytest.fixture
async def auth_client():
    async with aiohttp.ClientSession() as session:
        yield LymowAuth(session=session)


class TestLymowAuthLogin:
    async def test_login_success_first_region(self, auth_client):
        auth_client._srp_login = AsyncMock(return_value={"AccessToken": "tok", "IdToken": "id", "RefreshToken": "ref"})
        result = await auth_client.login("user", "pass")
        assert result["region"] == "eu-west-1"
        assert result["AccessToken"] == "tok"

    async def test_login_skips_region_without_pool_id(self, auth_client):
        tried_regions: list[str] = []

        async def _fake_srp(username, password, region, pool_id, client_id):
            tried_regions.append(region)
            return {"AccessToken": "tok", "IdToken": "id", "RefreshToken": "ref"}

        auth_client._srp_login = _fake_srp
        await auth_client.login("user", "pass")
        assert "us-east-2" not in tried_regions

    async def test_login_falls_back_on_first_region_failure(self, auth_client):
        calls: list[str] = []

        async def _fake_srp(username, password, region, pool_id, client_id):
            calls.append(region)
            if region == "eu-west-1":
                raise ValueError("auth failed")
            return {"AccessToken": "tok", "IdToken": "id", "RefreshToken": "ref"}

        auth_client._srp_login = _fake_srp
        result = await auth_client.login("user", "pass")
        assert result["region"] == "ap-southeast-2"
        assert "eu-west-1" in calls

    async def test_login_raises_when_all_regions_fail(self, auth_client):
        auth_client._srp_login = AsyncMock(side_effect=ValueError("auth error"))
        with pytest.raises(ValueError, match="Login failed for all regions"):
            await auth_client.login("user", "pass")


class TestLymowAuthLoginRegion:
    async def test_login_region_success(self, auth_client):
        auth_client._srp_login = AsyncMock(return_value={"AccessToken": "tok", "IdToken": "id", "RefreshToken": "ref"})
        result = await auth_client.login_region("user", "pass", "eu-west-1")
        assert result["region"] == "eu-west-1"
        assert result["AccessToken"] == "tok"

    async def test_login_region_raises_when_no_pool_id(self, auth_client):
        with pytest.raises(ValueError, match="no user_pool_id"):
            await auth_client.login_region("user", "pass", "us-east-2")


class TestLymowAuthSrpLogin:
    async def test_srp_login_success(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDP_EU, payload=_FAKE_CHALLENGE)
            m.post(_COGNITO_IDP_EU, payload=_FAKE_AUTH_RESULT)
            result = await auth_client._srp_login(
                "user@example.com",
                "password",
                "eu-west-1",
                "eu-west-1_6qNPbnrrd",
                "test-client-id",
            )
        assert result["AccessToken"] == "access"

    async def test_srp_login_raises_on_first_http_error(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDP_EU, status=400, body="Bad Request")
            with pytest.raises(ValueError, match="HTTP 400"):
                await auth_client._srp_login(
                    "user@example.com",
                    "password",
                    "eu-west-1",
                    "eu-west-1_6qNPbnrrd",
                    "test-client-id",
                )

    async def test_srp_login_raises_on_second_http_error(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDP_EU, payload=_FAKE_CHALLENGE)
            m.post(_COGNITO_IDP_EU, status=400, body="Bad Request")
            with pytest.raises(ValueError, match="HTTP 400"):
                await auth_client._srp_login(
                    "user@example.com",
                    "password",
                    "eu-west-1",
                    "eu-west-1_6qNPbnrrd",
                    "test-client-id",
                )


class TestLymowAuthRefreshTokens:
    async def test_refresh_tokens_success(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDP_EU, payload=_FAKE_AUTH_RESULT)
            result = await auth_client.refresh_tokens("refresh-token", "eu-west-1")
        assert result["AccessToken"] == "access"

    async def test_refresh_tokens_raises_on_http_error(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDP_EU, status=401, body="Unauthorized")
            with pytest.raises(ValueError, match="Token refresh failed HTTP 401"):
                await auth_client.refresh_tokens("refresh-token", "eu-west-1")


class TestLymowAuthGetAwsCredentials:
    async def test_get_aws_credentials_success(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDENTITY_EU, payload={"IdentityId": "eu-west-1:abc123"})
            m.post(
                _COGNITO_IDENTITY_EU,
                payload={"Credentials": {"AccessKeyId": "key", "SecretKey": "secret", "SessionToken": "token"}},
            )
            result = await auth_client.get_aws_credentials("id-token", "eu-west-1")
        assert result["identity_id"] == "eu-west-1:abc123"
        assert result["credentials"]["AccessKeyId"] == "key"

    async def test_get_aws_credentials_raises_on_first_http_error(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDENTITY_EU, status=403)
            with pytest.raises(aiohttp.ClientResponseError):
                await auth_client.get_aws_credentials("id-token", "eu-west-1")

    async def test_get_aws_credentials_raises_on_second_http_error(self, auth_client):
        with aioresponses() as m:
            m.post(_COGNITO_IDENTITY_EU, payload={"IdentityId": "eu-west-1:abc123"})
            m.post(_COGNITO_IDENTITY_EU, status=403)
            with pytest.raises(aiohttp.ClientResponseError):
                await auth_client.get_aws_credentials("id-token", "eu-west-1")
