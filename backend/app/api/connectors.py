"""Authenticated OAuth initiation/polling and a one-use public provider callback."""

import json
import secrets
import uuid
from html import escape

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ingest_key
from app.config import Settings, base_settings, get_settings
from app.database import get_session
from app.integrations import oauth
from app.models.account import Membership, Workspace
from app.tenancy import LEGACY_WORKSPACE, principal_context, workspace_settings

router = APIRouter(dependencies=[Depends(require_ingest_key)])
callback_router = APIRouter()
TTL = 600


def identity(*, admin=False):
    p = principal_context.get()
    if base_settings().auth_mode == "oidc":
        if p is None:
            raise HTTPException(401, "Sign in to connect your tools")
        if admin and p.role not in {"owner", "admin"}:
            raise HTTPException(403, "A workspace administrator must connect this tool")
        return str(p.workspace_id), str(p.account_id)
    return str(LEGACY_WORKSPACE), "development"


def latest_key(workspace, provider):
    return f"aoi:oauth:latest:{workspace}:{provider}"


def apply_values(settings, values):
    return Settings.model_validate({**settings.model_dump(), **values})


def clear_values(settings, provider):
    return {
        field: Settings.model_fields[field].get_default(call_default_factory=True)
        for field in oauth.PROVIDER_FIELDS[provider]
    }


def public_entry(entry):
    return {
        "status": entry.get("status", "disconnected"),
        "destination": entry.get("destination"),
        "choices": [{"id": c["id"], "label": c["label"]} for c in entry.get("choices", [])],
    }


@router.get("")
async def list_connectors():
    _, _account = identity()
    s = get_settings()
    metadata = oauth.grants(s)
    return {
        provider: {
            "name": data[0],
            "oauth_available": oauth.available(provider),
            "setup_error": oauth.setup_error(provider),
            **(
                public_entry(metadata[provider])
                if provider in metadata
                else {
                    "status": "configured" if getattr(s, provider + "_enabled") else "disconnected",
                    "destination": None,
                    "choices": [],
                }
            ),
        }
        for provider, data in oauth.PROVIDERS.items()
    }


@router.post("/{provider}/start")
async def start_connector(provider: str, db: AsyncSession = Depends(get_session)):
    workspace, account = identity(admin=True)
    if provider not in oauth.PROVIDERS:
        raise HTTPException(404, "Connector not found")
    if error := oauth.setup_error(provider):
        raise HTTPException(503, error)
    if await db.get(Workspace, uuid.UUID(workspace)) is None:
        raise HTTPException(503, "Apply the workspace migration before connecting tools")
    state, ticket, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    payload = {
        "workspace": workspace,
        "account": account,
        "provider": provider,
        "ticket": ticket,
        "verifier": verifier,
    }
    auth_url = oauth.authorization(provider, state, verifier)
    try:
        async with Redis.from_url(base_settings().redis_url) as redis:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.set(
                    oauth.state_key(state),
                    oauth.cipher().encrypt(json.dumps(payload).encode()),
                    ex=TTL,
                )
                pipe.set(
                    oauth.ticket_key(ticket),
                    json.dumps(
                        {
                            "workspace": workspace,
                            "account": account,
                            "provider": provider,
                            "status": "pending",
                        }
                    ),
                    ex=TTL,
                )
                pipe.set(latest_key(workspace, provider), oauth.state_key(state), ex=TTL)
                await pipe.execute()
    except RedisError:
        raise HTTPException(503, "Connection service unavailable. Please try again.") from None
    return {"authorization_url": auth_url, "ticket": ticket, "expires_in": TTL}


@router.get("/attempts/{ticket}")
async def connection_attempt(ticket: str = Path(min_length=20, max_length=256)):
    workspace, account = identity()
    try:
        async with Redis.from_url(base_settings().redis_url) as redis:
            raw = await redis.get(oauth.ticket_key(ticket))
    except RedisError:
        raise HTTPException(503, "Connection service unavailable. Please try again.") from None
    if not raw:
        raise HTTPException(410, "Sign-in expired. Click Connect to start again.")
    result = json.loads(raw)
    if result["workspace"] != workspace or result["account"] != account:
        raise HTTPException(404, "Connection attempt not found")
    return {key: result[key] for key in ("provider", "status", "message") if key in result}


class Destination(BaseModel):
    id: str = Field(min_length=1, max_length=512)


@router.post("/{provider}/destination")
async def select_destination(
    provider: str, payload: Destination, db: AsyncSession = Depends(get_session)
):
    workspace, _account = identity(admin=True)
    if provider not in oauth.PROVIDERS:
        raise HTTPException(404, "Connector not found")
    row = await db.get(Workspace, uuid.UUID(workspace), with_for_update=True)
    if not row:
        raise HTTPException(404, "Workspace not found")
    settings = workspace_settings(row.connector_ciphertext)
    metadata = oauth.grants(settings)
    entry = metadata.get(provider, {})
    selected = next((c for c in entry.get("choices", []) if c["id"] == payload.id), None)
    if entry.get("status") != "select_destination" or not selected:
        raise HTTPException(
            409, "Destination is unavailable. Refresh the connectors and try again."
        )
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            values = selected["values"]
            if (provider == "jira" and not values.get("jira_project_key")) or (
                provider == "teams" and not values.get("teams_channel_id")
            ):
                values = await oauth.advance(client, provider, entry, values)
            else:
                entry.update(status="connected", destination=selected["label"], choices=[])
        settings = apply_values(settings, values)
        oauth.save_settings(row, settings, metadata)
        await db.commit()
    except oauth.OAuthError as exc:
        raise HTTPException(502, str(exc)) from None
    return public_entry(entry)


