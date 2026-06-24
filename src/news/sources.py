"""News source adapters: RSS feeds, The Guardian API, and web scraping.

Sources are deliberately layered:
1. RSS    — structured, always available, no API key needed
2. Guardian API — full article text, free key (500 req/day)
3. Web scraping — fallback for open-access sites with no RSS/API

Financial Times and Bloomberg are paywalled; only their RSS headlines
are fetched (no scraping — would violate their ToS).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import Any

from bs4 import BeautifulSoup
import feedparser
import httpx

from src.agent.utils.logger import get_logger
from src.config import settings

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# RSS feed catalogue
# ---------------------------------------------------------------------------

RSS_FEEDS: dict[str, str] = {
    # ── Global financial & economic ──────────────────────────────────────────
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "Reuters Markets": "https://feeds.reuters.com/reuters/financialsNews",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
    "Yahoo Finance": "https://finance.yahoo.com/rss/topstories",
    "Bloomberg Markets": "https://feeds.bloomberg.com/markets/news.rss",
    "Financial Times": "https://www.ft.com/world?format=rss",
    "The Economist – Finance": "https://www.economist.com/finance-and-economics/rss.xml",
    "WSJ Markets": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "AP Business": "https://feeds.apnews.com/apf-business",
    "Investopedia": "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedType=rss",
    # ── Crypto ───────────────────────────────────────────────────────────────
    "Coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CryptoNews": "https://cryptonews.com/news/feed/",
    "The Block": "https://www.theblock.co/rss.xml",
    "Decrypt": "https://decrypt.co/feed",
    # ── European & Portuguese ────────────────────────────────────────────────
    "ECB Press": "https://www.ecb.europa.eu/rss/press.html",
    "Jornal de Negócios": "https://www.jornaldenegocios.pt/rss/",
    "Dinheiro Vivo": "https://www.dinheirovivo.pt/feed/",
    "ECO Portugal": "https://eco.pt/feed/",
    "Público Economia": "https://www.publico.pt/economia/rss",
}

# ---------------------------------------------------------------------------
# Sentiment lexicon (shared with live news tool)
# ---------------------------------------------------------------------------

_POSITIVE = {
    "surge",
    "rally",
    "gain",
    "rise",
    "soar",
    "boom",
    "bull",
    "strong",
    "beat",
    "record",
    "high",
    "growth",
    "profit",
    "upbeat",
    "upgrade",
    "buy",
    "outperform",
    "positive",
    "optimism",
    "recovery",
    "expansion",
}
_NEGATIVE = {
    "fall",
    "drop",
    "crash",
    "plunge",
    "decline",
    "loss",
    "bear",
    "weak",
    "miss",
    "low",
    "recession",
    "downgrade",
    "sell",
    "underperform",
    "negative",
    "concern",
    "risk",
    "fear",
    "inflation",
    "default",
    "bankruptcy",
    "layoff",
    "cut",
    "shrink",
}

# Common financial ticker symbols used to auto-tag articles
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
_KNOWN_TICKERS = {
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "TSLA",
    "META",
    "BRK",
    "SPY",
    "QQQ",
    "VTI",
    "GLD",
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "EUR",
    "USD",
    "GBP",
    "JPY",
    "CHF",
    "CAD",
}


def _sentiment(text: str) -> tuple[str, float]:
    words = set(re.findall(r"\b\w+\b", text.lower()))
    pos = len(words & _POSITIVE)
    neg = len(words & _NEGATIVE)
    total = pos + neg
    if total == 0:
        return "neutral", 0.0
    score = round((pos - neg) / total, 3)
    if score > 0.15:
        return "bullish", score
    if score < -0.15:
        return "bearish", score
    return "neutral", score


def _extract_tags(text: str) -> list[str]:
    """Return known ticker symbols found in the text."""
    candidates = set(_TICKER_RE.findall(text))
    return sorted(candidates & _KNOWN_TICKERS)


def _parse_date(value: str) -> datetime | None:
    """Best-effort ISO-8601 / RFC-2822 date parse."""
    if not value:
        return None
    import email.utils

    try:
        ts = email.utils.parsedate_to_datetime(value)
        return ts.astimezone(UTC)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value[:25], fmt)
            return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
        except ValueError:
            pass
    return None


def _article(
    title: str,
    summary: str,
    source: str,
    url: str,
    published_raw: str = "",
    content: str | None = None,
) -> dict[str, Any]:
    full_text = f"{title} {summary} {content or ''}"
    label, score = _sentiment(full_text)
    return {
        "title": title[:500],
        "summary": summary[:2000],
        "content": content,
        "source": source,
        "url": url,
        "published_at": _parse_date(published_raw),
        "sentiment_label": label,
        "sentiment_score": score,
        "tags": _extract_tags(full_text),
    }


# ---------------------------------------------------------------------------
# RSS adapter
# ---------------------------------------------------------------------------


def fetch_rss(max_per_feed: int = 20) -> list[dict[str, Any]]:
    """Fetch articles from all RSS feeds. Returns raw article dicts."""
    articles: list[dict[str, Any]] = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_per_feed]:
                link = getattr(entry, "link", "") or ""
                if not link:
                    continue
                title = getattr(entry, "title", "") or ""
                summary = getattr(entry, "summary", "") or ""
                published = getattr(entry, "published", "") or ""
                articles.append(_article(title, summary, source, link, published))
        except Exception as exc:
            logger.debug("RSS %s failed: %s", source, exc)
    return articles


# ---------------------------------------------------------------------------
# The Guardian API adapter
# ---------------------------------------------------------------------------

_GUARDIAN_BASE = "https://content.guardianapis.com/search"
_GUARDIAN_SECTIONS = [
    "business",
    "money",
    "technology",
    "world",
    "environment",
]


async def fetch_guardian(days_back: int = 1) -> list[dict[str, Any]]:
    """Fetch articles from The Guardian Content API (free tier: 500 req/day).

    Set GUARDIAN_API_KEY in .env — obtain at https://open-platform.theguardian.com/
    """
    if not settings.guardian_api_key:
        return []

    since = (datetime.now(UTC) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    articles: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=15) as client:
        for section in _GUARDIAN_SECTIONS:
            params = {
                "api-key": settings.guardian_api_key,
                "section": section,
                "from-date": since,
                "page-size": 50,
                "show-fields": "bodyText,trailText",
                "order-by": "newest",
            }
            try:
                resp = await client.get(_GUARDIAN_BASE, params=params)
                resp.raise_for_status()
                for item in resp.json().get("response", {}).get("results", []):
                    fields = item.get("fields", {})
                    body = fields.get("bodyText", "")
                    trail = fields.get("trailText", "")
                    articles.append(
                        _article(
                            title=item.get("webTitle", ""),
                            summary=trail or body[:500],
                            source="The Guardian",
                            url=item.get("webUrl", ""),
                            published_raw=item.get("webPublicationDate", ""),
                            content=body[:5000] if body else None,
                        )
                    )
            except Exception as exc:
                logger.warning("Guardian API section=%s failed: %s", section, exc)

    return articles


# ---------------------------------------------------------------------------
# Web scraper (open-access sites only)
# ---------------------------------------------------------------------------

# Each entry: (source_name, url, article_selector, title_selector, summary_selector)
_SCRAPE_TARGETS: list[tuple[str, str, str, str, str]] = [
    (
        "ECB Speeches",
        "https://www.ecb.europa.eu/press/key/html/index.en.html",
        "div.title",
        "a",
        "div.subtitle",
    ),
    (
        "Banco de Portugal",
        "https://www.bportugal.pt/en/publications/banco-de-portugal/all",
        "li.views-row",
        "h3 a",
        "div.field-body",
    ),
]


async def fetch_scraped(max_per_site: int = 10) -> list[dict[str, Any]]:
    """Scrape open-access financial sites that have no suitable RSS/API."""
    articles: list[dict[str, Any]] = []
    headers = {"User-Agent": "InvestmentAssistantBot/1.0 (research; non-commercial)"}

    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as client:
        for source, url, item_sel, title_sel, summary_sel in _SCRAPE_TARGETS:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                for i, item in enumerate(soup.select(item_sel)):
                    if i >= max_per_site:
                        break
                    title_el = item.select_one(title_sel)
                    summary_el = item.select_one(summary_sel)
                    if not title_el:
                        continue
                    title = title_el.get_text(strip=True)
                    link_el = title_el if title_el.name == "a" else title_el.find("a")
                    href = link_el["href"] if link_el and link_el.get("href") else url
                    if href.startswith("/"):
                        from urllib.parse import urlparse

                        base = urlparse(url)
                        href = f"{base.scheme}://{base.netloc}{href}"
                    summary = summary_el.get_text(strip=True)[:500] if summary_el else ""
                    articles.append(_article(title, summary, source, href))
            except Exception as exc:
                logger.debug("Scrape %s failed: %s", source, exc)

    return articles


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


async def fetch_all(days_back: int = 1) -> list[dict[str, Any]]:
    """Collect articles from all sources. Called by the ingestion scheduler."""
    results: list[dict[str, Any]] = []
    results.extend(fetch_rss())
    results.extend(await fetch_guardian(days_back=days_back))
    results.extend(await fetch_scraped())
    # Drop articles with no URL (unfilterable duplicates)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for a in results:
        u = a.get("url", "").strip()
        if u and u not in seen:
            seen.add(u)
            deduped.append(a)
    return deduped
