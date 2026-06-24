# Scheduler and Jobs

The scheduler is implemented with **APScheduler 3.x** running inside the FastAPI process.
It starts in the `lifespan` context manager (`src/app.py`) and stops on shutdown.

---

## Why APScheduler?

APScheduler runs in-process with asyncio, requiring zero additional infrastructure. The
alternatives:

- **Celery**: separate worker process, task serialisation, operational complexity.
  Worth it for high-throughput background tasks; overkill for 5 periodic jobs.
- **cron (system)**: would require a separate shell script or Python runner that connects
  to the application's database. APScheduler is easier because it runs in the same
  process and shares the same session factory.
- **systemd timers**: similar to cron but harder to configure dynamically.

The downside of in-process scheduling is that if the FastAPI process crashes, all
scheduled jobs stop running. On a home Pi with `restart: unless-stopped` in Docker
Compose, the process typically restarts within seconds.

---

## Jobs

### `market_data_refresh` — every N minutes (default: 5 min)

```python
trigger=IntervalTrigger(minutes=settings.market_data_refresh_minutes)
```

**What it does**: calls `get_market_overview()` (major indices, VIX, bonds, commodities)
and `search_market_news()` for "Bitcoin crypto market" and "stock market S&P 500".
Stores the result in the `_latest_snapshot` module-level dict.

**Why cached?** The `/api/market/snapshot` REST endpoint serves this cached dict directly.
Without the cache, every page load would trigger a Yahoo Finance HTTP request, adding
2–5 seconds of latency per request. The 5-minute staleness is acceptable for a chat
interface — the agent can always call `get_market_overview` directly for fresher data.

**`misfire_grace_time=60`**: if the job fires 60 seconds late (e.g. the system was busy
running inference), it still executes. After 60 seconds, it's considered a misfire and
skipped (the next interval will fire normally).

---

### `weekly_report` — Sunday at 18:00 UTC (configurable)

```python
trigger=CronTrigger(
    day_of_week=settings.weekly_report_day,  # 6 = Sunday
    hour=settings.weekly_report_hour,         # 18
    minute=settings.weekly_report_minute,     # 0
    timezone="UTC",
)
```

18:00 UTC = 19:00 Lisbon time (UTC+1 in winter, UTC+2 in summer with DST). This is
early evening — the US market has been closed for 90 minutes, European markets for 5
hours, so all weekly data is final.

**What it does**: generates a `WEEKLY_REPORT_PROMPT`-driven LLM conversation for the
past 7 days, renders it to HTML + PDF via `weasyprint`, and persists to `reports`.

**Customise**: set in `.env`:

```env
WEEKLY_REPORT_DAY=6     # 0=Monday, 6=Sunday
WEEKLY_REPORT_HOUR=18
WEEKLY_REPORT_MINUTE=0
```

---

### `autonomous_scan` — Mon–Fri 9am–5pm EST (every 30 min)

```python
trigger=CronTrigger(
    day_of_week="mon-fri",
    hour="14-21",   # 9am–5pm EST = 14:00–21:00 UTC
    minute="*/30",  # every 30 minutes
    timezone="UTC",
)
```

**Only runs in `AUTO` mode.** In `RECOMMEND` mode, `_autonomous_scan()` returns
immediately without calling the LLM.

**What it does**: creates (or reuses) an orchestrator session with the ID
`"autonomous_scanner"` and sends the prompt:

> "Perform a proactive market scan. Check market overview, scan for technical signals on
> major stocks and crypto. If you identify a compelling trade opportunity with a strong
> risk/reward profile, execute it. Document your full reasoning."

The agent then runs its full tool-use loop — checking markets, news, technicals — and
may call `execute_trade` if it finds something compelling.

**Important**: the autonomous scanner uses a **shared session** (`"autonomous_scanner"`)
that accumulates history across all 30-minute runs during the trading day. This gives
the scanner context about what it already checked this morning. The session resets on
process restart.

**Analysis persistence**: after the agent's response stream completes, `_persist_analysis()`
writes an `Analysis` row to the database with `trigger="scheduled"` and the full response
text as `summary`. This creates a queryable audit trail of every autonomous scan.

**Safety**: `execute_trade` in auto mode respects `AUTO_ALLOWED_SYMBOLS` and the daily
loss-limit halt flag. If `DailyPnL.auto_trading_halted` is `True`, all trade calls are
blocked until the next calendar day.

---

### `news_ingestion` — every 30 minutes

```python
trigger=IntervalTrigger(minutes=30)
misfire_grace_time=120
```

**What it does**: calls `run_ingestion(days_back=1)` which fetches articles from all 21
RSS feeds, the Guardian API, and the two scraped sites, then inserts new articles into
`news_articles` (duplicates silently skipped).

**30-minute interval**: balances freshness vs. API rate limits. The Guardian free tier
allows 500 requests/day; 5 sections × 48 runs/day = 240 requests/day — well within limit.
RSS feeds have no explicit rate limits but excessive polling is discouraged.

**`misfire_grace_time=120`**: 2 minutes. News ingestion can take 10–20 seconds (many
HTTP requests). If it runs past the next fire time, it's allowed 2 minutes before the
missed fire is discarded.

---

### `newsletter_ingestion` — Saturday at 09:00 UTC

```python
trigger=CronTrigger(
    day_of_week="sat",
    hour=9,
    minute=0,
    timezone="UTC",
)
```

09:00 UTC = 10:00 Lisbon time (winter) / 11:00 (summer). This is mid-morning on Saturday
— most weekly newsletters have been delivered by then.

**`since_days=8`**: looks back 8 days rather than 7, ensuring a newsletter sent late on
Friday or early Saturday is always captured.

**What it does**: connects to the configured IMAP server, searches for messages from the
configured sender within the lookback window, extracts the text body, and ingests as
`NewsArticle` rows. Requires `NEWSLETTER_EMAIL_USER` and `NEWSLETTER_EMAIL_PASSWORD` to be
set in `.env`; silently skips if they're missing.

---

## Scheduler lifecycle

```python
# app.py lifespan
async def lifespan(app):
    await create_all_tables()
    setup_scheduler()   # registers all jobs and calls scheduler.start()
    yield
    shutdown_scheduler()  # scheduler.shutdown(wait=False)
```

`wait=False` means the scheduler doesn't wait for running jobs to complete before
shutting down. This avoids a multi-second hang when stopping the container. The running
job (if any) will be interrupted — for news ingestion this is fine (duplicate-safe); for
report generation it could produce an incomplete report. A future improvement would be
`wait=True` with a timeout.

---

## Monitoring scheduler health

```bash
# Check if jobs are firing
docker compose logs app | grep "Scheduled:"

# Expected log lines:
# INFO  Scheduler started (5 jobs)
# INFO  Scheduled: refreshing market data
# INFO  Scheduled: news ingestion
# INFO  News ingestion done: fetched=142 new=38
```

APScheduler logs missed firings at WARNING level:

```text
WARNING  Run time of job "market_data_refresh" was missed by 0:01:05
```

This happens if the Pi is under heavy load (e.g. model inference). The job will fire on
the next interval. If you see this frequently, consider reducing `market_data_refresh_minutes`
or increasing the interval to avoid competing with LLM inference.
