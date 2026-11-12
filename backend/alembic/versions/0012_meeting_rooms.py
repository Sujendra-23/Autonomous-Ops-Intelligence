"""Workspace-isolated AOI video meetings and reusable audio huddles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012_meeting_rooms"
down_revision = "0011_transcription_quality"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "meeting_rooms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_zone", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("attendees", JSONB(), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="SET NULL")),
        sa.Column("reminder_minutes", sa.Integer()),
        sa.Column("calendar_event_id", sa.String(1024)),
        sa.Column("calendar_url", sa.Text()),
        sa.Column("invitation_status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "request_id", name="uq_meeting_rooms_request"),
        sa.CheckConstraint("kind IN ('meeting','huddle')", name="meeting_room_kind"),
    )
    op.create_index("ix_meeting_rooms_workspace_id", "meeting_rooms", ["workspace_id"])
    op.execute('ALTER TABLE "meeting_rooms" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "meeting_rooms" FORCE ROW LEVEL SECURITY')
    op.execute("""CREATE POLICY workspace_isolation ON meeting_rooms
        USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)""")


def downgrade():
    op.drop_table("meeting_rooms")
