"""Verified OIDC identities and revocable, workspace-bound extension tokens."""

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import jwt
from fastapi import HTTPException
from sqlalchemy import select, text

from app.config import base_settings
from app.database import SessionLocal
from app.models.account import AccessToken, Account, Membership, Workspace


@dataclass(frozen=True)
class Principal:
    account_id: uuid.UUID
    workspace_id: uuid.UUID
    role: str
    connector_ciphertext: str | None


_jwks: dict = {}
_jwks_until = 0.0
_jwks_lock = asyncio.Lock()


async def verify_identity(token: str) -> str:
    global _jwks, _jwks_until
    settings = base_settings()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in ("RS256", "ES256") or not header.get("kid"):
            raise ValueError("Unsupported signing key")
        # Unknown keys cannot cause unbounded network calls; refresh at most once per minute.
        async with _jwks_lock:
            if time.monotonic() >= _jwks_until:
                async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                    response = await client.get(settings.auth_jwks_url)
                    response.raise_for_status()
                    if len(response.content) > 131072:
                        raise ValueError("Oversized key set")
                    keys = response.json()["keys"]
                _jwks = {key["kid"]: key for key in keys if key.get("use", "sig") == "sig"}
                _jwks_until = time.monotonic() + 60
        key_data = _jwks[header["kid"]]
        key = jwt.PyJWK.from_dict(key_data, algorithm=header["alg"]).key
        claims = jwt.decode(
            token,
            key,
            algorithms=[header["alg"]],
            issuer=settings.auth_issuer,
            audience=settings.auth_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject or len(subject) > 512:
            raise ValueError("Invalid subject")
        return subject
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, httpx.HTTPError):
        raise HTTPException(401, "Invalid or expired access token") from None


async def authenticate(token: str, workspace_header: str | None = None) -> Principal:
    try:
        requested = uuid.UUID(workspace_header) if workspace_header else None
    except ValueError:
        raise HTTPException(400, "Invalid workspace ID") from None
    async with SessionLocal() as db:
        if token.startswith("aoi_"):
            access = await db.scalar(
                select(AccessToken).where(
                    AccessToken.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                    AccessToken.revoked_at.is_(None),
                    AccessToken.expires_at > datetime.now(UTC),
                )
            )
            if access is None:
                raise HTTPException(401, "Invalid or expired access token")
            if requested and requested != access.workspace_id:
                raise HTTPException(403, "Token is bound to another workspace")
            account_id, requested = access.account_id, access.workspace_id
        else:
            subject = await verify_identity(token)
            # Serializes first-login provisioning, including across API replicas.
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:subject, 0))"),
                {"subject": subject},
            )
            account = await db.scalar(select(Account).where(Account.subject == subject))
            if account is None:
                account = Account(subject=subject)
                workspace = Workspace(name="My workspace")
                db.add_all([account, workspace])
                await db.flush()
                db.add(Membership(account_id=account.id, workspace_id=workspace.id, role="owner"))
                await db.commit()
            account_id = account.id
        query = (
            select(Membership, Workspace)
            .join(Workspace, Membership.workspace_id == Workspace.id)
            .where(Membership.account_id == account_id)
        )
        if requested:
            query = query.where(Membership.workspace_id == requested)
        row = (await db.execute(query.order_by(Workspace.created_at).limit(1))).first()
        if row is None:
            raise HTTPException(403, "Workspace membership required")
        member, workspace = row
        return Principal(account_id, workspace.id, member.role, workspace.connector_ciphertext)
