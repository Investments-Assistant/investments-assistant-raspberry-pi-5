# News Pipeline

The news system has two separate pipelines:

1. **Live search** (`src/tools/news.py`): on-demand fetch when the agent calls
   `search_market_news`. Results are not stored.

2. **News memory** (`src/news/`): a background pipeline that continuously ingests articles
   from multiple sources into PostgreSQL, enabling the agent to search historical context
   via `search_stored_news`.

---

## Why two pipelines?

The live tool is fast and always returns fresh results, but:
- It has no memory — the agent can't ask "what was the news about X last month?"
- RSS feeds return only the most recent 10–20 articles per feed
- NewsAPI has a rate limit and 7-day lookback on the free tier

The persistent memory pipeline runs on a schedule (every 30 minutes), accumulates a growing
corpus, and enables ranked full-text search over it. Together the two pipelines cover both
"what's happening right now" and "what happened historically".

---

## Source catalogue (`src/news/sources.py`)

The persistent pipeline pulls from 21 RSS feeds, The Guardian Content API, and optional
web scraping targets. The RSS catalogue was curated to cover:

### Global financial media
| Source | Why included |
|---|---|
| Reuters Business + Markets | Wire-service speed; no paywall on headlines |
| CNBC | High-volume, real-time market commentary |
| MarketWatch | Dow Jones affiliate; strong data journalism |
| Seeking Alpha | Retail investor analysis and earnings coverage |
| Yahoo Finance | Broad market news |
| Bloomberg Markets | Limited free RSS headlines; still useful for major moves |
| Financial Times | Premium financial journalism; paywalled but headlines are free |
| The Economist | Macro and geopolitical depth |
| WSJ Markets | Dow Jones / Wall Street Journal market feed |
| AP Business | Wire-service neutrality |
| Investopedia | Educational context for terminology |

### Crypto
| Source | Why included |
|---|---|
| Coindesk | Largest crypto news outlet |
| CryptoNews | Fast-moving altcoin coverage |
| The Block | Institutional-grade crypto analysis |
| Decrypt | Accessible crypto journalism |

### European and Portuguese
| Source | Why included |
|---|---|
| ECB Press | Official European Central Bank releases (rates, inflation, policy) |
| Jornal de Negócios | Leading Portuguese business newspaper |
| Dinheiro Vivo | Portuguese financial news (Expresso group) |
| ECO Portugal | Portuguese economics and finance |
| Público Economia | Portuguese broadsheet economics section |

The Portuguese sources were added because the user is based in Portugal and tracks
Portuguese/European economic developments alongside global markets.

---

## The Guardian API adapter

The Guardian offers a **free tier** (500 requests/day, no credit card required) with full
article body text via their Content API (https://open-platform.theguardian.com/).

Unlike RSS which gives only headlines and short summaries, the Guardian API returns the
full `bodyText` field — up to 5,000 characters stored in the `content` column of
`news_articles`. This is the only source that provides article body text; all other
sources have summaries only.

The pipeline fetches from five Guardian sections: `business`, `money`, `technology`,
`world`, `environment` — each up to 50 articles per run, looking back `days_back` days
(default: 1 day for scheduled runs).

**Configuration**: set `GUARDIAN_API_KEY` in `.env`. If the key is absent, the Guardian
adapter silently returns an empty list.

---

## Web scraping (`src/news/sources.py`)

Two open-access targets are scraped with `httpx` + `BeautifulSoup`:

| Source | URL | Why scraped |
|---|---|---|
| ECB Speeches | `ecb.europa.eu/press/key/html/index.en.html` | ECB president and board member speeches are market-moving events not in the press RSS |
| Banco de Portugal | `bportugal.pt/en/publications` | Portuguese central bank publications |

These sites have no RSS feed and no public API, so scraping is the only option. The
scraper uses a polite `User-Agent` string (`InvestmentAssistantBot/1.0`) and fetches at
most 10 items per site per 30-minute run.

**Note**: Financial Times and Bloomberg are paywalled. Their RSS feeds give only
headlines; scraping would violate their Terms of Service, so only RSS is used.

---

## Email newsletter ingestion (`src/news/email_reader.py`)

Many of the best investment newsletters are distributed via email (Substack, Morning Brew
Premium, FINVIZ Elite, etc.). The email reader connects via IMAP over SSL and ingests them.

**How it works**:
1. IMAP4_SSL connection to `NEWSLETTER_IMAP_SERVER` (default: `imap.gmail.com:993`)
2. `INBOX` is searched for messages matching `(FROM "filter@example.com" SINCE "dd-Mon-yyyy")`
3. Each email's body is extracted (prefers `text/plain`; falls back to HTML stripped via
   a custom `HTMLParser` — no `beautifulsoup4` dependency for the email parser)
