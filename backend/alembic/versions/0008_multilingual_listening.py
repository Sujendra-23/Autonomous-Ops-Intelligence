"""Persist the listening language so reconnects use the same preference."""

import sqlalchemy as sa
from alembic import op

revision = "0008_multilingual_listening"
down_revision = "0007_identity_gateway"
branch_labels = depends_on = None


def upgrade():
    op.add_column("transcripts", sa.Column(
        "transcription_language", sa.String(16), nullable=False, server_default="auto"
    ))


def downgrade():
    op.drop_column("transcripts", "transcription_language")
