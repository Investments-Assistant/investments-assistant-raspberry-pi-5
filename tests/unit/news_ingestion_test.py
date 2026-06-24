"""Unit tests for src/news/ingestion.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.news.ingestion import get_article_count, ingest_articles, run_ingestion


def _make_article(url: str = "https://example.com/1") -> dict:
    return {
        "title": "Test headline",
        "summary": "Test summary",
        "content": None,
        "source": "Reuters",
        "url": url,
        "published_at": None,
        "sentiment_label": "neutral",
        "sentiment_score": 0.0,
        "tags": [],
    }


# ---------------------------------------------------------------------------
# ingest_articles
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIngestArticles:
    async def test_empty_list_returns_zero(self, mock_async_session_factory):
        result = await ingest_articles([])
        assert result == 0

    async def test_articles_without_url_are_skipped(self, mock_async_session_factory):
        articles = [_make_article(url="")]
        result = await ingest_articles(articles)
        assert result == 0

    async def test_valid_articles_inserted(self, mock_async_session_factory):
        # Arrange
        mock_execute_result = MagicMock()
        mock_execute_result.rowcount = 2
        mock_async_session_factory.return_value.execute = AsyncMock(
            return_value=mock_execute_result
        )

        articles = [_make_article("https://a.com/1"), _make_article("https://a.com/2")]

        # Act
        result = await ingest_articles(articles)

        # Assert
        assert result == 2

    async def test_session_commit_called(self, mock_async_session_factory):
        mock_execute_result = MagicMock()
        mock_execute_result.rowcount = 1
        session = mock_async_session_factory.return_value
        session.execute = AsyncMock(return_value=mock_execute_result)
        session.commit = AsyncMock()

        await ingest_articles([_make_article()])

        session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# run_ingestion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunIngestion:
    async def test_calls_fetch_all_and_ingest(self):
        articles = [_make_article()]
        with patch("src.news.ingestion.fetch_all", new=AsyncMock(return_value=articles)):
            with patch("src.news.ingestion.ingest_articles", new=AsyncMock(return_value=1)):
                stats = await run_ingestion()

        assert stats["fetched"] == 1
        assert stats["inserted"] == 1

    async def test_exception_returns_error_dict(self):
        with patch("src.news.ingestion.fetch_all", new=AsyncMock(side_effect=RuntimeError("net"))):
            stats = await run_ingestion()

        assert stats["fetched"] == 0
        assert "error" in stats

    async def test_days_back_passed_to_fetch_all(self):
        mock_fetch = AsyncMock(return_value=[])
        with patch("src.news.ingestion.fetch_all", new=mock_fetch):
            with patch("src.news.ingestion.ingest_articles", new=AsyncMock(return_value=0)):
                await run_ingestion(days_back=7)

        mock_fetch.assert_called_once_with(days_back=7)


# ---------------------------------------------------------------------------
# get_article_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetArticleCount:
    async def test_returns_row_count(self, mock_async_session_factory):
        mock_result = MagicMock()
        mock_result.all.return_value = [MagicMock()] * 42
        mock_async_session_factory.return_value.execute = AsyncMock(return_value=mock_result)

        count = await get_article_count()
        assert count == 42
