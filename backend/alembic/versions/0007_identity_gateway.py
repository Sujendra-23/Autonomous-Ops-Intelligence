"""Bind a verified identity-gateway company to exactly one AOI workspace."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from alembic import op

revision = "0007_identity_gateway"
down_revision = "0006_transcript_dlp"
branch_labels = depends_on = None


def upgrade():
    # Migration 0004 creates account tables from the current model metadata.
    # Fresh installs therefore already include these columns; upgraded installs do not.
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("workspaces")}
    for column in (
        sa.Column("identity_issuer", sa.String(256), nullable=True),
        sa.Column("identity_tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("identity_tenant_slug", sa.String(63), nullable=True),
    ):
        if column.name not in existing:
            op.add_column("workspaces", column)
    constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("workspaces")}
    if "uq_workspace_gateway_tenant" not in constraints:
        op.create_unique_constraint("uq_workspace_gateway_tenant", "workspaces", ["identity_issuer", "identity_tenant_id"])


def downgrade():
    # The binding must not be silently discarded while customer data remains.
    raise RuntimeError("Restore a verified backup to roll back company identity bindings")
