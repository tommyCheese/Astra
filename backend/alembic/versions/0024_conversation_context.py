"""add conversation context state

Revision ID: 0024_conversation_context
Revises: 0023_conversation_retention_indexes
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0024_conversation_context"
down_revision = "0023_conversation_retention_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "context_state",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "context_state")
