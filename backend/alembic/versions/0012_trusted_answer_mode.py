"""add trusted answer mode and immutable run profile

Revision ID: 0012_trusted_answer_mode
Revises: 0011_plan_execution_runtime
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0012_trusted_answer_mode"
down_revision = "0011_plan_execution_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_strategy_preferences") as batch:
        batch.add_column(
            sa.Column(
                "preferred_answer_mode",
                sa.String(length=40),
                nullable=False,
                server_default="standard",
            )
        )
    with op.batch_alter_table("runs") as batch:
        batch.add_column(
            sa.Column(
                "answer_mode", sa.String(length=40), nullable=False, server_default="trusted"
            )
        )
        batch.add_column(sa.Column("execution_profile", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("execution_profile")
        batch.drop_column("answer_mode")
    with op.batch_alter_table("conversation_strategy_preferences") as batch:
        batch.drop_column("preferred_answer_mode")
