"""Cross-cutting FastAPI dependencies."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import get_settings
from app.tenancy import principal_context
import secrets


async def require_ingest_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if principal_context.get() is not None:
        return
    expected = get_settings().ingest_api_key.get_secret_value()
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-API-Key",
        )
