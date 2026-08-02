"""Authentication primitives used by the private Pi UI."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.web.auth import create_session, hash_password, verify_password, verify_session


@pytest.mark.unit
class TestPasswordHashing:
    def test_hash_round_trip(self):
        encoded = hash_password("a-long-and-private-password")
        assert encoded.startswith("scrypt$v1$")
        assert verify_password("a-long-and-private-password", encoded)
        assert not verify_password("wrong-password", encoded)

    def test_short_password_rejected(self):
        with pytest.raises(ValueError):
            hash_password("too-short")

    def test_malformed_hash_rejected(self):
        assert not verify_password("password", "not-a-password-hash")


@pytest.mark.unit
class TestSignedSessions:
    def test_session_round_trip_and_tamper_detection(self):
        with patch("src.web.auth.config.settings") as cfg:
            cfg.auth_session_secret = "test-session-secret"
            cfg.auth_username = "admin"
            cfg.auth_password_hash = "configured"
            cfg.auth_session_ttl_minutes = 10
            token = create_session("admin")
            assert verify_session(token).username == "admin"
            assert verify_session(token + "tampered") is None

    def test_unknown_user_rejected(self):
        with patch("src.web.auth.config.settings") as cfg:
            cfg.auth_session_secret = "test-session-secret"
            cfg.auth_username = "admin"
            cfg.auth_password_hash = "configured"
            cfg.auth_session_ttl_minutes = 10
            token = create_session("other")
            assert verify_session(token) is None
