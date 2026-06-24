"""APScheduler background jobs: market data polling and weekly reports."""

from __future__ import annotations

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


def _refresh_market_data() -> None:
    """Pull latest market overview + major news. Runs every N minutes."""
    global _latest_snapshot
    logger.info("Scheduled: refreshing market data")
    try:
        overview = get_market_overview()
        btc_news = search_market_news("Bitcoin crypto market", max_articles=5)
        stock_news = search_market_news("stock market S&P 500", max_articles=5)
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
    """When in AUTO mode, scan markets and act if opportunities are found."""
    if settings.trading_mode != "auto":
        return
    logger.info("Scheduled: autonomous market scan")
    try:
        from src.agent.orchestrator import get_or_create_session

        session = get_or_create_session("autonomous_scanner")
        prompt = (
            "Perform a proactive market scan. Check market overview, scan for technical "
            "signals on major stocks and crypto. If you identify a compelling trade opportunity "
            "with a strong risk/reward profile, execute it. Document your full reasoning."
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

    # Autonomous market scan (every 30 min during market hours Mon–Fri)
    scheduler.add_job(
        _autonomous_scan,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="14-21",  # 9am–5pm EST = 14–21 UTC
            minute="*/30",
            timezone="UTC",
        ),
        id="autonomous_scan",
        replace_existing=True,
    )

    # News memory ingestion (every 30 minutes, 24/7)
    scheduler.add_job(
        _ingest_news,
        trigger=IntervalTrigger(minutes=30),
        id="news_ingestion",
        replace_existing=True,
        misfire_grace_time=120,
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
