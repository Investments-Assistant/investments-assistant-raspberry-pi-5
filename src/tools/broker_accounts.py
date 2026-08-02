"""Per-user brokerage configuration vault.

Secrets are encrypted before they enter PostgreSQL. The Fernet key is supplied
out-of-band through ``BROKER_CREDENTIALS_KEY`` and is never returned to the UI
or injected into the LLM context.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from src.config import settings
from src.db.database import async_session
from src.db.models import BrokerAccount

SUPPORTED_BROKERS = ("alpaca", "ibkr", "coinbase", "binance")

BROKER_FIELDS: dict[str, set[str]] = {
    "alpaca": {"api_key", "secret_key", "paper"},
    "coinbase": {"api_key", "api_secret"},
    "binance": {"api_key", "secret_key", "testnet"},
    "ibkr": {"host", "port", "client_id", "enabled"},
}
SECRET_FIELDS: dict[str, set[str]] = {
    "alpaca": {"api_key", "secret_key"},
    "coinbase": {"api_key", "api_secret"},
    "binance": {"api_key", "secret_key"},
    "ibkr": set(),
}


class BrokerVaultUnavailable(RuntimeError):
    """Raised when the deployment has not configured encryption at rest."""


@dataclass(frozen=True)
class BrokerAccountConfig:
    id: str
    user_id: str
    broker: str
    display_name: str
    config: dict[str, Any]

    @property
    def public(self) -> dict[str, Any]:
        credentials = self.config
        status: dict[str, bool] = {}
        masked: dict[str, Any] = {}
        for field in BROKER_FIELDS[self.broker]:
            value = credentials.get(field)
            if field in SECRET_FIELDS[self.broker]:
                text_value = str(value or "")
                status[field] = bool(text_value)
                if not text_value:
                    masked[field] = ""
                elif len(text_value) <= 4:
                    masked[field] = "••••"
                else:
                    masked[field] = f"••••{text_value[-4:]}"
            else:
                status[field] = value not in (None, "", False)
                masked[field] = value
        return {
            "id": self.id,
            "broker": self.broker,
            "display_name": self.display_name,
            "active": True,
            "configured_fields": status,
            "masked_fields": masked,
        }


def _fernet() -> Fernet:
    key = settings.broker_credentials_key.strip()
    if not key:
        raise BrokerVaultUnavailable(
            "Broker credential storage is unavailable until BROKER_CREDENTIALS_KEY is configured."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BrokerVaultUnavailable(
            "BROKER_CREDENTIALS_KEY is invalid; generate a Fernet key with "
            "scripts/create_broker_key.py."
        ) from exc


def ensure_broker_vault() -> None:
    """Validate that encrypted account storage is available without exposing the key."""
    _fernet()


def encrypt_config(config: dict[str, Any]) -> str:
    """Encrypt a JSON-serialisable broker configuration."""
    try:
        payload = json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Broker configuration must be JSON serialisable") from exc
    return _fernet().encrypt(payload).decode("ascii")


def decrypt_config(token: str) -> dict[str, Any]:
    """Decrypt one database token and reject malformed/corrupt data."""
    try:
        value = json.loads(_fernet().decrypt(token.encode("ascii")).decode("utf-8"))
    except (InvalidToken, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise BrokerVaultUnavailable("Stored broker credentials could not be decrypted") from exc
    if not isinstance(value, dict):
        raise BrokerVaultUnavailable("Stored broker credentials are invalid")
    return value


def validate_broker_config(broker: str, config: dict[str, Any]) -> dict[str, Any]:
    """Keep only supported fields and normalize provider-specific values."""
    if broker not in BROKER_FIELDS:
        raise ValueError(f"Unknown broker: {broker}")
    unknown = set(config) - BROKER_FIELDS[broker]
    if unknown:
        raise ValueError(f"Unsupported {broker} configuration field: {sorted(unknown)[0]}")
    normalized = dict(config)

    def boolean(field: str, default: bool) -> bool:
        value = normalized.get(field, default)
        if not isinstance(value, bool):
            raise ValueError(f"{field} must be a boolean")
        return value

    if broker == "ibkr":
        normalized["host"] = str(normalized.get("host", "127.0.0.1")).strip()[:255]
        normalized["port"] = int(normalized.get("port", 4002))
        normalized["client_id"] = int(normalized.get("client_id", 1))
        normalized["enabled"] = boolean("enabled", True)
        if not 1 <= normalized["port"] <= 65535:
            raise ValueError("IBKR port must be between 1 and 65535")
        if normalized["client_id"] < 0:
            raise ValueError("IBKR client_id must be non-negative")
    elif broker == "alpaca":
        normalized["paper"] = boolean("paper", True)
    elif broker == "binance":
        normalized["testnet"] = boolean("testnet", True)
    for field in SECRET_FIELDS[broker]:
        if field in normalized:
            value = normalized[field]
            if not isinstance(value, str) or len(value) > 4_096:
                raise ValueError(f"{field} must be a string of at most 4096 characters")
    return normalized


async def load_user_broker_accounts(
    user_id: str,
    broker: str | None = None,
    account_id: str | None = None,
) -> list[BrokerAccountConfig]:
    """Load and decrypt active accounts owned by one authenticated user."""
    query = select(BrokerAccount).where(
        BrokerAccount.user_id == user_id,
        BrokerAccount.is_active.is_(True),
    )
    if broker:
        query = query.where(BrokerAccount.broker == broker)
    if account_id:
        query = query.where(BrokerAccount.id == account_id)
    async with async_session() as session:
        result = await session.execute(query.order_by(BrokerAccount.display_name))
        rows = result.scalars().all()
    return [
        BrokerAccountConfig(
            id=row.id,
            user_id=row.user_id,
            broker=row.broker,
            display_name=row.display_name,
            config=decrypt_config(row.config_encrypted),
        )
        for row in rows
    ]


def capability_unavailable(broker: str, capability: str) -> dict[str, Any]:
    """Return a model-friendly explanation instead of throwing on missing keys."""
    broker_text = f"{broker} " if broker and broker != "any" else ""
    return {
        "available": False,
        "broker": broker,
        "capability": capability,
        "error": (
            f"This task cannot be executed because no active {broker_text}brokerage account is "
            "configured for the current user. Add the required credentials or connection "
            "settings in Brokerage "
            "Accounts in the UI, then try again."
        ),
    }
