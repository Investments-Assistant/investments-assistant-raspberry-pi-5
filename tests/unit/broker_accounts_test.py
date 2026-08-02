"""Tests for encrypted per-user brokerage account capability handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet
import pytest

from src.tools import dispatcher
from src.tools.broker_accounts import (
    BrokerAccountConfig,
    decrypt_config,
    encrypt_config,
    validate_broker_config,
)


@pytest.mark.unit
def test_broker_config_round_trips_encrypted_without_plaintext_token():
    key = Fernet.generate_key().decode("ascii")
    config = {"api_key": "key-value", "secret_key": "secret-value", "paper": True}
    with patch("src.tools.broker_accounts.settings") as settings:
        settings.broker_credentials_key = key
        token = encrypt_config(config)
        assert "secret-value" not in token
        assert decrypt_config(token) == config


@pytest.mark.unit
def test_public_account_masks_short_and_long_secrets():
    account = BrokerAccountConfig(
        id="account-1",
        user_id="user-1",
        broker="alpaca",
        display_name="Paper",
        config={"api_key": "abc", "secret_key": "secret-value", "paper": True},
    )
    assert account.public["masked_fields"]["api_key"] == "••••"
    assert account.public["masked_fields"]["secret_key"] == "••••alue"
    assert account.public["configured_fields"]["secret_key"] is True


@pytest.mark.unit
def test_broker_boolean_fields_are_strict():
    with pytest.raises(ValueError, match="paper must be a boolean"):
        validate_broker_config(
            "alpaca",
            {"api_key": "key", "secret_key": "secret", "paper": "false"},
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authenticated_user_without_account_gets_capability_result():
    with patch.object(dispatcher, "load_user_broker_accounts", new=AsyncMock(return_value=[])):
        with dispatcher.tool_context("session-1", "user-1", "recommend"):
            result = await dispatcher._dispatch(
                "get_account_info",
                {"broker": "alpaca"},
            )
    assert result["available"] is False
    assert "required credentials" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_multiple_accounts_require_explicit_selection():
    accounts = [
        BrokerAccountConfig("one", "user-1", "alpaca", "Paper", {"paper": True}),
        BrokerAccountConfig("two", "user-1", "alpaca", "Live", {"paper": False}),
    ]
    with patch.object(
        dispatcher,
        "load_user_broker_accounts",
        new=AsyncMock(return_value=accounts),
    ):
        with dispatcher.tool_context("session-1", "user-1", "recommend"):
            result = await dispatcher._dispatch(
                "get_account_info",
                {"broker": "alpaca"},
            )
    assert result["account_id_required"] is True
    assert {item["id"] for item in result["accounts"]} == {"one", "two"}
