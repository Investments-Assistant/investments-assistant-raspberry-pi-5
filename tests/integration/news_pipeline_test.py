"""Integration tests for the news ingestion pipeline.

These tests verify that:
  - Articles fetched from mocked sources can be ingested into a real PostgreSQL DB
  - Duplicate URLs are skipped via ON CONFLICT DO NOTHING
  - The search and headline functions return correctly shaped results

Run with:
    pytest -m integration tests/integration/test_news_pipeline.py
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from src.db.models import NewsArticle
from src.news.ingestion import get_article_count, ingest_articles
from src.news.search import get_recent_headlines, search_news


def _article(url: str = "https://example.com/1", title: str = "Test headline") -> dict:
    return {
        "title": title,
        "summary": "A summary of the article.",
        "content": None,
        "source": "Reuters",
        "url": url,
        "published_at": datetime(2024, 3, 1, tzinfo=UTC),
        "sentiment_label": "neutral",
        "sentiment_score": 0.0,
        "tags": ["AAPL"],
    }


# ---------------------------------------------------------------------------
# ingest_articles
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestIngestArticlesIntegration:
    async def test_articles_written_to_db(self, db_session):
        articles = [_article("https://reuters.com/1"), _article("https://reuters.com/2")]

        with patch("src.news.ingestion.async_session") as mock_factory:
            mock_factory.return_value = db_session
            inserted = await ingest_articles(articles)

        assert inserted == 2

        result = await db_session.execute(select(NewsArticle))
        rows = result.scalars().all()
        assert len(rows) == 2

    async def test_duplicate_url_skipped(self, db_session):
        url = "https://reuters.com/dedup"
        articles = [_article(url), _article(url)]

        with patch("src.news.ingestion.async_session") as mock_factory:
            mock_factory.return_value = db_session
            await ingest_articles(articles)

        # Only one row should be in the DB
        result = await db_session.execute(
            select(func.count()).select_from(NewsArticle).where(NewsArticle.url == url)
        )
        count = result.scalar()
        assert count == 1

    async def test_missing_url_skipped(self, db_session):
        articles = [_article(url="")]

        with patch("src.news.ingestion.async_session") as mock_factory:
            mock_factory.return_value = db_session
            inserted = await ingest_articles(articles)

        assert inserted == 0


# ---------------------------------------------------------------------------
# get_article_count
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetArticleCountIntegration:
    async def test_count_matches_db(self, db_session):
        # Insert 3 articles directly
        for i in range(3):
            db_session.add(_article_orm(f"https://count-test.com/{i}"))
        await db_session.flush()

        with patch("src.news.ingestion.async_session") as mock_factory:
            mock_factory.return_value = db_session
            count = await get_article_count()

        assert count >= 3


def _article_orm(url: str) -> NewsArticle:
    return NewsArticle(
        title="Headline",
        summary="Summary",
        source="Test",
        url=url,
        sentiment_label="neutral",
        sentiment_score=0.0,
        tags=[],
    )


# ---------------------------------------------------------------------------
# search_news
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSearchNewsIntegration:
    async def test_full_text_search_finds_article(self, db_session):
        db_session.add(
            NewsArticle(
                title="Federal Reserve hikes interest rates aggressively",
                summary="The Fed raised rates by 75bp at its July meeting.",
                source="Bloomberg",
                url="https://bloomberg.com/fed-hike-2024",
                sentiment_label="bearish",
                sentiment_score=-0.6,
                published_at=datetime(2024, 7, 1, tzinfo=UTC),
                tags=["FED"],
            )
        )
        await db_session.flush()

        with patch("src.news.search.async_session") as mock_factory:
            mock_factory.return_value = db_session
            results = await search_news("Federal Reserve rates")

        assert any("Federal Reserve" in r["title"] for r in results)

    async def test_sentiment_filter(self, db_session):
        db_session.add(
            NewsArticle(
                title="Markets rally on positive earnings",
                summary="Strong Q3 results lift equities.",
                source="CNBC",
                url="https://cnbc.com/rally-2024",
                sentiment_label="bullish",
                sentiment_score=0.7,
                published_at=datetime(2024, 8, 1, tzinfo=UTC),
                tags=[],
            )
        )
        await db_session.flush()

        with patch("src.news.search.async_session") as mock_factory:
            mock_factory.return_value = db_session
            results = await search_news("markets", sentiment="bullish")

        for r in results:
            assert r["sentiment"]["label"] == "bullish"

    async def test_empty_query_returns_list(self, db_session):
        with patch("src.news.search.async_session") as mock_factory:
            mock_factory.return_value = db_session
            results = await search_news("nonexistent_term_xyz_123")

        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# get_recent_headlines
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetRecentHeadlinesIntegration:
    async def test_returns_most_recent_first(self, db_session):
        for day in [1, 2, 3]:
            db_session.add(
                NewsArticle(
                    title=f"News day {day}",
                    summary="",
                    source="Test",
                    url=f"https://test.com/day-{day}",
                    published_at=datetime(2024, 1, day, tzinfo=UTC),
                )
            )
        await db_session.flush()

        with patch("src.news.search.async_session") as mock_factory:
            mock_factory.return_value = db_session
            results = await get_recent_headlines(limit=3)

        assert len(results) == 3
        # Should be ordered most recent first
        dates = [r["published_at"] for r in results if r["published_at"]]
        assert dates == sorted(dates, reverse=True)

    async def test_limit_respected(self, db_session):
        for i in range(5):
            db_session.add(
                NewsArticle(
                    title=f"Headline {i}",
                    summary="",
                    source="X",
                    url=f"https://x.com/{i}",
                )
            )
        await db_session.flush()

        with patch("src.news.search.async_session") as mock_factory:
            mock_factory.return_value = db_session
            results = await get_recent_headlines(limit=2)

        assert len(results) <= 2
