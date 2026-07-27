"""add conversation retention scan indexes

Revision ID: 0023_conversation_retention_indexes
Revises: 0022_skill_audit_events
"""

from collections.abc import Sequence

from alembic import op

revision = "0023_conversation_retention_indexes"
down_revision = "0022_skill_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_tasks_retention_scan",
        "tasks",
        ["pinned_at", "updated_at"],
    )
    op.create_index(
        "ix_runs_task_status",
        "runs",
        ["task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_runs_task_status", table_name="runs")
    op.drop_index("ix_tasks_retention_scan", table_name="tasks")
