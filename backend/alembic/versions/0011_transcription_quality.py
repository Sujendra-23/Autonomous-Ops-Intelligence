"""Preserve provider confidence and review cues alongside the full transcript."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011_transcription_quality"
down_revision = "0010_project_roadmaps"
branch_labels = depends_on = None


def upgrade():
    op.add_column("transcripts", sa.Column("transcription_segments", JSONB(),
                                          nullable=False, server_default="[]"))


def downgrade():
    op.drop_column("transcripts", "transcription_segments")
