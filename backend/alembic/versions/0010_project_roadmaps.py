"""Project roadmaps share their project's existing workspace isolation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_project_roadmaps"
down_revision = "0009_unified_tasks"
branch_labels = depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("roadmap", JSONB(), nullable=True))
    op.add_column("projects", sa.Column("roadmap_version", sa.Integer(), nullable=False,
                                      server_default="0"))


def downgrade():
    op.drop_column("projects", "roadmap_version")
    op.drop_column("projects", "roadmap")
