"""Unit tests for src/news/email_reader.py."""

from __future__ import annotations

import email as email_lib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.news.email_reader import (
    _build_search_criteria,
    _decode_header_value,
    _extract_body,
    _strip_html,
    read_and_ingest_newsletters,
)

# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStripHtml:
    def test_removes_html_tags(self):
        # Arrange
        html = "<h1>Hello</h1><p>World</p>"
        # Act
        result = _strip_html(html)
        # Assert
        assert "<" not in result
        assert "Hello" in result
        assert "World" in result

    def test_collapses_whitespace(self):
        html = "<p>  lots   of   spaces  </p>"
        result = _strip_html(html)
        assert "  " not in result

    def test_empty_string_returns_empty(self):
        assert _strip_html("") == ""

    def test_plain_text_unchanged(self):
        result = _strip_html("no tags here")
        assert result == "no tags here"

    def test_nested_tags_stripped(self):
        html = "<div><span><b>bold</b></span></div>"
        result = _strip_html(html)
        assert result == "bold"


# ---------------------------------------------------------------------------
# _decode_header_value
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecodeHeaderValue:
    def test_plain_ascii_returned_as_is(self):
        assert _decode_header_value("Hello World") == "Hello World"

    def test_encoded_utf8_decoded(self):
        # RFC2047 encoded word: "=?utf-8?q?Test?="
        encoded = "=?utf-8?q?Investimento?="
        result = _decode_header_value(encoded)
        assert result == "Investimento"

    def test_empty_string(self):
        assert _decode_header_value("") == ""

    def test_multiple_parts_joined(self):
        # Some headers split across multiple encoded words
        result = _decode_header_value("Hello =?utf-8?q?World?=")
        assert "Hello" in result
        assert "World" in result


# ---------------------------------------------------------------------------
# _extract_body
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractBody:
    def _make_simple(
        self, text: str, content_type: str = "text/plain"
    ) -> email_lib.message.Message:
        msg = email_lib.message_from_string(
            f"Content-Type: {content_type}; charset=utf-8\n\n{text}"
        )
        return msg

    def _make_multipart(self, plain: str, html: str) -> MIMEMultipart:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        return msg

    def test_extracts_plain_text_body(self):
        # Arrange
        msg = self._make_simple("Investment update: markets up 5%")
        # Act
        body = _extract_body(msg)
        # Assert
        assert "Investment update" in body

    def test_extracts_html_as_stripped_text(self):
        msg = self._make_simple("<p>Markets are <b>bullish</b> today</p>", "text/html")
        body = _extract_body(msg)
        assert "Markets are" in body
        assert "<" not in body

    def test_multipart_prefers_plain_over_html(self):
        msg = self._make_multipart(
            plain="Plain text content",
            html="<p>HTML content</p>",
        )
        body = _extract_body(msg)
        assert "Plain text content" in body

    def test_multipart_falls_back_to_html_when_no_plain(self):
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText("<p>Only HTML</p>", "html", "utf-8"))
        body = _extract_body(msg)
        assert "Only HTML" in body

    def test_empty_payload_returns_empty(self):
        msg = email_lib.message_from_string("Content-Type: text/plain\n\n")
        body = _extract_body(msg)
        assert body == ""


# ---------------------------------------------------------------------------
# _build_search_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSearchCriteria:
    def test_without_sender_filter(self, force_development_env):
        # Arrange
        force_development_env.newsletter_sender_filter = ""
        # Act
        criteria = _build_search_criteria(since_days=7)
        # Assert
        assert "SINCE" in criteria
        assert "FROM" not in criteria

    def test_with_sender_filter(self, force_development_env):
        force_development_env.newsletter_sender_filter = "news@example.com"
        criteria = _build_search_criteria(since_days=7)
        assert "FROM" in criteria
        assert "news@example.com" in criteria
        assert "SINCE" in criteria


# ---------------------------------------------------------------------------
# read_and_ingest_newsletters (async)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadAndIngestNewsletters:
    async def test_returns_zero_when_no_credentials(self, force_development_env):
        # Arrange — no credentials configured
        force_development_env.newsletter_email_user = ""
        force_development_env.newsletter_email_password = ""
        # Act
        result = await read_and_ingest_newsletters()
        # Assert
        assert result == {"fetched": 0, "inserted": 0}

    async def test_imap_login_failure_returns_zero(self, force_development_env):
        force_development_env.newsletter_email_user = "user@gmail.com"
        force_development_env.newsletter_email_password = "wrong"

        with patch("src.news.email_reader.imaplib.IMAP4_SSL") as mock_imap:
            mock_imap.side_effect = Exception("auth failed")
            result = await read_and_ingest_newsletters()

        assert result == {"fetched": 0, "inserted": 0}

    async def test_parses_and_ingests_matching_email(self, force_development_env):
        force_development_env.newsletter_email_user = "user@gmail.com"
        force_development_env.newsletter_email_password = "secret"
        force_development_env.newsletter_sender_filter = "news@letter.com"

        # Build a real email message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Weekly Investment Digest"
        msg["From"] = "news@letter.com"
        msg["Message-ID"] = "<abc123@letter.com>"
        msg.attach(MIMEText("Market recap: S&P up 2% this week.", "plain", "utf-8"))
        raw_bytes = msg.as_bytes()

        mock_conn = MagicMock()
        mock_conn.select = MagicMock()
        mock_conn.search = MagicMock(return_value=(None, [b"1"]))
        mock_conn.fetch = MagicMock(return_value=(None, [(None, raw_bytes)]))
        mock_conn.logout = MagicMock()

        with patch("src.news.email_reader.imaplib.IMAP4_SSL", return_value=mock_conn):
            with patch("src.news.email_reader.ingest_articles", new=AsyncMock(return_value=1)):
                result = await read_and_ingest_newsletters()

        assert result["fetched"] == 1
        assert result["inserted"] == 1
