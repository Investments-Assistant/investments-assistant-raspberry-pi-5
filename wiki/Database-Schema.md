# Database Schema

**Engine**: PostgreSQL 16 (Alpine Docker image)
**ORM**: SQLAlchemy 2.0 async with `asyncpg` driver
**Migration**: tables are created via `Base.metadata.create_all` on startup. Alembic is
a declared dependency for future migration management but is not yet configured with
migration scripts.

---

## Why SQLAlchemy 2.0 async?

SQLAlchemy 2.0 introduced a first-class async API built on `asyncio`. Combined with
`asyncpg` (a pure asyncio PostgreSQL driver, no thread pool), database access is
genuinely non-blocking — the FastAPI event loop is free to handle other WebSocket messages
while a query is awaiting a result.

The older `databases` library (used in many FastAPI tutorials) wraps a sync driver in a
thread pool. SQLAlchemy 2.0 async is architecturally cleaner and has better typing support.

---

## Connection pool

```python
engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,  # log SQL in dev
    pool_pre_ping=True,            # test connections before use
    pool_size=5,                   # 5 persistent connections
    max_overflow=10,               # up to 10 extra under load
)
```

`pool_pre_ping=True` sends a `SELECT 1` before using a connection from the pool. This
handles the case where PostgreSQL has closed an idle connection (e.g. after the Docker
container restarted). Without it, the first request after an idle period would fail with
a broken pipe error.

`pool_size=5` is appropriate for a single-user server. The Pi's `asyncpg` concurrency
ceiling is effectively the number of CPU cores (4) for CPU-bound work; 5 connections
ensures there's always one available.

---

## Tables

### `chat_messages`

One row per turn in a conversation.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID (string) | Primary key, `uuid4()` |
| `session_id` | String(36) | Indexed — used for history queries |
| `role` | String(16) | `user`, `assistant`, or `tool` |
| `content` | Text | The message text |
| `tool_calls` | JSON | Optional — raw tool call data for `assistant` turns |
| `created_at` | DateTime(tz) | Server default `now()` |

**Why UUID for PK?** UUIDs are session-safe — they can be generated client-side (e.g.
`str(uuid.uuid4())` in Python) without a round-trip to the database to get the next
auto-increment ID. This allows the application to construct the record in Python before
the `INSERT`.

**Why `session_id` is indexed**: the most common query is
`SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at LIMIT 15`.
Without the index, this would be a full table scan.

---

### `trades`

One row per order submitted or recommended.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `broker` | String(32) | `alpaca`, `ibkr`, `coinbase`, `binance` |
| `symbol` | String(20) | Ticker |
| `side` | String(8) | `buy` or `sell` |
| `quantity` | Float | Shares or coins |
| `price` | Float | Limit price (null for market orders) |
| `order_type` | String(16) | `market`, `limit`, `stop_limit` |
| `status` | String(16) | `pending`, `filled`, `cancelled`, `rejected` |
| `broker_order_id` | String(64) | The broker-assigned order ID |
| `mode` | String(16) | `auto`, `manual`, `simulated` |
| `reason` | Text | Agent's stated rationale — audit trail |
| `pnl_usd` | Float | Realised P&L when the trade is closed (filled by future work) |
| `created_at` | DateTime(tz) | When the order was placed |
| `filled_at` | DateTime(tz) | When the order was filled (null until then) |

**`mode` values**:

- `auto` — executed autonomously by the agent in AUTO mode
- `manual` — user-confirmed via `confirm_trade` tool in RECOMMEND mode
- `simulated` — reserved for future paper-trading simulation

**Audit trail**: every trade has a `reason` column populated from the `reason` parameter
the LLM must provide when calling `execute_trade` or `confirm_trade`. This creates a
permanent, queryable record of why each trade was made — crucial for reviewing whether
the agent's reasoning was sound.

---

### `analyses`

A market analysis snapshot produced by the autonomous scan job.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `trigger` | String(32) | `scheduled`, `user_request`, `alert` |
| `symbols` | JSON | List of symbols analysed |
| `summary` | Text | Full agent response text |
| `sentiment` | String(16) | `bullish`, `bearish`, `neutral` (optional) |
| `confidence` | Float | 0–1 confidence score (optional) |
| `recommendations` | JSON | List of `{"symbol": ..., "action": ...}` dicts |
| `raw_data` | JSON | Metadata — e.g. the prompt that triggered the scan |
| `created_at` | DateTime(tz) | — |

**Written by**: `_autonomous_scan()` in `jobs.py` collects the full text produced by the
agent's tool-use loop and persists it via `_persist_analysis()` after each scheduled scan.
The `trigger` field is set to `"scheduled"`. User-initiated analyses (`trigger="user_request"`)
are a future extension — the table schema already supports them.

---

### `reports`

