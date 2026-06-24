"""Unit tests for src/tools/news_memory.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.news_memory import get_latest_news, search_stored_news, trigger_news_ingestion


@pytest.mark.unit
class TestSearchStoredNews:
    async def test_delegates_to_search_news(self):
        mock_articles = [{"title": "Test", "url": "https://x.com"}]

        with patch("src.tools.news_memory.search_news", new=AsyncMock(return_value=mock_articles)):
            with patch("src.tools.news_memory.get_article_count", new=AsyncMock(return_value=100)):
                result = await search_stored_news("ECB rate cut")

        assert result["query"] == "ECB rate cut"
        assert result["results_found"] == 1
        assert result["total_articles_in_memory"] == 100
        assert result["articles"] == mock_articles

    async def test_filters_included_in_response(self):
        with patch("src.tools.news_memory.search_news", new=AsyncMock(return_value=[])):
            with patch("src.tools.news_memory.get_article_count", new=AsyncMock(return_value=0)):
                result = await search_stored_news(
                    "inflation", days_back=7, sources=["Reuters"], sentiment="bearish", limit=5
                )

        assert result["filters"]["days_back"] == 7
        assert result["filters"]["sources"] == ["Reuters"]
        assert result["filters"]["sentiment"] == "bearish"

    async def test_passes_all_args_to_search(self):
        mock_search = AsyncMock(return_value=[])
        with patch("src.tools.news_memory.search_news", new=mock_search):
            with patch("src.tools.news_memory.get_article_count", new=AsyncMock(return_value=0)):
                await search_stored_news("test", days_back=14, limit=10)

        mock_search.assert_called_once_with(
            query="test", days_back=14, sources=None, sentiment=None, limit=10
        )


@pytest.mark.unit
class TestGetLatestNews:
    async def test_returns_articles_and_count(self):
        headlines = [{"title": "Headline 1"}, {"title": "Headline 2"}]

        with patch(
            "src.tools.news_memory.get_recent_headlines", new=AsyncMock(return_value=headlines)
        ):
            with patch("src.tools.news_memory.get_article_count", new=AsyncMock(return_value=50)):
                result = await get_latest_news(limit=20)

        assert result["articles"] == headlines
        assert result["total_articles_in_memory"] == 50

    async def test_limit_passed_to_get_recent_headlines(self):
        mock_headlines = AsyncMock(return_value=[])
        with patch("src.tools.news_memory.get_recent_headlines", new=mock_headlines):
            with patch("src.tools.news_memory.get_article_count", new=AsyncMock(return_value=0)):
                await get_latest_news(limit=5)

        mock_headlines.assert_called_once_with(limit=5)


@pytest.mark.unit
class TestTriggerNewsIngestion:
    async def test_delegates_to_run_ingestion(self):
        expected = {"fetched": 30, "inserted": 10}
        with patch("src.tools.news_memory.run_ingestion", new=AsyncMock(return_value=expected)):
            result = await trigger_news_ingestion()

        assert result == expected
