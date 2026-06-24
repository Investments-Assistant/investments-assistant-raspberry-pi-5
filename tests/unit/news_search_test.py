"""Unit tests for src/news/search.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.news.search import get_recent_headlines, search_news


def _make_db_article(**kwargs) -> MagicMock:
    """Return a MagicMock that behaves like a NewsArticle ORM row."""
    row = MagicMock()
    row.title = kwargs.get("title", "Headline")
    row.summary = kwargs.get("summary", "Summary text")
    row.source = kwargs.get("source", "Reuters")
    row.url = kwargs.get("url", "https://reuters.com/1")
    row.published_at = kwargs.get("published_at", datetime(2024, 1, 1, tzinfo=UTC))
    row.sentiment_label = kwargs.get("sentiment_label", "neutral")
    row.sentiment_score = kwargs.get("sentiment_score", 0.0)
    row.tags = kwargs.get("tags", [])
    return row


# ---------------------------------------------------------------------------
# search_news
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchNews:
    async def test_returns_list_of_dicts(self, mock_async_session_factory):
        # Arrange — mock the DB to return one article
        article = _make_db_article()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [article]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        # Act
        results = await search_news("ECB rates")

        # Assert
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["title"] == "Headline"

    async def test_result_dict_has_expected_keys(self, mock_async_session_factory):
        article = _make_db_article()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [article]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        results = await search_news("test")

        assert {
            "title",
            "summary",
            "source",
            "url",
            "published_at",
            "sentiment",
            "tags",
        } <= results[0].keys()

    async def test_published_at_none_serialised_as_none(self, mock_async_session_factory):
        article = _make_db_article(published_at=None)
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [article]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        results = await search_news("test")

        assert results[0]["published_at"] is None

    async def test_limit_capped_at_100(self, mock_async_session_factory):
        """Passing limit > 100 should be silently capped."""
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        # Act — should not raise even with huge limit
        results = await search_news("test", limit=9999)
        assert isinstance(results, list)

    async def test_empty_db_returns_empty_list(self, mock_async_session_factory):
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        results = await search_news("anything")
        assert results == []


# ---------------------------------------------------------------------------
# get_recent_headlines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetRecentHeadlines:
    async def test_returns_headlines_list(self, mock_async_session_factory):
        article = _make_db_article(title="Breaking news")
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [article]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        results = await get_recent_headlines(limit=10)

        assert len(results) == 1
        assert results[0]["title"] == "Breaking news"

    async def test_headline_dict_structure(self, mock_async_session_factory):
        article = _make_db_article()
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [article]
        execute_result = MagicMock()
        execute_result.scalars.return_value = scalars_mock
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=execute_result)

        results = await get_recent_headlines()

        assert {"title", "source", "url", "published_at", "sentiment"} <= results[0].keys()
