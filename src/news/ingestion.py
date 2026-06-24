"""Ingestion pipeline: fetch from all sources and persist to PostgreSQL.

Deduplication is done at the DB level via the unique constraint on `url`.
Insert-or-ignore semantics: articles already in the DB are silently skipped.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.agent.utils.logger import get_logger
from src.db.database import async_session
from src.db.models import NewsArticle
from src.news.sources import fetch_all

logger = get_logger(__name__)


async def ingest_articles(articles: list[dict[str, Any]]) -> int:
    """Persist *articles* to the DB. Returns the count of newly inserted rows.

    Uses PostgreSQL's ON CONFLICT DO NOTHING so duplicate URLs are silently
    skipped without raising an error.
    """
    if not articles:
        return 0

    rows = [
        {
            "title": a["title"],
            "summary": a.get("summary", ""),
            "content": a.get("content"),
            "source": a["source"],
            "url": a["url"],
            "published_at": a.get("published_at"),
            "sentiment_label": a.get("sentiment_label", "neutral"),
            "sentiment_score": a.get("sentiment_score", 0.0),
            "tags": a.get("tags", []),
        }
        for a in articles
        if a.get("url")
    ]

    if not rows:
        return 0

    async with async_session() as session:
        stmt = pg_insert(NewsArticle).values(rows).on_conflict_do_nothing(index_elements=["url"])
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def run_ingestion(days_back: int = 1) -> dict[str, int]:
    """Fetch all sources and persist. Returns stats dict."""
    logger.info("News ingestion started (days_back=%d)", days_back)
    try:
        articles = await fetch_all(days_back=days_back)
        fetched = len(articles)
        inserted = await ingest_articles(articles)
        logger.info("News ingestion done: fetched=%d new=%d", fetched, inserted)
        return {"fetched": fetched, "inserted": inserted}
    except Exception as exc:
        logger.error("News ingestion failed: %s", exc)
        return {"fetched": 0, "inserted": 0, "error": str(exc)}


async def get_article_count() -> int:
    """Return total number of articles stored."""
    async with async_session() as session:
        result = await session.execute(
            select(NewsArticle).with_only_columns(  # type: ignore[call-overload]
                NewsArticle.id
            )
        )
        return len(result.all())
