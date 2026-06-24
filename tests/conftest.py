"""Root-level pytest fixtures shared across unit and integration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Settings override — forces development mode so IP checks are bypassed
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def force_development_env(monkeypatch):
    """Override environment to 'development' for every test.

    This prevents IP-whitelist checks from blocking TestClient requests.
    Patches src.config.settings and also the local bindings in news modules
    that do `from src.config import settings` at module level.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    mock_cfg = MagicMock()
    mock_cfg.environment = "development"
    mock_cfg.is_development = True
    mock_cfg.trading_mode = "recommend"
    mock_cfg.llm_model_path = "/app/models/test.gguf"
    mock_cfg.guardian_api_key = ""
    mock_cfg.newsapi_key = ""
    mock_cfg.newsletter_email_user = ""
    mock_cfg.newsletter_email_password = ""
    mock_cfg.newsletter_sender_filter = ""
    mock_cfg.newsletter_imap_server = "imap.gmail.com"
    mock_cfg.newsletter_imap_port = 993
    mock_cfg.auto_allowed_symbols_set = set()
    mock_cfg.auto_max_trade_usd = 500.0
    mock_cfg.auto_daily_loss_limit_usd = 1000.0
    mock_cfg.is_ip_allowed = MagicMock(return_value=True)
    with (
        patch("src.config.settings", mock_cfg),
        patch("src.news.sources.settings", mock_cfg),
        patch("src.news.email_reader.settings", mock_cfg),
    ):
        yield mock_cfg


# ---------------------------------------------------------------------------
# Async DB session mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db_session():
    """Return an AsyncMock that behaves like an SQLAlchemy AsyncSession."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_async_session_factory(mock_db_session):
    """Patch async_session context manager used across all DB calls.

    Also patches the local bindings in modules that do
    `from src.db.database import async_session` at module level, since
    patching the source (src.db.database.async_session) does not affect
    already-imported local names in other modules.
    """
    with patch("src.db.database.async_session") as mock_factory:
        mock_factory.return_value = mock_db_session
        with (
            patch("src.news.ingestion.async_session", mock_factory),
            patch("src.news.search.async_session", mock_factory),
        ):
            yield mock_factory
