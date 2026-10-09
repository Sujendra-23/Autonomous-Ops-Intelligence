"""Inbound webhook for the field-service connector.

Mounted at ``/webhooks/field-service`` (outside ``/api``): the provider cannot send our bearer
credentials, so the HMAC signature is the authentication. Fails closed when no secret is set.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import base_settings
from app.database import get_session
from app.integrations.field_service_webhook import apply_event, verify_signature

router = APIRouter()
MAX_BODY_BYTES = 1_000_000


@router.post("/field-service", summary="Field-service provider webhook")
async def field_service_webhook(
    request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    settings = base_settings()
    secret = settings.field_service_webhook_secret.get_secret_value()
    if not secret:
        return JSONResponse({"detail": "webhook not configured"}, status_code=503)
    if settings.auth_mode == "oidc":
        # Workspace routing for provider callbacks is not implemented; refuse rather than guess.
        return JSONResponse({"detail": "not available in multi-tenant mode"}, status_code=503)
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "payload too large"}, status_code=413)
    if not verify_signature(
        secret,
        body,
        request.headers.get("x-fs-timestamp"),
        request.headers.get("x-fs-signature"),
    ):
        return JSONResponse({"detail": "invalid signature"}, status_code=401)
    try:
        event = json.loads(body)
        if not isinstance(event, dict):
            raise ValueError
    except ValueError:
        return JSONResponse({"detail": "invalid JSON"}, status_code=400)
    return JSONResponse({"status": await apply_event(session, event)})