@router.delete("/{provider}")
async def disconnect(provider: str, db: AsyncSession = Depends(get_session)):
    workspace, _account = identity(admin=True)
    if provider not in oauth.PROVIDERS:
        raise HTTPException(404, "Connector not found")
    # Invalidate outstanding callbacks so an old tab cannot reconnect the tool.
    try:
        async with Redis.from_url(base_settings().redis_url) as redis:
            await redis.delete(latest_key(workspace, provider))
    except RedisError:
        raise HTTPException(503, "Connection service unavailable. Please try again.") from None
    row = await db.get(Workspace, uuid.UUID(workspace), with_for_update=True)
    if not row:
        raise HTTPException(404, "Workspace not found")
    settings = workspace_settings(row.connector_ciphertext)
    metadata = oauth.grants(settings)
    metadata.pop(provider, None)
    settings = apply_values(settings, clear_values(settings, provider))
    if provider in {"linear", "jira"} and not settings.linear_enabled and not settings.jira_enabled:
        settings.task_sync_enabled = False
    oauth.save_settings(row, settings, metadata)
    await db.commit()
    return {"status": "disconnected"}


def callback_page(message, status_code=200):
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Connector sign-in · Ops AI Agent</title><body><main><h1>Connector sign-in</h1><p>"
        + escape(message)
        + "</p><p>You can close this tab and return to Connectors.</p></main></body></html>",
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@callback_router.get("/{provider}/callback", response_class=HTMLResponse)
async def oauth_callback(
    provider: str,
    state: str = Query("", max_length=256),
    code: str = Query("", max_length=4096),
    error: str = Query("", max_length=512),
    db: AsyncSession = Depends(get_session),
):
    if provider not in oauth.PROVIDERS or not state:
        return callback_page("Invalid sign-in request. Start again from Connectors.", 400)
    try:
        async with Redis.from_url(base_settings().redis_url) as redis:
            # Validate the provider before consuming the one-use state.
            raw = await redis.get(oauth.state_key(state))
            if not raw:
                return callback_page(
                    "Sign-in expired or was already completed. Start again from Connectors.", 400
                )
            payload = json.loads(oauth.cipher().decrypt(raw))
            if payload["provider"] != provider:
                return callback_page("Invalid sign-in request. Start again from Connectors.", 400)
            raw = await redis.getdel(oauth.state_key(state))
            if not raw:
                return callback_page("Sign-in was already completed. Return to Connectors.", 400)
            workspace, account = payload["workspace"], payload["account"]
            result = {"workspace": workspace, "account": account, "provider": provider}
            try:
                if error or not code:
                    raise oauth.OAuthError("Sign-in was cancelled. Click Connect to try again.")
                if base_settings().auth_mode == "oidc":
                    membership = await db.get(
                        Membership, (uuid.UUID(account), uuid.UUID(workspace)), with_for_update=True
                    )
                    if not membership or membership.role not in {"owner", "admin"}:
                        raise oauth.OAuthError(
                            "Workspace administrator access is no longer available."
                        )
                # Lock before checking the current attempt; disconnect uses this lock too.
                row = await db.get(Workspace, uuid.UUID(workspace), with_for_update=True)
                current = await redis.get(latest_key(workspace, provider))
                if not row or current != oauth.state_key(state).encode():
                    raise oauth.OAuthError(
                        "This connection attempt was replaced or cancelled. "
                        "Start again from Connectors."
                    )
                async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                    body = await oauth.exchange(
                        client, provider, code=code, verifier=payload["verifier"]
                    )
                    entry = oauth.grant(body)
                    entry["choices"] = await oauth.destinations(
                        client, provider, entry["access_token"], body
                    )
                    values = await oauth.advance(client, provider, entry)
                settings = workspace_settings(row.connector_ciphertext)
                metadata = oauth.grants(settings)
                settings = apply_values(settings, {**clear_values(settings, provider), **values})
                metadata[provider] = entry
                oauth.save_settings(row, settings, metadata)
                # Check again after provider calls: disconnect/new attempts may have superseded us.
                current = await redis.get(latest_key(workspace, provider))
                if current != oauth.state_key(state).encode():
                    raise oauth.OAuthError(
                        "This connection attempt was cancelled. Start again from Connectors."
                    )
                await db.commit()
                result.update(
                    status=entry["status"],
                    message=(
                        f"{oauth.PROVIDERS[provider][0]} connected successfully."
                        if entry["status"] == "connected"
                        else "Sign-in complete. Return to Connectors to choose a destination."
                    ),
                )
            except Exception as exc:
                await db.rollback()
                message = (
                    str(exc)
                    if isinstance(exc, oauth.OAuthError)
                    else "Connection could not be saved. Please try again from Connectors."
                )
                result.update(status="failed", message=message)
            await redis.set(oauth.ticket_key(payload["ticket"]), json.dumps(result), ex=TTL)
            return callback_page(result["message"], 400 if result["status"] == "failed" else 200)
    except (RedisError, oauth.OAuthError, InvalidToken, ValueError):
        return callback_page("Sign-in service unavailable. Start again from Connectors.", 503)
