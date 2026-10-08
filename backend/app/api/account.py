"""Workspace management, revocable tokens and encrypted connector configuration."""

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, base_settings
from app.database import get_session
from app.models.account import AccessToken, Account, Membership, Workspace
from app.tenancy import CONNECTOR_FIELDS, principal_context, workspace_settings

router = APIRouter()


def principal():
    value = principal_context.get()
    if value is None:
        raise HTTPException(503, "Account management requires OIDC authentication")
    return value


def administrator():
    value = principal()
    if value.role not in ("owner", "admin"):
        raise HTTPException(403, "Workspace administrator required")
    return value


@router.get("/me")
async def me(db: AsyncSession = Depends(get_session)):
    p = principal()
    rows = (
        await db.execute(
            select(Workspace.id, Workspace.name, Membership.role)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.account_id == p.account_id)
            .order_by(Workspace.created_at)
        )
    ).all()
    return {
        "account_id": str(p.account_id),
        "workspace_id": str(p.workspace_id),
        "role": p.role,
        "workspaces": [{"id": str(r.id), "name": r.name, "role": r.role} for r in rows],
    }


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"\S")


@router.post("/workspaces", status_code=201)
async def create_workspace(payload: WorkspaceCreate, db: AsyncSession = Depends(get_session)):
    p = principal()
    workspace = Workspace(name=payload.name.strip())
    db.add(workspace)
    await db.flush()
    db.add(Membership(account_id=p.account_id, workspace_id=workspace.id, role="owner"))
    await db.commit()
    return {"id": str(workspace.id), "name": workspace.name}


class MemberCreate(BaseModel):
    account_id: uuid.UUID
    role: str = Field(pattern="^(admin|member|viewer)$")


@router.get("/members")
async def members(db: AsyncSession = Depends(get_session)):
    p = administrator()
    rows = (
        await db.scalars(select(Membership).where(Membership.workspace_id == p.workspace_id))
    ).all()
    return [{"account_id": str(r.account_id), "role": r.role} for r in rows]


@router.put("/members")
async def add_member(payload: MemberCreate, db: AsyncSession = Depends(get_session)):
    p = administrator()
    if p.role != "owner" and payload.role == "admin":
        raise HTTPException(403, "Only owners can grant administrator access")
    if await db.get(Account, payload.account_id) is None:
        raise HTTPException(404, "Account not found; recipient must sign in first")
    member = await db.get(Membership, (payload.account_id, p.workspace_id))
    if member and (member.role == "owner" or (p.role != "owner" and member.role == "admin")):
        raise HTTPException(403, "Cannot change this membership")
    if member:
        member.role = payload.role
    else:
        db.add(
            Membership(
                account_id=payload.account_id, workspace_id=p.workspace_id, role=payload.role
            )
        )
    await db.commit()
    return {"status": "saved"}


@router.delete("/members/{account_id}")
async def remove_member(account_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    p = administrator()
    member = await db.get(Membership, (account_id, p.workspace_id))
    if member is None:
        raise HTTPException(404, "Member not found")
    if member.role == "owner" or (p.role != "owner" and member.role == "admin"):
        raise HTTPException(403, "Cannot remove this membership")
    await db.delete(member)
    await db.commit()
    return {"status": "removed"}


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_in_days: int = Field(default=30, ge=1, le=90)


@router.post("/tokens", status_code=201)
async def create_token(payload: TokenCreate, db: AsyncSession = Depends(get_session)):
    p = principal()
    token = "aoi_" + secrets.token_urlsafe(32)
    row = AccessToken(
        account_id=p.account_id,
        workspace_id=p.workspace_id,
        name=payload.name,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    db.add(row)
    await db.commit()
    return {"id": str(row.id), "token": token, "expires_at": row.expires_at}


@router.get("/tokens")
async def list_tokens(db: AsyncSession = Depends(get_session)):
    p = principal()
    rows = (
        await db.scalars(
            select(AccessToken).where(
                AccessToken.account_id == p.account_id, AccessToken.workspace_id == p.workspace_id
            )
        )
    ).all()
    return [
        {"id": str(r.id), "name": r.name, "expires_at": r.expires_at, "revoked_at": r.revoked_at}
        for r in rows
    ]


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    p = principal()
    row = await db.scalar(
        select(AccessToken).where(
            AccessToken.id == token_id,
            AccessToken.account_id == p.account_id,
            AccessToken.workspace_id == p.workspace_id,
        )
    )
    if row is None:
        raise HTTPException(404, "Token not found")
    row.revoked_at = datetime.now(UTC)
    await db.commit()
    return {"status": "revoked"}


class ConnectorUpdate(BaseModel):
    values: dict[str, str | bool | list[str] | dict[str, str]]


@router.put("/connectors")
async def update_connectors(payload: ConnectorUpdate, db: AsyncSession = Depends(get_session)):
    p = administrator()
    if len(json.dumps(payload.values)) > 32768:
        raise HTTPException(422, "Connector configuration is too large")
    if set(payload.values) - CONNECTOR_FIELDS:
        raise HTTPException(422, "Unsupported connector field")
    row = await db.get(Workspace, p.workspace_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, "Workspace not found")
    settings = workspace_settings(row.connector_ciphertext)
    values = settings.model_dump()
    values.update(payload.values)
    try:
        checked = Settings.model_validate(values)
    except ValidationError:
        # ValidationError can include submitted secret values; never return it.
        raise HTTPException(422, "Invalid connector configuration") from None
    from urllib.parse import urlsplit

    for field, suffixes in (
        ("jira_base_url", (".atlassian.net",)),
        ("salesforce_instance_url", (".salesforce.com", ".force.com")),
        (
            "teams_webhook_url",
            (".logic.azure.com", ".webhook.office.com", ".environment.api.powerplatform.com"),
        ),
        ("webhook_url", ()),
    ):
        value = getattr(checked, field)
        url = value.get_secret_value() if hasattr(value, "get_secret_value") else value
        if not url:
            continue
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        trusted = (
            hostname in base_settings().connector_allowed_webhook_hosts.split(",")
            if field == "webhook_url"
            else any(hostname.endswith(suffix) for suffix in suffixes)
        )
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or not trusted
        ):
            raise HTTPException(422, "Connector destination is not an approved provider host")
    encoded = {
        field: (
            getattr(checked, field).get_secret_value()
            if hasattr(getattr(checked, field), "get_secret_value")
            else getattr(checked, field)
        )
        for field in CONNECTOR_FIELDS
    }
    row.connector_ciphertext = (
        Fernet(base_settings().connector_encryption_key.get_secret_value().encode())
        .encrypt(json.dumps(encoded).encode())
        .decode()
    )
    await db.commit()
    return {"status": "saved"}
