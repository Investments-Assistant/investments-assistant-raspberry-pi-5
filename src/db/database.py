"""Async SQLAlchemy engine, session factory, and base class."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.is_development,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async DB session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_all_tables() -> None:
    """Create tables and apply small additive compatibility migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            # SQLAlchemy create_all deliberately does not alter existing
            # tables. These columns/indexes are additive and safe for the
            # already-deployed single-user schema.
            await conn.execute(
                text(
                    "ALTER TABLE chat_messages "
                    "ADD COLUMN IF NOT EXISTS user_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text("ALTER TABLE trades ADD COLUMN IF NOT EXISTS user_id VARCHAR(36)")
            )
            await conn.execute(
                text(
                    "ALTER TABLE trades ADD COLUMN IF NOT EXISTS "
                    "broker_account_id VARCHAR(36)"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                    "trading_mode VARCHAR(16) NOT NULL DEFAULT 'recommend'"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_chat_messages_user_session_created "
                    "ON chat_messages (user_id, session_id, created_at)"
                )
            )

    await _bootstrap_auth_user()


async def _bootstrap_auth_user() -> None:
    """Create the configured env account once, then migrate legacy chat rows."""
    if not settings.auth_username or not settings.auth_password_hash:
        return

    from src.db.models import ChatMessage, User

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.username == settings.auth_username)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                username=settings.auth_username,
                password_hash=settings.auth_password_hash,
                display_name=settings.auth_username,
                trading_mode=settings.trading_mode,
            )
            session.add(user)
            await session.flush()
        elif user.password_hash != settings.auth_password_hash:
            # The environment account is the bootstrap credential source. If
            # its hash is rotated in .env, make the DB login follow the new
            # value while leaving additional CLI-created users untouched.
            user.password_hash = settings.auth_password_hash
        # Existing single-user installations have NULL user_id values. Assign
        # those legacy rows only to the bootstrap account, never to later users.
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.user_id.is_(None))
            .values(user_id=user.id)
        )
        from src.db.models import Trade

        await session.execute(
            update(Trade).where(Trade.user_id.is_(None)).values(user_id=user.id)
        )
        await session.commit()