4. A stable synthetic URL `email://{email_user}/{message_id}` is generated for deduplication
5. The article is ingested via `ingest_articles()` with `sentiment_label="neutral"`
   (sentiment analysis on email body would be expensive; the LLM can analyse the content
   itself)

**Gmail setup** (2FA required):
1. Settings → Forwarding and POP/IMAP → Enable IMAP
2. Google Account → Security → App Passwords → create a 16-char app password
3. Set `NEWSLETTER_EMAIL_PASSWORD` to the 16-char password (not your Google password)

**Run schedule**: every Saturday at 09:00 UTC (`src/scheduler/jobs.py`). `since_days=8`
ensures a weekly newsletter published on Saturday is always caught, even if the cron
fires a few hours late.

---

## Deduplication

All three source types (RSS, Guardian, scraper, email) write through `ingest_articles()`
which uses PostgreSQL's **ON CONFLICT DO NOTHING** on the `url` unique constraint:

```python
stmt = pg_insert(NewsArticle).values(rows).on_conflict_do_nothing(index_elements=["url"])
```

This is an upsert-style insert that silently skips duplicate URLs. Because every scheduled
run fetches the same RSS feeds, many articles would be re-fetched. Without this constraint,
the news table would balloon with duplicates.

Pre-deduplication also happens in `fetch_all()` using a Python `seen: set[str]` to
eliminate duplicates within a single ingestion run before they hit the database.

---

## Full-text search (`src/news/search.py`)

### Why PostgreSQL FTS?

Alternatives considered:
- **Elasticsearch/OpenSearch**: excellent for large-scale FTS but requires running a
  separate service (Java, 1+ GB RAM) — too heavy for the Pi.
- **pgvector + embeddings**: semantic search via embeddings would be more powerful but
  requires running an embedding model on the Pi, adding significant complexity.
- **SQLite FTS5**: works but lacks `ts_rank` and the SQLAlchemy async FTS syntax.

PostgreSQL's built-in FTS is a good fit: it's already running, uses minimal extra RAM,
and `ts_rank` gives relevance ranking that's sufficient for financial news queries.

### GIN index

The `news_articles` table has a GIN index on the tsvector:

```python
Index(
    "ix_news_articles_fts",
    func.to_tsvector(
        "english",
        func.coalesce("title", "") + " " + func.coalesce("summary", "") + " " + func.coalesce("content", "")
    ),
    postgresql_using="gin",
)
```

A GIN (Generalised Inverted Index) maps each lexeme to the list of rows containing it —
similar to a book index. Queries using `@@` on a tsvector column use this index and are
O(log n) rather than a full table scan. For a corpus of hundreds of thousands of articles,
this makes the difference between milliseconds and seconds.

### Search query

```python
ts_vector = func.to_tsvector("english", title + " " + summary + " " + content)
ts_query  = func.plainto_tsquery("english", query)
rank      = func.ts_rank(ts_vector, ts_query)

stmt = select(NewsArticle).where(ts_vector.op("@@")(ts_query)).order_by(rank.desc())
```

`plainto_tsquery` is used instead of `to_tsquery` because it handles natural language
input without requiring the user to use FTS operators (`&`, `|`, `!`).

### Filters

On top of FTS relevance, the search function supports:
- `days_back`: filters on `published_at >= now() - interval` (using `fetched_at` as
  fallback for articles with no publication date)
- `sources`: case-insensitive `LIKE '%source%'` match
- `sentiment`: exact match on `sentiment_label` column

---

## Sentiment scoring

Both the live tool and the persistent pipeline use the same keyword-based scorer. Each
article's title + summary + content is scored once at ingestion time and stored in
`sentiment_label` and `sentiment_score` columns.

The scorer is intentionally simple — a more sophisticated approach would run FinBERT
(a financial-domain BERT) on each article. FinBERT would give significantly better
sentiment accuracy, especially for subtle bearish signals ("concerns about slowing growth"
vs "strong growth"). This is a future improvement opportunity: the `sentiment_label`
column and the filter parameter in `search_news` are already in place; swapping in
FinBERT would require only changing the `_sentiment()` function in `sources.py`.
