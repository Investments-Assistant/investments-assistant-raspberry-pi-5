"""Smoke tests — fast checks that critical modules import and initialise correctly."""

from __future__ import annotations


def test_app_imports() -> None:
    """FastAPI app object must be importable without raising."""
    # Import only the router to avoid triggering lifespan (DB/scheduler)
    from src.web.routes import router  # noqa: F401

    assert router is not None


def test_tool_definitions_importable() -> None:
    """Tool definitions list must be non-empty."""
    from src.tools.definitions import TOOL_DEFINITIONS

    assert isinstance(TOOL_DEFINITIONS, list)
    assert len(TOOL_DEFINITIONS) > 0


def test_settings_importable() -> None:
    """Settings must load without raising even without a .env file."""
    from src.config import settings

    assert settings is not None
    assert settings.trading_mode in ("recommend", "auto")


def test_db_models_importable() -> None:
    """All ORM model classes must be importable."""
    from src.db.models import (  # noqa: F401
        Analysis,
        ChatMessage,
        DailyPnL,
        NewsArticle,
        Report,
        SimulationResult,
        Trade,
    )
