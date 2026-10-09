"""ASGI security boundary for all API routes (including WebSockets)."""

import json

from fastapi import HTTPException
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.auth import authenticate
from app.config import base_settings
from app.tenancy import principal_context, settings_context, workspace_context, workspace_settings

# Self-service endpoints that only ever act on the caller's own account, so a read-only
# workspace role must not stop someone from exercising their right to erasure.
SELF_SERVICE_PATHS = frozenset({"/api/account/data-deletion"})


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app
        self.redis = Redis.from_url(base_settings().redis_url)

    async def __call__(self, scope, receive, send):
        settings = base_settings()
        if (
            scope["type"] not in ("http", "websocket")
            or not scope["path"].startswith("/api/")
            or settings.auth_mode != "oidc"
        ):
            return await self.app(scope, receive, send)
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        if scope["type"] == "http" and scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        tokens = []
        lease_key = lease_value = None
        started = False
        original_send = send

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ],
                }
            await original_send(message)

        send = secured_send
        try:
            bearer = headers.get("authorization", "")
            credential = (
                bearer[7:] if bearer.startswith("Bearer ") else headers.get("x-api-key", "")
            )
            if scope["type"] == "websocket":
                import asyncio

                await receive()  # websocket.connect
                await send({"type": "websocket.accept"})
                message = await asyncio.wait_for(receive(), timeout=5)
                raw = message.get("text", "")
                if len(raw) > 8192:
                    raise HTTPException(401, "Invalid authentication frame")
                frame = json.loads(raw)
                if frame.get("type") != "auth":
                    raise HTTPException(401, "Authentication required")
                credential = frame.get("token", "")
            if not credential or len(credential) > 8192:
                raise HTTPException(401, "Authentication required")
            principal = await authenticate(credential, headers.get("x-workspace-id"))
            if credential.startswith("aoi_") and scope["path"].startswith("/api/account/"):
                raise HTTPException(403, "Sign in to manage workspace accounts and credentials")
            if (
                principal.role == "viewer"
                and scope["path"] not in SELF_SERVICE_PATHS
                and (scope["type"] == "websocket" or scope["method"] not in ("GET", "HEAD"))
            ):
                raise HTTPException(403, "Read-only workspace membership")
            # Atomic fixed-window counter; outage fails closed before paid work runs.
            key = f"aoi:rate:{principal.workspace_id}:{principal.account_id}"
            count = await self.redis.eval(
                "local n=redis.call('INCR',KEYS[1]); "
                "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
                1,
                key,
            )
            if count > settings.request_limit_per_minute:
                raise HTTPException(429, "Request limit reached; retry in one minute")
            if (
                scope["type"] == "http"
                and scope["method"] == "POST"
                and any(
                    scope["path"].startswith(path)
                    for path in ("/api/transcripts", "/api/live/sessions", "/api/intelligence")
                )
            ):
                from datetime import UTC, datetime

                day = datetime.now(UTC).date()
                for quota_key, maximum in (
                    (
                        f"aoi:quota:workspace:{principal.workspace_id}:{day}",
                        settings.paid_operations_per_day,
                    ),
                    (
                        f"aoi:quota:account:{principal.account_id}:{day}",
                        settings.paid_operations_per_day,
                    ),
                    (f"aoi:quota:platform:{day}", settings.platform_paid_operations_per_day),
                ):
                    attempts = await self.redis.eval(
                        "local n=redis.call('INCR',KEYS[1]); "
                        "if n==1 then redis.call('EXPIRE',KEYS[1],172800) end; return n",
                        1,
                        quota_key,
                    )
                    if attempts > maximum:
                        raise HTTPException(429, "Daily processing limit reached")
            tokens = [
                (workspace_context, workspace_context.set(principal.workspace_id)),
                (principal_context, principal_context.set(principal)),
                (
                    settings_context,
                    settings_context.set(workspace_settings(principal.connector_ciphertext)),
                ),
            ]
            if scope["type"] == "websocket":
                import secrets

                lease_key = f"aoi:live:{principal.workspace_id}:{scope['path']}"
                lease_value = secrets.token_hex(16)
                if not await self.redis.set(
                    lease_key, lease_value, nx=True, ex=settings.live_session_max_seconds + 60
                ):
                    lease_key = None
                    raise HTTPException(409, "Session already has an active connection")
                # Starlette expects a connect event and accepts again; swallow its accept.
                first = True

                async def ws_receive():
                    nonlocal first
                    if first:
                        first = False
                        return {"type": "websocket.connect"}
                    return await receive()

                async def ws_send(message):
                    if message["type"] != "websocket.accept":
                        await send(message)

                await self.app(scope, ws_receive, ws_send)
            else:
                total = 0

                async def limited_receive():
                    nonlocal total
                    message = await receive()
                    total += len(message.get("body", b""))
                    if total > settings.max_request_bytes:
                        raise HTTPException(413, "Request body exceeds upload limit")
                    return message

                length = headers.get("content-length", "0")
                if not length.isdigit() or int(length) > settings.max_request_bytes:
                    raise HTTPException(413, "Request body exceeds upload limit")
                await self.app(scope, limited_receive, send)
        except HTTPException as exc:
            if started:
                raise
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                body = json.dumps({"detail": exc.detail}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": exc.status_code,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
        except (TimeoutError, ValueError, TypeError, KeyError, RedisError):
            if started:
                raise
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({"type": "http.response.start", "status": 503, "headers": []})
                await send({"type": "http.response.body", "body": b"Authentication unavailable"})
        finally:
            if lease_key:
                try:
                    await self.redis.eval(
                        "if redis.call('GET',KEYS[1])==ARGV[1] then "
                        "return redis.call('DEL',KEYS[1]) end; return 0",
                        1,
                        lease_key,
                        lease_value,
                    )
                except RedisError:
                    pass  # Lease expires automatically.
            for variable, token in reversed(tokens):
                variable.reset(token)
