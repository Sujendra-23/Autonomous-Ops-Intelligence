"""Merge the data-subject audit and Salesforce task migrations into one head."""

revision = "0006_merge_heads"
down_revision = ("0005_data_subject_audit", "0005_salesforce_tasks")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
