"""Integration tests for ORM models — require a real PostgreSQL database.

Run with:
    pytest -m integration tests/integration/test_db_models.py
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from src.db.models import ChatMessage, DailyPnL, NewsArticle, Trade


@pytest.mark.integration
class TestChatMessageModel:
    async def test_insert_and_retrieve(self, db_session):
        # Arrange
        msg = ChatMessage(
            session_id="sess-1",
            role="user",
            content="Hello agent",
        )
        # Act
        db_session.add(msg)
        await db_session.flush()

        result = await db_session.execute(
            select(ChatMessage).where(ChatMessage.session_id == "sess-1")
        )
        rows = result.scalars().all()

        # Assert
        assert len(rows) == 1
        assert rows[0].content == "Hello agent"

    async def test_id_auto_generated(self, db_session):
        msg = ChatMessage(session_id="sess-2", role="assistant", content="Hi!")
        db_session.add(msg)
        await db_session.flush()

        assert msg.id is not None
        assert len(msg.id) == 36  # UUID format

    async def test_tool_calls_json_column(self, db_session):
        msg = ChatMessage(
            session_id="sess-3",
            role="tool",
            content="result",
            tool_calls={"name": "get_stock_data", "result": {"price": 150.0}},
        )
        db_session.add(msg)
        await db_session.flush()

        row = await db_session.get(ChatMessage, msg.id)
        assert row.tool_calls["name"] == "get_stock_data"


@pytest.mark.integration
class TestTradeModel:
    async def test_insert_and_retrieve(self, db_session):
        trade = Trade(
            broker="alpaca",
            symbol="AAPL",
            side="buy",
            quantity=10.0,
            order_type="market",
            status="filled",
            mode="auto",
        )
        db_session.add(trade)
        await db_session.flush()

        result = await db_session.execute(select(Trade).where(Trade.symbol == "AAPL"))
        rows = result.scalars().all()

        assert len(rows) == 1
        assert rows[0].broker == "alpaca"
        assert rows[0].quantity == 10.0

    async def test_optional_fields_nullable(self, db_session):
        trade = Trade(
            broker="ibkr",
            symbol="SPY",
            side="sell",
            quantity=5.0,
            order_type="limit",
            status="pending",
            mode="recommend",
        )
        db_session.add(trade)
        await db_session.flush()

        assert trade.price is None
        assert trade.pnl_usd is None
        assert trade.broker_order_id is None


@pytest.mark.integration
class TestNewsArticleModel:
    async def test_insert_and_retrieve(self, db_session):
        article = NewsArticle(
            title="Fed raises rates",
            summary="The Federal Reserve raised rates by 25bp.",
            source="Reuters",
            url="https://reuters.com/fed-rates-2024",
            sentiment_label="bearish",
            sentiment_score=-0.4,
            tags=["FED", "rates"],
        )
        db_session.add(article)
        await db_session.flush()

        result = await db_session.execute(
            select(NewsArticle).where(NewsArticle.source == "Reuters")
        )
        rows = result.scalars().all()

        assert len(rows) == 1
        assert rows[0].title == "Fed raises rates"
        assert rows[0].tags == ["FED", "rates"]

    async def test_url_uniqueness_constraint(self, db_session):
        """Inserting the same URL twice should raise an IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        url = "https://example.com/unique-test"
        a1 = NewsArticle(title="Art 1", source="X", url=url)
        a2 = NewsArticle(title="Art 2", source="Y", url=url)

        db_session.add(a1)
        await db_session.flush()

        db_session.add(a2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_full_text_search_index_usable(self, db_session):
        """Verify the GIN index doesn't prevent inserts and that plainto_tsquery works."""
        article = NewsArticle(
            title="ECB cuts interest rates",
            summary="European Central Bank reduces borrowing costs.",
            source="Guardian",
            url="https://theguardian.com/ecb-cut",
        )
        db_session.add(article)
        await db_session.flush()

        # Run a plain FTS query directly — if the index is corrupt the query would fail
        result = await db_session.execute(
            text(
                "SELECT id FROM news_articles "
                "WHERE to_tsvector('english', title || ' ' || summary) "
                "@@ plainto_tsquery('english', 'ECB interest')"
            )
        )
        ids = [row[0] for row in result.fetchall()]
        assert article.id in ids


@pytest.mark.integration
class TestDailyPnLModel:
    async def test_unique_date_constraint(self, db_session):
        from sqlalchemy.exc import IntegrityError

        pnl1 = DailyPnL(date="2024-01-01", realized_usd=100.0)
        pnl2 = DailyPnL(date="2024-01-01", realized_usd=200.0)

        db_session.add(pnl1)
        await db_session.flush()

        db_session.add(pnl2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_defaults(self, db_session):
        pnl = DailyPnL(date="2024-06-15")
        db_session.add(pnl)
        await db_session.flush()

        assert pnl.realized_usd == 0.0
        assert pnl.unrealized_usd == 0.0
        assert pnl.auto_trading_halted is False
