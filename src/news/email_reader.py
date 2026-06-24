"""IMAP newsletter reader.

Connects to your email account and ingests investment newsletters into the
news memory database, treated like any other article from a trusted source.

Setup (Gmail example):
1. In Gmail settings → Forwarding and POP/IMAP → enable IMAP.
2. If 2-Factor Auth is on, create an App Password:
   Google Account → Security → App Passwords → "Mail" → your device.
3. Set in .env:
       NEWSLETTER_IMAP_SERVER=imap.gmail.com
       NEWSLETTER_IMAP_PORT=993
       NEWSLETTER_EMAIL_USER=your@gmail.com
       NEWSLETTER_EMAIL_PASSWORD=xxxx xxxx xxxx xxxx   # 16-char app password
       NEWSLETTER_SENDER_FILTER=newsletter@example.com  # sender to watch for
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import email
from email.header import decode_header
from html.parser import HTMLParser
import imaplib
import re

from src.agent.utils.logger import get_logger
from src.config import settings
from src.news.ingestion import ingest_articles

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# HTML → plain text (no extra deps)
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return re.sub(r"\s+", " ", stripper.get_text()).strip()


# ---------------------------------------------------------------------------
# Header decoding
# ---------------------------------------------------------------------------


def _decode_header_value(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return "".join(decoded)


# ---------------------------------------------------------------------------
# Email body extraction
# ---------------------------------------------------------------------------


def _extract_body(msg: email.message.Message) -> str:
    """Return the plaintext body of an email (prefers text/plain over HTML)."""
    plain: list[str] = []
    html: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(charset, errors="replace")
            if ct == "text/plain":
                plain.append(text)
            elif ct == "text/html":
                html.append(_strip_html(text))
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        if payload:
            text = payload.decode(charset, errors="replace")
            ct = msg.get_content_type()
            if ct == "text/html":
                html.append(_strip_html(text))
            else:
                plain.append(text)

    return "\n\n".join(plain or html)


# ---------------------------------------------------------------------------
# IMAP search + ingestion
# ---------------------------------------------------------------------------


def _imap_connect() -> imaplib.IMAP4_SSL | None:
    if not settings.newsletter_email_user or not settings.newsletter_email_password:
        logger.debug("Newsletter IMAP credentials not configured — skipping")
        return None
    try:
        conn = imaplib.IMAP4_SSL(settings.newsletter_imap_server, settings.newsletter_imap_port)
        conn.login(settings.newsletter_email_user, settings.newsletter_email_password)
        return conn
    except Exception as exc:
        logger.warning("IMAP login failed: %s", exc)
        return None


def _build_search_criteria(since_days: int) -> str:
    since_date = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    criteria = f'(SINCE "{since_date}")'
    if settings.newsletter_sender_filter:
        criteria = f'(FROM "{settings.newsletter_sender_filter}" SINCE "{since_date}")'
    return criteria


async def read_and_ingest_newsletters(since_days: int = 8) -> dict:
    """Fetch unseen newsletters via IMAP and ingest them.

    By default looks back 8 days so a weekly Saturday newsletter is always
    caught even if the job fires a day late.

    Returns stats dict: {"fetched": N, "inserted": M}.
    """
    conn = _imap_connect()
    if conn is None:
        return {"fetched": 0, "inserted": 0}

    articles = []
    try:
        conn.select("INBOX")
        criteria = _build_search_criteria(since_days)
        _, msg_nums = conn.search(None, criteria)

        for num in (msg_nums[0] or b"").split():
            try:
                _, data = conn.fetch(num, "(RFC822)")
                raw = data[0][1] if data and data[0] else None
                if not raw:
                    continue
                msg = email.message_from_bytes(raw)
                subject = _decode_header_value(msg.get("Subject", "Newsletter"))
                sender = msg.get("From", "")
                body = _extract_body(msg)

                if not body.strip():
                    continue

                # Use a stable synthetic URL so dedup works across re-runs
                msg_id = msg.get("Message-ID", f"email-{num.decode()}")
                url = f"email://{settings.newsletter_email_user}/{msg_id.strip('<>')}"

                source_name = "Newsletter"
                if settings.newsletter_sender_filter:
                    domain = settings.newsletter_sender_filter.split("@")[-1]
                    source_name = f"Newsletter ({domain})"

                articles.append(
                    {
                        "title": subject[:500],
                        "summary": body[:2000],
                        "content": body[:10000],
                        "source": source_name,
                        "url": url,
                        "published_at": None,
                        "sentiment_label": "neutral",
                        "sentiment_score": 0.0,
                        "tags": [],
                    }
                )
                logger.info("Newsletter fetched: %s (from %s)", subject[:80], sender)
            except Exception as exc:
                logger.warning("Could not parse email %s: %s", num, exc)

    finally:
        try:
            conn.logout()
        except Exception:
            pass

    inserted = await ingest_articles(articles)
    logger.info("Newsletter ingestion: fetched=%d new=%d", len(articles), inserted)
    return {"fetched": len(articles), "inserted": inserted}
