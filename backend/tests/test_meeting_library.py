"""Meeting-library search uses real PostgreSQL and disposable schemas."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.api.transcripts import router
from app.database import Base, get_session
from app.models.project import Project
from app.models.transcript import Transcript
from app.tenancy import workspace_context


@pytest.fixture
async def library(db_available):
    if not db_available:
        pytest.skip("Set TEST_DATABASE_URL to an isolated test database")
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with engine.connect() as connection:
        transaction = await connection.begin()
        schema = "library_test_" + uuid.uuid4().hex
        await connection.execute(text(f"CREATE SCHEMA {schema}"))
        await connection.execute(text(f"SET LOCAL search_path TO {schema}"))
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn, tables=[Project.__table__, Transcript.__table__]
            )
        )
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            app = FastAPI()
            app.include_router(router, prefix="/api/transcripts")
            app.dependency_overrides[get_session] = lambda: session
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield session, client
        await transaction.rollback()
    await engine.dispose()


async def test_title_content_pagination_and_totals(library):
    session, client = library
    session.add_all(
        [
            Transcript(
                title="Portal planning", content="Discuss launch scope.", status="completed"
            ),
            Transcript(title="Design sync", content="PORTAL launch planning.", status="completed"),
            Transcript(title="Other meeting", content="No matching topic.", status="completed"),
        ]
    )
    await session.flush()
    result = (await client.get("/api/transcripts", params={"q": " portal ", "limit": 1})).json()
    assert result["total"] == 2 and len(result["items"]) == 1
    next_page = (
        await client.get("/api/transcripts", params={"q": "portal", "limit": 1, "offset": 1})
    ).json()
    assert next_page["items"][0]["id"] != result["items"][0]["id"]


async def test_literal_wildcards_combined_filters_and_validation(library):
    session, client = library
    now = datetime.now(UTC)
    session.add_all(
        [
            Transcript(
                title="100%_ready", content="Current plan", status="completed", meeting_date=now
            ),
            Transcript(
                title="100XXready", content="Different plan", status="completed", meeting_date=now
            ),
            Transcript(
                title="Older plan",
                content="100%_ready",
                status="completed",
                meeting_date=now - timedelta(days=40),
            ),
            Transcript(title="Live plan", content="100%_ready", status="live", meeting_date=now),
        ]
    )
    await session.flush()
    result = (
        await client.get("/api/transcripts", params={"q": "%_", "status": "completed", "days": 7})
    ).json()
    assert result["total"] == 1 and result["items"][0]["title"] == "100%_ready"
    assert (await client.get("/api/transcripts", params={"q": "not found"})).json()["total"] == 0
    for params in ({"days": 0}, {"days": 366}, {"q": "x" * 201}, {"status": "invented"}):
        assert (await client.get("/api/transcripts", params=params)).status_code == 422


async def test_search_cannot_cross_workspace_context(library, monkeypatch):
    session, client = library
    first, other = uuid.uuid4(), uuid.uuid4()
    session.add_all(
        [
            Transcript(title="Shared keyword A", content="First company", workspace_id=first),
            Transcript(title="Shared keyword B", content="Other company", workspace_id=other),
        ]
    )
    await session.flush()
    monkeypatch.setattr("app.config.base_settings", lambda: SimpleNamespace(auth_mode="oidc"))
    token = workspace_context.set(first)
    try:
        result = (await client.get("/api/transcripts", params={"q": "Shared keyword"})).json()
        assert result["total"] == 1 and result["items"][0]["title"] == "Shared keyword A"
    finally:
        workspace_context.reset(token)
