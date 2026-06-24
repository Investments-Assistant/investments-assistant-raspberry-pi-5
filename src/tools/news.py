"""News aggregation and sentiment analysis tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re

import feedparser
from newsapi import NewsApiClient

from src.agent.utils.logger import get_logger
from src.config import settings

logger = get_logger(__name__)

# Financial RSS feeds (always available, no API key required)
RSS_FEEDS = {
    "Reuters Business": "https://feeds.reuters.com/reuters/businessNews",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Seeking Alpha": "https://seekingalpha.com/market_currents.xml",
    "Yahoo Finance": "https://finance.yahoo.com/rss/topstories",
    "Coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CryptoNews": "https://cryptonews.com/news/feed/",
}

# Simple keyword-based sentiment lexicon
_POSITIVE_WORDS = {
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
_NEGATIVE_WORDS = {
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


def _simple_sentiment(text: str) -> dict:
    """Score sentiment based on keyword presence. Returns score -1..+1."""
    words = set(re.findall(r"\b\w+\b", text.lower()))
    pos = len(words & _POSITIVE_WORDS)
    neg = len(words & _NEGATIVE_WORDS)
    total = pos + neg
    if total == 0:
        return {"label": "neutral", "score": 0.0, "positive": 0, "negative": 0}
    score = (pos - neg) / total
    if score > 0.15:
        label = "bullish"
    elif score < -0.15:
        label = "bearish"
    else:
        label = "neutral"
    return {"label": label, "score": round(score, 3), "positive": pos, "negative": neg}


def _rss_entries(source: str, url: str):
    """Yield (source, entry) pairs from a single RSS feed, suppressing fetch errors."""
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            yield source, entry
    except Exception as exc:
        logger.debug("RSS feed %s failed: %s", source, exc)


def _entry_to_article(entry, source: str, query_words: set[str]) -> dict | None:
    """Return an article dict if the entry matches the query, else None."""
    title = entry.get("title", "")
    summary = entry.get("summary", "")
    text = f"{title} {summary}".lower()
    if query_words and not any(w in text for w in query_words):
        return None
    summary_text = summary[:400] if summary else ""
    return {
        "title": title,
        "summary": summary_text,
        "source": source,
        "url": entry.get("link", ""),
        "published_at": entry.get("published", ""),
        "sentiment": _simple_sentiment(f"{title} {summary}"),
    }


def _fetch_rss(query: str, max_articles: int) -> list[dict]:
    """Fetch articles from RSS feeds matching the query."""
    query_words = set(re.findall(r"\b\w+\b", query.lower()))
    articles = []
    for source, url in RSS_FEEDS.items():
        for src, entry in _rss_entries(source, url):
            article = _entry_to_article(entry, src, query_words)
            if article:
                articles.append(article)
            if len(articles) >= max_articles * 3:
                break
    return articles[:max_articles]


def _fetch_newsapi(query: str, max_articles: int) -> list[dict]:
    """Fetch articles from NewsAPI (requires API key)."""
    if not settings.newsapi_key:
        return []
    try:
        client = NewsApiClient(api_key=settings.newsapi_key)
        from_date = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
        resp = client.get_everything(
            q=query,
            language="en",
            sort_by="publishedAt",
            page_size=min(max_articles, 20),
            from_param=from_date,
        )
        articles = []
        for art in resp.get("articles", []):
            title = art.get("title", "")
            description = art.get("description", "")
            content = art.get("content", "")
            full_text = f"{title} {description} {content}"
            articles.append(
                {
                    "title": title,
                    "summary": description or content[:400] if content else "",
                    "source": art.get("source", {}).get("name", ""),
                    "url": art.get("url", ""),
                    "published_at": art.get("publishedAt", ""),
                    "sentiment": _simple_sentiment(full_text),
                }
            )
        return articles
    except Exception as exc:
        logger.warning("NewsAPI failed: %s", exc)
        return []


def search_market_news(
    query: str,
    max_articles: int = 10,
    sources: list[str] | None = None,
) -> dict:
    """Search financial news and return articles with sentiment."""
    max_articles = min(max(1, max_articles), 20)

    # Try NewsAPI first; fall back to RSS
    articles = _fetch_newsapi(query, max_articles)
    if not articles:
        articles = _fetch_rss(query, max_articles)

    # Filter by source if requested
    if sources:
        src_lower = {s.lower() for s in sources}
        articles = [a for a in articles if any(s in a["source"].lower() for s in src_lower)]

    # Aggregate sentiment
    sentiments = [a["sentiment"]["label"] for a in articles]
    overall = max(set(sentiments), key=sentiments.count) if sentiments else "neutral"
    avg_score = (
        round(sum(a["sentiment"]["score"] for a in articles) / len(articles), 3)
        if articles
        else 0.0
    )

    return {
        "query": query,
        "articles_found": len(articles),
        "overall_sentiment": overall,
        "avg_sentiment_score": avg_score,
        "articles": articles,
    }
