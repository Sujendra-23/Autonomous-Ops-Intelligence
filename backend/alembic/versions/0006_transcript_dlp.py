"""Transcript classification and deterministic redaction of existing text.

Run with the schema-owner role, as with the workspace migration. Downgrade
removes metadata only; erased PII cannot be restored.
"""

from types import SimpleNamespace

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from app.services.dlp import protect_transcript, redact

revision = "0006_transcript_dlp"
down_revision = "0005_data_subject_audit"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "transcripts",
        sa.Column("sensitivity_label", sa.String(16), nullable=False, server_default="internal"),
    )
    op.add_column(
        "transcripts", sa.Column("dlp_findings", JSONB, nullable=False, server_default="{}")
    )
    op.add_column(
        "transcripts",
        sa.Column(
            "dlp_policy_version", sa.String(64), nullable=False, server_default="regex-checksum-v1"
        ),
    )
    op.create_check_constraint(
        "ck_transcripts_sensitivity_label",
        "transcripts",
        "sensitivity_label IN ('public', 'internal', 'confidential')",
    )
    connection = op.get_bind()
    transcripts = sa.table(
        "transcripts",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("content", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("participants", JSONB),
        sa.column("calendar_context", JSONB),
        sa.column("error", sa.Text()),
        sa.column("sensitivity_label", sa.String()),
        sa.column("dlp_findings", JSONB),
        sa.column("dlp_policy_version", sa.String()),
    )
    # RLS is forced: schema owners without BYPASSRLS still need workspace context.
    workspaces = connection.execute(sa.text("SELECT id FROM workspaces")).scalars().all()
    for workspace in workspaces:
        connection.execute(
            sa.text("SELECT set_config('app.workspace_id', :id, true)"), {"id": str(workspace)}
        )
        # Keyset pages keep memory bounded without leaving asyncpg server-side
        # portals open. Such portals prevent later migrations from altering the
        # scanned tables, even after SQLAlchemy's result.close().
        after = None
        while True:
            query = (
                sa.select(transcripts)
                .where(transcripts.c.workspace_id == workspace)
                .order_by(transcripts.c.id)
                .limit(100)
            )
            if after is not None:
                query = query.where(transcripts.c.id > after)
            rows = connection.execute(query).mappings().all()
            if not rows:
                break
            for row in rows:
                record = SimpleNamespace(**row)
                protect_transcript(record)
                values = {key: getattr(record, key) for key in row if key != "id"}
                connection.execute(
                    transcripts.update().where(transcripts.c.id == row["id"]).values(**values)
                )
            after = rows[-1]["id"]
        chunks = sa.table(
            "transcript_chunks", sa.column("id", sa.Uuid()),
            sa.column("workspace_id", sa.Uuid()), sa.column("content", sa.Text()),
        )
        after = None
        while True:
            query = (
                sa.select(chunks.c.id, chunks.c.content)
                .where(chunks.c.workspace_id == workspace)
                .order_by(chunks.c.id)
                .limit(100)
            )
            if after is not None:
                query = query.where(chunks.c.id > after)
            rows = connection.execute(query).all()
            if not rows:
                break
            for chunk_id, content in rows:
                sanitized = redact(content)
                if sanitized != content:
                    connection.execute(
                        sa.text(
                            "UPDATE transcript_chunks SET content=:content, embedding=NULL WHERE id=:id"
                        ),
                        {"content": sanitized, "id": chunk_id},
                    )
            after = rows[-1][0]


def downgrade():
    op.drop_constraint("ck_transcripts_sensitivity_label", "transcripts", type_="check")
    for column in ("dlp_policy_version", "dlp_findings", "sensitivity_label"):
        op.drop_column("transcripts", column)
