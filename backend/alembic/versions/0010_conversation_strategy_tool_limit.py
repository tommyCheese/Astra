"""add conversation strategy tool call limit

Revision ID: 0010_conversation_strategy_tool_limit
Revises: 0009_conversation_management_sharing
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0010_conversation_strategy_tool_limit"
down_revision = "0009_conversation_management_sharing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversation_strategy_preferences",
        sa.Column("max_tool_calls", sa.Integer(), nullable=False, server_default="8"),
    )
    op.execute(
        sa.text(
            "UPDATE conversation_strategy_preferences "
            "SET max_tool_calls = CASE reasoning_effort "
            "WHEN 'fast' THEN 5 WHEN 'deep' THEN 16 ELSE 8 END"
        )
    )


def downgrade() -> None:
    op.drop_column("conversation_strategy_preferences", "max_tool_calls")
