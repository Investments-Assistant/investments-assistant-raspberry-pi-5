"""APScheduler background jobs: market data polling and weekly reports."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.agent.utils.logger import get_logger
from src.config import settings
from src.news.email_reader import read_and_ingest_newsletters
from src.news.ingestion import run_ingestion
from src.tools.market_data import get_market_overview
from src.tools.news import search_market_news

logger = get_logger(__name__)

scheduler = AsyncIOScheduler()

# Cache for the latest market snapshot (served to the UI)
_latest_snapshot: dict = {}


def get_latest_snapshot() -> dict:
    return _latest_snapshot


async def _refresh_market_data() -> None:
    """Pull latest market overview + major news. Runs every N minutes."""
    global _latest_snapshot
    logger.info("Scheduled: refreshing market data")
    try:
        # yfinance and the RSS reader are synchronous adapters.  Run them in
        # worker threads so a slow provider cannot stall WebSocket responses.
        overview, btc_news, stock_news = await asyncio.gather(
            asyncio.to_thread(get_market_overview),
            asyncio.to_thread(search_market_news, "Bitcoin crypto market", max_articles=5),
            asyncio.to_thread(search_market_news, "stock market S&P 500", max_articles=5),
        )
        _latest_snapshot = {
            "timestamp": datetime.now(UTC).isoformat(),
            "market_overview": overview,
            "crypto_news": btc_news,
            "stock_news": stock_news,
        }
        logger.debug("Market snapshot refreshed")
    except Exception as exc:
        logger.error("Market data refresh failed: %s", exc)


async def _run_weekly_report() -> None:
    """Generate and save the weekly report."""
    logger.info("Scheduled: generating weekly report")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    # Compute last 7 days
    from datetime import timedelta

    start = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        from src.scheduler.reporter import generate_report

        result = await generate_report(period_start=start, period_end=today)
        logger.info("Weekly report generated: %s", result.get("report_id"))
    except Exception as exc:
        logger.error("Weekly report generation failed: %s", exc)


async def _ingest_news() -> None:
    """Fetch and persist articles from all configured sources."""
    logger.info("Scheduled: news ingestion")
    stats = await run_ingestion(days_back=1)
    logger.info("News ingestion complete: %s", stats)


async def _ingest_newsletter() -> None:
    """Check inbox for new newsletters and ingest them (runs Saturday mornings)."""
    logger.info("Scheduled: newsletter email ingestion")
    stats = await read_and_ingest_newsletters(since_days=8)
    logger.info("Newsletter ingestion complete: %s", stats)


async def _autonomous_scan() -> None:
    """Run a local evidence review; execution remains server-guarded."""
    if not settings.autonomous_scans_enabled:
        return
    logger.info("Scheduled: autonomous market scan")
    try:
        from src.agent.orchestrator import get_or_create_session

        session = get_or_create_session("autonomous_scanner")
        prompt = (
            "Perform the hourly autonomous investment review. Check the latest stored global "
            "news, market overview, portfolio exposure, and technical data for configured "
            "assets. Identify material risks, thesis invalidations, and opportunities across "
            "stocks, ETFs, options, crypto, and FX. Use evidence and state uncertainty. "
            f"Current mode is {settings.trading_mode}. In recommend mode, record proposals "
            "but do not attempt to confirm them. In auto mode, only use execute_trade when "
            "the server-side limits can be satisfied; never bypass a blocked result."
        )
        text_parts: list[str] = []
        async for event in session.chat(prompt):
            if event["type"] == "text_delta":
                text_parts.append(event["text"])

        summary = "".join(text_parts)
        if summary:
            await _persist_analysis(summary, prompt)
    except Exception as exc:
        logger.error("Autonomous scan failed: %s", exc)


async def _persist_analysis(summary: str, prompt: str) -> None:
    """Save the autonomous scan result as an Analysis record."""
    try:
        from src.db.database import async_session
        from src.db.models import Analysis

        async with async_session() as session:
            analysis = Analysis(
                trigger="scheduled",
                symbols=[],
                summary=summary,
                raw_data={"prompt": prompt},
            )
            session.add(analysis)
            await session.commit()
        logger.info("Autonomous scan analysis persisted")
    except Exception as exc:
        logger.warning("Failed to persist autonomous scan analysis: %s", exc)


def setup_scheduler() -> None:
    """Register all scheduled jobs and start the scheduler."""

    # Market data refresh (every N minutes, all day)
    scheduler.add_job(
        _refresh_market_data,
        trigger=IntervalTrigger(minutes=settings.market_data_refresh_minutes),
        id="market_data_refresh",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Weekly report
    scheduler.add_job(
        _run_weekly_report,
        trigger=CronTrigger(
            day_of_week=settings.weekly_report_day,
            hour=settings.weekly_report_hour,
            minute=settings.weekly_report_minute,
            timezone="UTC",
        ),
        id="weekly_report",
        replace_existing=True,
    )

    # Autonomous review (hourly by default, including global/crypto markets)
    scheduler.add_job(
        _autonomous_scan,
        trigger=IntervalTrigger(minutes=settings.autonomous_scan_interval_minutes),
        id="autonomous_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # News memory ingestion (hourly by default, 24/7)
    scheduler.add_job(
        _ingest_news,
        trigger=IntervalTrigger(minutes=settings.news_ingestion_minutes),
        id="news_ingestion",
        replace_existing=True,
        misfire_grace_time=120,
        max_instances=1,
        coalesce=True,
    )

    # Newsletter email reader (every Saturday at 09:00 UTC = ~10am Lisbon time)
    scheduler.add_job(
        _ingest_newsletter,
        trigger=CronTrigger(
            day_of_week="sat",
            hour=9,
            minute=0,
            timezone="UTC",
        ),
        id="newsletter_ingestion",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started (%d jobs)", len(scheduler.get_jobs()))


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
