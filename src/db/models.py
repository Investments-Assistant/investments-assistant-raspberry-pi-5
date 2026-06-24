"""SQLAlchemy ORM models."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db.database import Base


def _now() -> datetime:
    return datetime.now(UTC)


class ChatMessage(Base):
    """One turn in the chat conversation."""

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )


class Trade(Base):
    """A trade executed or recommended by the agent."""

    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    broker: Mapped[str] = mapped_column(String(32))  # alpaca | ibkr | coinbase | binance
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_type: Mapped[str] = mapped_column(String(16))  # market | limit | stop_limit
    status: Mapped[str] = mapped_column(String(16))  # pending | filled | cancelled | rejected
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str] = mapped_column(String(16))  # auto | manual | simulated
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Analysis(Base):
    """A market analysis snapshot produced by the agent."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trigger: Mapped[str] = mapped_column(String(32))  # scheduled | user_request | alert
    symbols: Mapped[list] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # bullish | bearish | neutral
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0–1
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    raw_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )


class Report(Base):
    """A weekly (or on-demand) investment report."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(256))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    html_content: Mapped[str] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    total_pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )


class DailyPnL(Base):
    """Daily profit-and-loss snapshot (used for auto-mode safety limits)."""

    __tablename__ = "daily_pnl"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), unique=True)  # YYYY-MM-DD
    realized_usd: Mapped[float] = mapped_column(Float, default=0.0)
    unrealized_usd: Mapped[float] = mapped_column(Float, default=0.0)
    auto_trading_halted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class NewsArticle(Base):
    """A news article ingested from RSS, API, web scraping, or email newsletter."""

    __tablename__ = "news_articles"
    __table_args__ = (
        # GIN index on the concatenated tsvector enables fast full-text search.
        Index(
            "ix_news_articles_fts",
            func.to_tsvector(
                "english",
                func.coalesce(func.cast("title", Text), "")
                + " "
                + func.coalesce(func.cast("summary", Text), "")
                + " "
                + func.coalesce(func.cast("content", Text), ""),
            ),
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)  # deduplication key
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
    sentiment_label: Mapped[str] = mapped_column(String(20), default="neutral")
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)
    tags: Mapped[list] = mapped_column(JSON, default=list)  # tickers / topics extracted


class SimulationResult(Base):
    """Results from a backtested or forward-simulated investment strategy."""

    __tablename__ = "simulation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(256))
    strategy: Mapped[dict] = mapped_column(JSON)
    initial_capital: Mapped[float] = mapped_column(Float)
    final_value: Mapped[float] = mapped_column(Float)
    total_return_pct: Mapped[float] = mapped_column(Float)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    period_start: Mapped[str] = mapped_column(String(10))
    period_end: Mapped[str] = mapped_column(String(10))
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)  # [{date, value}]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now()
    )
