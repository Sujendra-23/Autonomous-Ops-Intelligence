"""Adapter for Sujendra-23/Multi-Tenant-Identity-Access-Gateway.

Independently verify Ed25519 tokens, then check the gateway's live session and
policy version on every request. Never authorize from X-Gateway-* headers.
"""

import re
import uuid
from dataclasses import dataclass

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text

from app.config import base_settings
from app.models.account import Account, Membership, Workspace


@dataclass(frozen=True)
class GatewayIdentity:
    subject: str
    tenant_id: uuid.UUID
    tenant_slug: str
    role: str
    expires_at: int


async def gateway_request(path: str, *, tenant: str, token: str = "", payload=None):
    settings = base_settings()
    headers = {"X-Tenant": tenant}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
            response = await client.request(
                "GET" if payload is None else "POST",
                f"{settings.identity_gateway_url.rstrip('/')}{path}",
                headers=headers,
                json=payload,
            )
        if response.status_code in (400, 401, 403, 404):
            raise HTTPException(401, "Company sign-in or session is no longer valid")
        if response.status_code == 429:
            raise HTTPException(429, "Company sign-in limit reached. Try again shortly.")
        if not response.is_success or len(response.content) > 32768:
            raise HTTPException(503, "Company identity service is unavailable")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid identity response")
        return data
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "Company identity service is unavailable") from None


async def verify_gateway_identity(token: str) -> GatewayIdentity:
    from app.auth import verify_claims

    claims = await verify_claims(token, ("EdDSA",))
    try:
        tenant_id = uuid.UUID(claims["tid"])
        subject = str(uuid.UUID(claims["sub"]))
        slug = claims["tsl"]
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}", slug):
            raise ValueError("Invalid company")
        if tenant_id.int == 0:
            raise ValueError("Invalid company")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(401, "Invalid company identity") from None
    # This also enforces tenant resolution, revocation, suspension, and current
    # policy version in the actual gateway, rather than trusting JWT role hints.
    current = await gateway_request("/v1/auth/me", tenant=slug, token=token)
    if current.get("user_id") != subject or current.get("tenant") != slug:
        raise HTTPException(401, "Company identity mismatch")
    roles = current.get("roles", [])
    if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
        raise HTTPException(401, "Invalid company roles")
    # The gateway's bundled member role is explicitly READ ONLY. Never map it
    # to AOI's writable member role. Unknown/custom roles fail closed.
    if "owner" in roles:
        role = "owner"
    elif "member" in roles:
        role = "viewer"
    else:
        raise HTTPException(403, "Your gateway role has no AOI access")
    return GatewayIdentity(subject, tenant_id, slug, role, int(claims["exp"]))


async def authenticate_gateway(token: str, requested: uuid.UUID | None = None):
    from app.auth import Principal, SessionLocal

    identity = await verify_gateway_identity(token)
    issuer = base_settings().auth_issuer
    subject = f"gateway|{issuer}|{identity.subject}"
    async with SessionLocal() as db:
        # Serialize tenant provisioning first, then identity provisioning, across
        # API replicas. The unique constraint is the final race safety boundary.
        for lock in (f"gateway-company|{issuer}|{identity.tenant_id}", subject):
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": lock}
            )
        workspace = await db.scalar(
            select(Workspace).where(
                Workspace.identity_issuer == issuer,
                Workspace.identity_tenant_id == identity.tenant_id,
            )
        )
        if workspace is None:
            workspace = Workspace(
                name=identity.tenant_slug,
                identity_issuer=issuer,
                identity_tenant_id=identity.tenant_id,
                identity_tenant_slug=identity.tenant_slug,
            )
            db.add(workspace)
            await db.flush()
        if requested is not None and requested != workspace.id:
            raise HTTPException(403, "Gateway session is bound to another company")
        account = await db.scalar(select(Account).where(Account.subject == subject))
        if account is None:
            account = Account(subject=subject)
            db.add(account)
            await db.flush()
        membership = await db.get(Membership, (account.id, workspace.id))
        if membership is None:
            db.add(Membership(account_id=account.id, workspace_id=workspace.id, role=identity.role))
        else:
            membership.role = identity.role
        workspace.identity_tenant_slug = identity.tenant_slug
        await db.commit()
        return Principal(account.id, workspace.id, identity.role, workspace.connector_ciphertext)
