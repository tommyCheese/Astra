"""make deep reasoning tool calls unlimited

Revision ID: 0016_deep_unlimited_tool_calls
Revises: 0015_agent_permission_foundations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0016_deep_unlimited_tool_calls"
down_revision = "0015_agent_permission_foundations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_strategy_preferences") as batch:
        batch.alter_column(
            "max_tool_calls",
            existing_type=sa.Integer(),
            nullable=True,
            existing_server_default="8",
        )
    op.execute(
        sa.text(
            "UPDATE conversation_strategy_preferences "
            "SET max_tool_calls = NULL WHERE reasoning_effort = 'deep'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE conversation_strategy_preferences "
            "SET max_tool_calls = 16 WHERE max_tool_calls IS NULL"
        )
    )
    with op.batch_alter_table("conversation_strategy_preferences") as batch:
        batch.alter_column(
            "max_tool_calls",
            existing_type=sa.Integer(),
            nullable=False,
            existing_server_default="8",
        )
