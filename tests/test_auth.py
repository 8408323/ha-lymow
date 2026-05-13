"""Tests for Lymow auth module."""

from __future__ import annotations

import base64
import hashlib

import pytest
from lymow.auth import SRPClient, _hash_sha256, _hex_hash, _hex_to_long, _pad_hex

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
