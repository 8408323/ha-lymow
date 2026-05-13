"""AWS Cognito SRP authentication for Lymow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .const import REGION_CONFIG

_LOGGER = logging.getLogger(__name__)

# SRP constants
N_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64"
    "ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6B"
    "F12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB31"
    "43DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF"
)
G_HEX = "2"
INFO_BITS = b"Caldera Derived Key"


def _pad_hex(n: int) -> str:
    h = hex(n)[2:]
    if len(h) % 2:
        h = "0" + h
    if h[0] in "89abcdef":
        h = "00" + h
    return h


def _hex_hash(h: str) -> str:
    return hashlib.sha256(bytes.fromhex(h)).hexdigest()


def _hash_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hex_to_long(h: str) -> int:
    return int(h, 16)


def _get_random(n_bytes: int) -> int:
    return int.from_bytes(os.urandom(n_bytes), "big")


def _long_to_hex(n: int) -> str:
    return f"{n:x}"


class SRPClient:
    def __init__(self, username: str, password: str, pool_id: str) -> None:
        self.username = username
        self.password = password
        self.pool_name = pool_id.split("_")[1]
        self.N = _hex_to_long(N_HEX)
        self.g = _hex_to_long(G_HEX)
        self.k = _hex_to_long(_hex_hash("00" + N_HEX + "0" + G_HEX))
        self.a = _get_random(128) % self.N
        self.A = pow(self.g, self.a, self.N)
        if self.A % self.N == 0:
            raise ValueError("SRP A is invalid")

    @property
    def srp_a(self) -> str:
        return _long_to_hex(self.A)

    def process_challenge(
        self, username: str, salt_hex: str, srp_b_hex: str, secret_block_b64: str, timestamp: str
    ) -> str:
        B = _hex_to_long(srp_b_hex)
        if B % self.N == 0:
            raise ValueError("SRP B is invalid")

        u = _hex_to_long(_hex_hash(_pad_hex(self.A) + _pad_hex(B)))
        if u == 0:
            raise ValueError("U cannot be zero")

        username_password_hash = _hash_sha256((self.pool_name + username + ":" + self.password).encode("utf-8"))
        x = _hex_to_long(_hex_hash(_pad_hex(_hex_to_long(salt_hex)) + username_password_hash))

        g_mod_pow_xn = pow(self.g, x, self.N)
        s = pow(B - self.k * g_mod_pow_xn, self.a + u * x, self.N)

        hkdf = _compute_hkdf(
            bytearray.fromhex(_pad_hex(s)),
            bytearray.fromhex(_pad_hex(u)),
        )

        msg = (
            bytearray(self.pool_name, "utf-8")
            + bytearray(username, "utf-8")
            + bytearray(base64.b64decode(secret_block_b64))
            + bytearray(timestamp, "utf-8")
        )
        return base64.b64encode(hmac.new(hkdf, msg, hashlib.sha256).digest()).decode()


def _compute_hkdf(ikm: bytearray, salt: bytearray) -> bytes:
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    info_bits_update = INFO_BITS + b"\x01"
    return hmac.new(prk, info_bits_update, hashlib.sha256).digest()[:16]


class LymowAuth:
    """Handles Cognito SRP login and returns tokens + detected region."""

    _COGNITO_IDP = "https://cognito-idp.{region}.amazonaws.com/"

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def login(self, username: str, password: str) -> dict[str, Any]:
        """Attempt login against all known regions, return tokens + region."""
        # eu-west-1 client_id is confirmed; use it as fallback for regions where it
        # hasn't been individually extracted yet (all regions share the same app)
        fallback_client_id: str = REGION_CONFIG["eu-west-1"]["client_id"]  # type: ignore[assignment]
        for region in ["eu-west-1", "us-east-2", "ap-southeast-2", "ap-east-1"]:
            cfg = REGION_CONFIG[region]
            pool_id = cfg.get("user_pool_id")
            if pool_id is None:
                _LOGGER.debug("[%s] skipped — user_pool_id not configured", region)
                continue
            client_id: str = cfg.get("client_id") or fallback_client_id
            try:
                result = await self._srp_login(username, password, region, pool_id, client_id)
                result["region"] = region
                return result
            except Exception as exc:
                _LOGGER.debug("[%s] login failed: %s", region, exc)
                continue
        raise ValueError("Login failed for all regions")

    async def login_region(self, username: str, password: str, region: str) -> dict[str, Any]:
        """Attempt login against a specific region (user-selected override)."""
        fallback_client_id: str = REGION_CONFIG["eu-west-1"]["client_id"]  # type: ignore[assignment]
        cfg = REGION_CONFIG[region]
        pool_id = cfg.get("user_pool_id")
        if pool_id is None:
            raise ValueError(f"Region {region} has no user_pool_id configured")
        client_id: str = cfg.get("client_id") or fallback_client_id
        result = await self._srp_login(username, password, region, pool_id, client_id)
        result["region"] = region
        return result

    async def _srp_login(
        self,
        username: str,
        password: str,
        region: str,
        pool_id: str,
        client_id: str,
    ) -> dict[str, Any]:
        url = self._COGNITO_IDP.format(region=region)
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }

        srp = SRPClient(username, password, pool_id)
        payload = {
            "AuthFlow": "USER_SRP_AUTH",
            "AuthParameters": {
                "USERNAME": username,
                "SRP_A": srp.srp_a,
            },
            "ClientId": client_id,
        }

        async with self._session.post(url, json=payload, headers=headers) as resp:
            if not resp.ok:
                body = await resp.text()
                raise ValueError(f"HTTP {resp.status}: {body}")
            data = await resp.json(content_type=None)

        params = data["ChallengeParameters"]
        now = datetime.now(UTC)
        # Cognito requires day without leading zero: "Mon Jan 1 00:00:00 UTC 2024"
        timestamp = f"{now.strftime('%a %b')} {now.day} {now.strftime('%H:%M:%S UTC %Y')}"

        signature = srp.process_challenge(
            params["USER_ID_FOR_SRP"],
            params["SALT"],
            params["SRP_B"],
            params["SECRET_BLOCK"],
            timestamp,
        )

        headers["X-Amz-Target"] = "AWSCognitoIdentityProviderService.RespondToAuthChallenge"
        payload = {
            "ChallengeName": "PASSWORD_VERIFIER",
            "ChallengeResponses": {
                "USERNAME": params["USER_ID_FOR_SRP"],
                "PASSWORD_CLAIM_SECRET_BLOCK": params["SECRET_BLOCK"],
                "TIMESTAMP": timestamp,
                "PASSWORD_CLAIM_SIGNATURE": signature,
            },
            "ClientId": client_id,
        }

        async with self._session.post(url, json=payload, headers=headers) as resp:
            if not resp.ok:
                body = await resp.text()
                raise ValueError(f"HTTP {resp.status}: {body}")
            data = await resp.json(content_type=None)

        return data["AuthenticationResult"]

    async def refresh_tokens(self, refresh_token: str, region: str) -> dict[str, Any]:
        """Use a RefreshToken to obtain a fresh AccessToken + IdToken."""
        cfg = REGION_CONFIG[region]
        client_id = cfg.get("client_id") or ""
        url = self._COGNITO_IDP.format(region=region)
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        }
        payload = {
            "AuthFlow": "REFRESH_TOKEN_AUTH",
            "AuthParameters": {"REFRESH_TOKEN": refresh_token},
            "ClientId": client_id,
        }
        async with self._session.post(url, json=payload, headers=headers) as resp:
            if not resp.ok:
                body = await resp.text()
                raise ValueError(f"Token refresh failed HTTP {resp.status}: {body}")
            data = await resp.json(content_type=None)
        return data["AuthenticationResult"]

    async def get_aws_credentials(self, id_token: str, region: str) -> dict[str, Any]:
        """Exchange Cognito IdToken for temporary AWS credentials."""
        cfg = REGION_CONFIG[region]
        pool_id = cfg["user_pool_id"]
        identity_pool_id = cfg["identity_pool_id"]

        url = f"https://cognito-identity.{region}.amazonaws.com/"
        headers = {
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityService.GetId",
        }
        login_key = f"cognito-idp.{region}.amazonaws.com/{pool_id}"

        async with self._session.post(
            url,
            json={
                "IdentityPoolId": identity_pool_id,
                "Logins": {login_key: id_token},
            },
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            get_id = await resp.json(content_type=None)

        identity_id = get_id["IdentityId"]

        headers["X-Amz-Target"] = "AWSCognitoIdentityService.GetCredentialsForIdentity"
        async with self._session.post(
            url,
            json={
                "IdentityId": identity_id,
                "Logins": {login_key: id_token},
            },
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            creds = await resp.json(content_type=None)

        return {
            "identity_id": identity_id,
            "credentials": creds["Credentials"],
        }
