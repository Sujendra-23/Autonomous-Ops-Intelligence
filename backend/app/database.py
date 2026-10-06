"""Async SQLAlchemy engine, session factory, and FastAPI dependency."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, with_loader_criteria

from app.config import get_settings
from app.tenancy import workspace_context


class Base(DeclarativeBase):
    """Project-wide declarative base."""


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    hide_parameters=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

SessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an `AsyncSession`."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# Defense in depth: ORM scoping plus database-enforced policies for raw SQL.


@event.listens_for(Session, "after_begin")
def set_database_workspace(session, transaction, connection):
    from app.config import base_settings

    if connection.dialect.name == "postgresql":
        from app.tenancy import LEGACY_WORKSPACE

        workspace = (
            workspace_context.get() if base_settings().auth_mode == "oidc" else LEGACY_WORKSPACE
        )
        connection.execute(
            text("SELECT set_config('app.workspace_id', :workspace, true)"),
            {"workspace": str(workspace) if workspace else ""},
        )


@event.listens_for(Session, "do_orm_execute")
def scope_workspace_queries(state):
    from app.config import base_settings
    from app.models._mixins import TenantMixin

    if base_settings().auth_mode != "oidc":
        return
    workspace = workspace_context.get()
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantMixin, lambda model: model.workspace_id == workspace, include_aliases=True
            )
        )


@event.listens_for(Session, "before_flush")
def scope_workspace_writes(session, flush_context, instances):
    from app.config import base_settings
    from app.models._mixins import TenantMixin

    if base_settings().auth_mode != "oidc":
        return
    workspace = workspace_context.get()
    for row in session.new.union(session.dirty).union(session.deleted):
        if isinstance(row, TenantMixin):
            if workspace is None or (
                row.workspace_id is not None and row.workspace_id != workspace
            ):
                raise ValueError("Workspace boundary violation")
            row.workspace_id = workspace