One row per generated investment report.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `title` | String(256) | Human-readable title |
| `period_start` | DateTime(tz) | Report period start |
| `period_end` | DateTime(tz) | Report period end |
| `html_content` | Text | The full HTML report |
| `pdf_path` | String(512) | Absolute path to the PDF on disk (null if weasyprint failed) |
| `total_pnl_usd` | Float | Period P&L (populated manually or by future work) |
| `created_at` | DateTime(tz) | — |

**Why store HTML in the DB?** The HTML is stored in the database for easy retrieval via
the REST API (`GET /api/reports/{id}`). Storing only the PDF path would require the PDF
to exist on disk — if the disk is wiped, the report is lost. With HTML in the DB, a
report can be re-rendered to PDF at any time.

---

### `daily_pnl`

Tracks daily realised and unrealised P&L for the auto-trading safety system.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | Integer | Auto-increment PK |
| `date` | String(10) | `YYYY-MM-DD`, unique |
| `realized_usd` | Float | Sum of closed trade P&L today |
| `unrealized_usd` | Float | Mark-to-market of open positions |
| `auto_trading_halted` | Boolean | If true, all auto-mode `execute_trade` calls are blocked |
| `updated_at` | DateTime(tz) | `onupdate=_now` — tracks when P&L was last recalculated |

**How the halt works**: after each auto-mode sell trade, `_check_and_enforce_daily_limit()`
in the dispatcher updates `realized_usd` and sets `auto_trading_halted = True` if
`realized_usd < -AUTO_DAILY_LOSS_LIMIT_USD`. From that point, `_is_daily_halted()` blocks
every further `execute_trade` call for the rest of the calendar day. The flag is not reset
programmatically — each new day gets a fresh row, so the halt expires naturally at midnight.

The `auto_trading_halted` flag can also be set manually via a direct DB update if you want
to pause autonomous trading outside of a loss event.

---

### `news_articles`

The persistent news memory store.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `title` | String(500) | Article title (truncated at 500 chars) |
| `summary` | Text | Short excerpt (up to 2,000 chars from RSS/scraper) |
| `content` | Text | Full body text (up to 5,000 chars from Guardian API) |
| `source` | String(100) | Source name, indexed |
| `url` | String(1000) | Unique — deduplication key |
| `published_at` | DateTime(tz) | Publication date (nullable — some sources omit it) |
| `fetched_at` | DateTime(tz) | When the article was ingested; server default `now()` |
| `sentiment_label` | String(20) | `bullish`, `bearish`, or `neutral` |
| `sentiment_score` | Float | -1.0 to +1.0 |
| `tags` | JSON | List of detected ticker symbols (e.g. `["AAPL", "NVDA"]`) |

**GIN index** on `to_tsvector('english', title || ' ' || summary || ' ' || content)` enables
fast full-text search. Without it, `search_news()` would need to scan every row.

**`url` as dedup key**: the `url` unique constraint (with `ON CONFLICT DO NOTHING`) is
the primary deduplication mechanism. For email newsletters, a synthetic `email://` URL is
constructed from the Message-ID header, ensuring the same newsletter isn't ingested twice
even across multiple Saturday runs.

**`published_at` vs `fetched_at`**: `published_at` comes from the RSS feed's `<pubDate>`
or the Guardian's `webPublicationDate`. It can be null (some feeds don't include it) or
in the past (fetched later). The search function uses both:

- For recency filtering, it checks `published_at >= since OR (published_at IS NULL AND fetched_at >= since)`
- For ordering recent headlines, it orders by `fetched_at DESC`

---

### `simulation_results`

One row per completed backtest.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | UUID | Primary key |
| `name` | String(256) | Human-readable simulation name |
| `strategy` | JSON | `{"type": "...", "params": {...}}` |
| `initial_capital` | Float | Starting USD |
| `final_value` | Float | Ending portfolio value |
| `total_return_pct` | Float | `(final - initial) / initial * 100` |
| `sharpe_ratio` | Float | Annualised Sharpe (no risk-free rate subtracted) |
| `max_drawdown_pct` | Float | Worst peak-to-trough as a percentage |
| `trades_count` | Integer | Number of trades executed in the simulation |
| `period_start` | String(10) | `YYYY-MM-DD` |
| `period_end` | String(10) | `YYYY-MM-DD` |
| `equity_curve` | JSON | Weekly resampled `[{"date": "...", "value": ...}]` |
| `created_at` | DateTime(tz) | — |

**Written by**: `_run_simulation_and_persist()` in the dispatcher wraps the synchronous
`run_simulation()` function and writes the result to this table after every successful
backtest. The `simulation_id` UUID is returned to the LLM as part of the tool result, so
the agent can reference past simulations by ID.

---

## Session factory pattern

```python
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
```

`expire_on_commit=False` means ORM objects remain accessible after the session commits.
Without this, accessing `report.id` after `await session.commit()` would trigger a lazy
load attempt, which fails in async context.

The `get_db()` FastAPI dependency yields a session with automatic commit on success and
rollback on exception. Inline tools that need DB access (like `_execute_trade`) use
`async with async_session() as session` directly — they don't use the FastAPI dependency
because they're called from within a tool dispatch, not from a route handler.
