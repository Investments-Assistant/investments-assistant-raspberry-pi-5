"""Integration test fixtures — require a running PostgreSQL instance.

Set DATABASE_URL in your environment (or .env.test) before running:
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost/test_investments pytest -m integration
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db.models import Base

# ---------------------------------------------------------------------------
# Engine / session scoped to the whole integration test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def integration_db_url() -> str:
    url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/test_investments",
    )
    return url


@pytest_asyncio.fixture(scope="session")
async def integration_engine(integration_db_url):
    """Create a single engine for the entire integration test session."""
    engine = create_async_engine(integration_db_url, echo=False, pool_pre_ping=True)
    # Create all tables once
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Drop all tables after the session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(integration_engine) -> AsyncSession:
    """Provide a transaction-rolled-back session for each test (keeps tests isolated)."""
    factory = async_sessionmaker(integration_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        async with session.begin():
            yield session
            # Roll back so each test starts with a clean slate
            await session.rollback()
