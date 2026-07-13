"""add persistent conversation strategy preferences

Revision ID: 0008_conversation_strategy_preferences
Revises: 0007_agent_profile_snapshot
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_conversation_strategy_preferences"
down_revision = "0007_agent_profile_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_strategy_preferences",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("reasoning_effort", sa.String(40), nullable=False),
        sa.Column("planning_strategy", sa.String(40), nullable=False),
        sa.Column("reflection_enabled", sa.Boolean(), nullable=False),
        sa.Column("reflection_trigger", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("conversation_strategy_preferences")
