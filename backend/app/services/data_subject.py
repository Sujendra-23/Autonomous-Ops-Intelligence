"""Data-subject export and erasure for a single account.

Customer data is keyed to *workspaces*, not to individual accounts: tasks, transcripts and the
rest carry a ``workspace_id`` but no author. That drives every decision below.

* A workspace the account is the only member of is the account's own data. It is exported in full
  and, on erasure, deleted together with its connector credentials.
* A workspace shared with other people belongs to its members collectively. Erasing one member
  removes that member's account, membership and tokens and leaves the workspace content alone.
  Content cannot be attributed to the leaving member, so nothing there is guessed at or
  anonymized.
* An owner cannot walk away from a shared workspace that would be left without an owner. The
  request is refused so the owner removes the other members first.

Everything here runs on the caller's session. Nothing commits, so the endpoint decides the
transaction boundary and a failure at any step leaves every row untouched.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import base_settings
from app.models.data_subject import DataSubjectAudit
from app.tenancy import LEGACY_WORKSPACE

CONFIRMATION_PHRASE = "DELETE MY ACCOUNT AND DATA"

# Workspace-keyed customer data, children first. Every table with a ``workspace_id`` column that
# holds customer content must be listed here; the coverage tests fail when one is missing.
WORKSPACE_DATA_TABLES = (
    "transcript_chunks",
    "task_activities",
    "webhook_deliveries",
    "blockers",
    "risks",
    "decisions",
    "tasks",
    "transcripts",
    "projects",
)
ACCOUNT_TABLES = ("memberships", "access_tokens")
ROOT_TABLES = ("accounts", "workspaces")
# Tables that are reviewed and known to hold neither account nor customer data.
UNRELATED_TABLES = frozenset({"alembic_version", "data_subject_audit"})

# Columns that are never exported. Named in the bundle, never valued.
SECRET_COLUMNS = {
    "access_tokens": ("token_hash",),
    "workspaces": ("connector_ciphertext",),
    # The outbox copies the connector's webhook URL, which may embed a credential.
    "webhook_deliveries": ("destination",),
}
# Derived data that is large and regenerable; only exported on request.
OPTIONAL_COLUMNS = {"transcript_chunks": ("embedding",)}

NOT_INCLUDED = (
    "Embedding vectors (derived from chunk text) unless include_embeddings=true.",
    "Content of workspaces shared with other members where this account is not an owner: it "
    "cannot be attributed to a single member.",
    "Copies held by connected providers (Slack, Notion, Linear, Jira, Discord, Teams, "
    "Google Calendar) and webhook receivers.",
    "Backups, logs and the identity provider's own record of the account.",
)


class ErasureBlocked(Exception):
    """The account owns a shared workspace that would be left without an owner."""

    def __init__(self, workspaces: list[dict[str, str]]):
        super().__init__("Account owns shared workspaces")
        self.workspaces = workspaces


class AccountNotFound(LookupError):
    pass


def covered_tables() -> frozenset[str]:
    return frozenset(WORKSPACE_DATA_TABLES) | set(ACCOUNT_TABLES) | set(ROOT_TABLES)


def unclassified_tables(table_names: Iterable[str]) -> list[str]:
    """Tables nobody has decided how to export and erase. A non-empty result is a bug."""
    known = covered_tables() | UNRELATED_TABLES
    return sorted(set(table_names) - known)


def subject_digest(account_id: uuid.UUID) -> str:
    """Keyed digest of an account id: checkable by an operator, useless as a list of names."""
    secret = base_settings().connector_encryption_key.get_secret_value().encode()
    key = hashlib.sha256(b"aoi-data-subject-audit-v1\0" + secret).digest()
    return hmac.new(key, account_id.bytes, hashlib.sha256).hexdigest()


async def _use_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    # Row-level security is keyed on this transaction-local setting; switching it is how one
    # transaction reaches several workspaces without ever using a privileged role.
    await db.execute(
        text("SELECT set_config('app.workspace_id', :workspace, true)"),
        {"workspace": str(workspace_id)},
    )


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row_expression(table: str, *, include_optional: bool) -> str:
    excluded = list(SECRET_COLUMNS.get(table, ()))
    if not include_optional:
        excluded += OPTIONAL_COLUMNS.get(table, ())
    if not excluded:
        return "to_jsonb(t)"
    names = ", ".join(f"'{name}'" for name in excluded)
    return f"(to_jsonb(t) - ARRAY[{names}]::text[])"


async def _rows(
    db: AsyncSession, table: str, where: str, params: dict[str, Any], *, include_optional: bool
) -> list[dict]:
    expression = _row_expression(table, include_optional=include_optional)
    result = await db.execute(
        # Table names and expressions come from the constants above, never from input.
        text(f"SELECT {expression} AS row FROM {table} t WHERE {where} ORDER BY 1"),  # noqa: S608
        params,
    )
    return [_json(row[0]) for row in result]


def _connector_field_names(ciphertext: str | None) -> dict[str, Any]:
    """Which connector settings exist, by name. Values are never decrypted into the bundle."""
    if not ciphertext:
        return {"stored_encrypted": False, "configured_fields": []}
    try:
        key = base_settings().connector_encryption_key.get_secret_value().encode()
        decoded = json.loads(Fernet(key).decrypt(ciphertext.encode()))
    except Exception:  # a bad key or corrupt ciphertext must not break the export
        return {"stored_encrypted": True, "configured_fields": None, "readable": False}
    configured = sorted(name for name, value in decoded.items() if value not in (None, "", [], {}))
    return {"stored_encrypted": True, "configured_fields": configured}


async def build_export(
    db: AsyncSession, account_id: uuid.UUID, *, include_embeddings: bool = False
) -> dict[str, Any]:
    # One snapshot for the whole bundle, so rows from different tables are consistent.
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    account = await _rows(db, "accounts", "t.id = :a", {"a": account_id}, include_optional=False)
    if not account:
        raise AccountNotFound
    memberships = await _rows(
        db, "memberships", "t.account_id = :a", {"a": account_id}, include_optional=False
    )
    tokens = await _rows(
        db, "access_tokens", "t.account_id = :a", {"a": account_id}, include_optional=False
    )
    workspace_rows = (
        await db.execute(
            text(
                "SELECT w.id, m.role, w.connector_ciphertext, "
                "(SELECT count(*) FROM memberships o "
                " WHERE o.workspace_id = w.id AND o.account_id <> :a) AS others, "
                "(SELECT to_jsonb(w) - 'connector_ciphertext') AS body "
                "FROM memberships m JOIN workspaces w ON w.id = m.workspace_id "
                "WHERE m.account_id = :a ORDER BY w.created_at, w.id"
            ),
            {"a": account_id},
        )
    ).all()

    workspaces = []
    for row in workspace_rows:
        entry: dict[str, Any] = {
            **_json(row.body),
            "your_role": row.role,
            "other_member_count": row.others,
        }
        # The account's own data: workspaces where it is the owner. Admins and members of
        # someone else's workspace can already read it there; it is not theirs to export.
        if row.role == "owner":
            await _use_workspace(db, row.id)
            entry["connector_credentials"] = _connector_field_names(row.connector_ciphertext)
            entry["data"] = {
                table: await _rows(
                    db,
                    table,
                    "t.workspace_id = :w",
                    {"w": row.id},
                    include_optional=include_embeddings,
                )
                for table in reversed(WORKSPACE_DATA_TABLES)
            }
        else:
            entry["data"] = None
        workspaces.append(entry)

    return {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "account": account[0],
        "memberships": memberships,
        "access_tokens": tokens,
        "workspaces": workspaces,
        "excluded_secrets": {table: list(cols) for table, cols in SECRET_COLUMNS.items()},
        "not_included": list(NOT_INCLUDED),
    }


async def erase_account(db: AsyncSession, account_id: uuid.UUID) -> dict[str, Any]:
    """Delete the account and what is exclusively its own, plus an audit row, uncommitted."""
    locked = await db.scalar(
        text("SELECT id FROM accounts WHERE id = :a FOR UPDATE"), {"a": account_id}
    )
    if locked is None:
        raise AccountNotFound
    memberships = (
        await db.execute(
            text(
                "SELECT workspace_id, role FROM memberships WHERE account_id = :a "
                "ORDER BY workspace_id"
            ),
            {"a": account_id},
        )
    ).all()
    # Lock the workspaces (ordered, to avoid deadlocks) before judging who else is in them: a
    # concurrent invite then waits and fails its foreign key instead of landing in a deleted
    # workspace.
    ids = [m.workspace_id for m in memberships]
    names = {}
    if ids:
        locked_rows = await db.execute(
            text(
                "SELECT id, name FROM workspaces WHERE id = ANY(CAST(:ids AS uuid[])) "
                "ORDER BY id FOR UPDATE"
            ),
            {"ids": ids},
        )
        names = {row.id: row.name for row in locked_rows}

    sole, blocked, retained = [], [], []
    for member in memberships:
        others = (
            await db.execute(
                text(
                    "SELECT role FROM memberships WHERE workspace_id = :w AND account_id <> :a"
                ),
                {"w": member.workspace_id, "a": account_id},
            )
        ).all()
        if member.workspace_id == LEGACY_WORKSPACE:
            retained.append(member.workspace_id)  # Quarantined data is never erased here.
        elif not others:
            sole.append(member.workspace_id)
        elif member.role == "owner" and not any(o.role == "owner" for o in others):
            blocked.append(
                {"id": str(member.workspace_id), "name": names.get(member.workspace_id, "")}
            )
        else:
            retained.append(member.workspace_id)
    if blocked:
        raise ErasureBlocked(blocked)

    counts: dict[str, int] = dict.fromkeys(
        (*WORKSPACE_DATA_TABLES, *ACCOUNT_TABLES, *ROOT_TABLES), 0
    )
    counts["memberships"] = len(memberships)
    # Tokens first, across every workspace; the account's tokens in retained workspaces go too.
    counts["access_tokens"] = (
        await db.execute(text("DELETE FROM access_tokens WHERE account_id = :a"), {"a": account_id})
    ).rowcount
    for workspace_id in sole:
        await _use_workspace(db, workspace_id)
        for table in WORKSPACE_DATA_TABLES:
            counts[table] += (
                await db.execute(
                    # Constant table names; the explicit filter backs up row-level security.
                    text(f"DELETE FROM {table} WHERE workspace_id = :w"),  # noqa: S608
                    {"w": workspace_id},
                )
            ).rowcount
        # Tokens of previously removed members survive membership removal; they go with the
        # workspace, as do the memberships and the encrypted connector credentials.
        counts["access_tokens"] += (
            await db.execute(
                text("SELECT count(*) FROM access_tokens WHERE workspace_id = :w"),
                {"w": workspace_id},
            )
        ).scalar_one()
        counts["workspaces"] += (
            await db.execute(text("DELETE FROM workspaces WHERE id = :w"), {"w": workspace_id})
        ).rowcount
    # Remaining memberships (retained workspaces) are removed with the account row.
    counts["accounts"] = (
        await db.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": account_id})
    ).rowcount

    audit = DataSubjectAudit(
        kind="erasure",
        subject_digest=subject_digest(account_id),
        details={
            "deleted_rows": {table: n for table, n in counts.items() if n},
            "workspaces_retained": len(retained),
        },
    )
    await write_audit(db, audit)
    return {
        "audit_id": str(audit.id),
        "deleted": {table: n for table, n in counts.items() if n},
        "workspaces_retained": len(retained),
    }


async def write_audit(db: AsyncSession, audit: DataSubjectAudit) -> None:
    db.add(audit)
    await db.flush()
