"""Unit tests for src/news/sources.py — pure functions and mocked adapters."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.news.sources import (
    _article,
    _extract_tags,
    _parse_date,
    _sentiment,
    fetch_all,
    fetch_guardian,
    fetch_rss,
)

# ---------------------------------------------------------------------------
# _sentiment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSentiment:
    def test_positive_words_produce_bullish(self):
        # Arrange — title heavy with positive words
        text = "Markets surge rally gain boom record high profit"
        # Act
        label, score = _sentiment(text)
        # Assert
        assert label == "bullish"
        assert score > 0.15

    def test_negative_words_produce_bearish(self):
        text = "Crash plunge recession bankruptcy layoff default risk fear"
        label, score = _sentiment(text)
        assert label == "bearish"
        assert score < -0.15

    def test_no_sentiment_words_returns_neutral(self):
        text = "The company announced its quarterly results today"
        label, score = _sentiment(text)
        assert label == "neutral"
        assert score == 0.0

    def test_mixed_words_near_zero_returns_neutral(self):
        # Equal positive and negative → score = 0 → neutral
        text = "rally crash"
        label, score = _sentiment(text)
        assert label == "neutral"
        assert score == 0.0

    def test_score_is_bounded_minus_one_to_one(self):
        text = " ".join(["crash"] * 20)
        _, score = _sentiment(text)
        assert -1.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# _extract_tags
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractTags:
    def test_known_tickers_are_extracted(self):
        tags = _extract_tags("AAPL reports strong earnings; BTC hits 100k")
        assert "AAPL" in tags
        assert "BTC" in tags

    def test_unknown_symbols_are_excluded(self):
        tags = _extract_tags("The XYZ company reported losses today")
        assert "XYZ" not in tags

    def test_empty_text_returns_empty_list(self):
        assert _extract_tags("") == []

    def test_returns_sorted_list(self):
        tags = _extract_tags("ETH AAPL BTC SPY")
        assert tags == sorted(tags)

    def test_lowercase_input_not_matched(self):
        # Tickers are uppercase — lowercase words should not match
        tags = _extract_tags("aapl btc eth")
        assert tags == []


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseDate:
    def test_rfc2822_format(self):
        # Arrange — standard email/RSS date format
        result = _parse_date("Mon, 01 Jan 2024 12:00:00 +0000")
        assert result is not None
        assert result.year == 2024
        assert result.tzinfo is not None

    def test_iso8601_z_format(self):
        result = _parse_date("2024-06-15T09:30:00Z")
        assert result is not None
        assert result.month == 6
        assert result.day == 15

    def test_iso8601_offset_format(self):
        result = _parse_date("2024-06-15T09:30:00+01:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_date_only_format(self):
        result = _parse_date("2024-03-20")
        assert result is not None
        assert result.year == 2024
        assert result.month == 3

    def test_empty_string_returns_none(self):
        assert _parse_date("") is None

    def test_invalid_date_returns_none(self):
        assert _parse_date("not-a-date") is None

    def test_result_is_utc_aware(self):
        result = _parse_date("2024-01-01T00:00:00Z")
        assert result is not None
        assert result.tzinfo is not None


# ---------------------------------------------------------------------------
# _article
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestArticleFactory:
    def test_required_fields_present(self):
        # Arrange
        art = _article("Title", "Summary", "Reuters", "https://example.com/1")
        # Assert
        assert art["title"] == "Title"
        assert art["summary"] == "Summary"
        assert art["source"] == "Reuters"
        assert art["url"] == "https://example.com/1"

    def test_sentiment_computed_from_text(self):
        art = _article("Markets surge rally", "", "Reuters", "https://x.com/1")
        assert art["sentiment_label"] in ("bullish", "bearish", "neutral")
        assert isinstance(art["sentiment_score"], float)

    def test_title_truncated_to_500_chars(self):
        long_title = "x" * 600
        art = _article(long_title, "", "Reuters", "https://x.com")
        assert len(art["title"]) == 500

    def test_summary_truncated_to_2000_chars(self):
        long_summary = "y" * 3000
        art = _article("T", long_summary, "Reuters", "https://x.com")
        assert len(art["summary"]) == 2000

    def test_tags_list_returned(self):
        art = _article("AAPL earnings beat", "", "Reuters", "https://x.com")
        assert isinstance(art["tags"], list)
        assert "AAPL" in art["tags"]

    def test_published_at_parsed_from_raw(self):
        art = _article("T", "S", "R", "https://x.com", published_raw="2024-01-01")
        assert art["published_at"] is not None

    def test_content_stored_when_provided(self):
        art = _article("T", "S", "R", "https://x.com", content="Full body text")
        assert art["content"] == "Full body text"


# ---------------------------------------------------------------------------
# fetch_rss
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchRss:
    def test_returns_list_of_articles(self):
        # Arrange — mock feedparser to return two entries
        fake_entry = SimpleNamespace(
            title="Test headline",
            summary="Test summary",
            link="https://reuters.com/1",
            published="Mon, 01 Jan 2024 10:00:00 +0000",
        )
        fake_feed = SimpleNamespace(entries=[fake_entry])

        with patch("src.news.sources.feedparser.parse", return_value=fake_feed):
            # Act
            articles = fetch_rss(max_per_feed=5)
        # Assert
        assert isinstance(articles, list)
        assert len(articles) > 0
        assert all("url" in a for a in articles)

    def test_entry_without_link_is_skipped(self):
        fake_entry = SimpleNamespace(title="No link", summary="", link="", published="")
        fake_feed = SimpleNamespace(entries=[fake_entry])
        with patch("src.news.sources.feedparser.parse", return_value=fake_feed):
            articles = fetch_rss()
        assert all(a["url"] != "" for a in articles)

    def test_feed_failure_is_suppressed(self):
        with patch("src.news.sources.feedparser.parse", side_effect=Exception("network error")):
            # Act + Assert — should not raise
            articles = fetch_rss()
        assert isinstance(articles, list)


# ---------------------------------------------------------------------------
# fetch_guardian (async)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchGuardian:
    async def test_returns_empty_when_no_api_key(self, force_development_env):
        # Arrange — guardian_api_key is "" in the fixture
        force_development_env.guardian_api_key = ""
        # Act
        articles = await fetch_guardian()
        # Assert
        assert articles == []

    async def test_fetches_articles_with_valid_key(self, force_development_env):
        force_development_env.guardian_api_key = "test-key"

        fake_response = MagicMock()
        fake_response.raise_for_status = MagicMock()
        fake_response.json.return_value = {
            "response": {
                "results": [
                    {
                        "webTitle": "ECB raises rates",
                        "webUrl": "https://theguardian.com/1",
                        "webPublicationDate": "2024-01-15T10:00:00Z",
                        "fields": {"trailText": "Summary text", "bodyText": "Full body"},
                    }
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=fake_response)

        with patch("src.news.sources.httpx.AsyncClient", return_value=mock_client):
            articles = await fetch_guardian()

        assert len(articles) > 0
        assert articles[0]["source"] == "The Guardian"
        assert articles[0]["url"] == "https://theguardian.com/1"

    async def test_api_error_returns_partial_results(self, force_development_env):
        force_development_env.guardian_api_key = "test-key"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("timeout"))

        with patch("src.news.sources.httpx.AsyncClient", return_value=mock_client):
            articles = await fetch_guardian()

        assert isinstance(articles, list)  # No exception raised


# ---------------------------------------------------------------------------
# fetch_all (async)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFetchAll:
    async def test_deduplicates_by_url(self):
        # Arrange — two sources return the same URL
        duplicate = {
            "title": "T",
            "summary": "S",
            "source": "X",
            "url": "https://dup.com/1",
            "published_at": None,
            "sentiment_label": "neutral",
            "sentiment_score": 0.0,
            "tags": [],
        }
        with patch("src.news.sources.fetch_rss", return_value=[duplicate, duplicate]):
            with patch("src.news.sources.fetch_guardian", new=AsyncMock(return_value=[])):
                with patch("src.news.sources.fetch_scraped", new=AsyncMock(return_value=[])):
                    articles = await fetch_all()

        urls = [a["url"] for a in articles]
        assert len(urls) == len(set(urls))

    async def test_articles_without_url_are_dropped(self):
        no_url = {
            "title": "T",
            "summary": "S",
            "source": "X",
            "url": "",
            "published_at": None,
            "sentiment_label": "neutral",
            "sentiment_score": 0.0,
            "tags": [],
        }
        with patch("src.news.sources.fetch_rss", return_value=[no_url]):
            with patch("src.news.sources.fetch_guardian", new=AsyncMock(return_value=[])):
                with patch("src.news.sources.fetch_scraped", new=AsyncMock(return_value=[])):
                    articles = await fetch_all()

        assert all(a["url"] for a in articles)
