"""store the preferred answer mode per conversation

Revision ID: 0021_conversation_answer_mode
Revises: 0020_agent_skills
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0021_conversation_answer_mode"
down_revision = "0020_agent_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column(
                "preferred_answer_mode",
                sa.String(length=40),
                nullable=False,
                server_default="standard",
            )
        )
    op.execute(
        """
        UPDATE tasks
        SET preferred_answer_mode = COALESCE(
            (
                SELECT runs.answer_mode
                FROM runs
                WHERE runs.task_id = tasks.id
                ORDER BY runs.created_at DESC
                LIMIT 1
            ),
            'standard'
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("preferred_answer_mode")
