"""Shared pytest fixtures.

Tests are split into two tiers:

- *unit* tests (chunker, prompt parsing, slugify…) need no infrastructure.
- *integration* tests need the real Postgres+pgvector. They are skipped
  automatically when the database isn't reachable, which keeps `pytest` fast
  during local development.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.database import Base
from app import models  # noqa: F401


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL", get_settings().database_url
    )


async def _database_available(url: str) -> bool:
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest_asyncio.fixture(scope="session")
async def db_available() -> bool:
    return await _database_available(_database_url())


@pytest_asyncio.fixture
async def db_session(db_available) -> AsyncSession:
    if not db_available:
        pytest.skip("Postgres + pgvector not reachable; integration test skipped")

    url = _database_url()
    engine = create_async_engine(url, pool_pre_ping=True)

    # Each test gets a fresh schema in an isolated transaction so they can run
    # in any order without bleeding state.
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()
