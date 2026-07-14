"""add conversation management and sharing

Revision ID: 0009_conversation_management_sharing
Revises: 0008_conversation_strategy_preferences
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0009_conversation_management_sharing"
down_revision = "0008_conversation_strategy_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("title_source", sa.String(20), nullable=False, server_default="auto"))
    op.add_column("tasks", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "conversation_shares",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("token", sa.String(120), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("conversation_id", name="uq_conversation_shares_conversation_id"),
        sa.UniqueConstraint("token", name="uq_conversation_shares_token"),
    )
    op.create_index("ix_conversation_shares_conversation_id", "conversation_shares", ["conversation_id"])
    op.create_index("ix_conversation_shares_token", "conversation_shares", ["token"])


def downgrade() -> None:
    op.drop_index("ix_conversation_shares_token", table_name="conversation_shares")
    op.drop_index("ix_conversation_shares_conversation_id", table_name="conversation_shares")
    op.drop_table("conversation_shares")
    op.drop_column("tasks", "pinned_at")
    op.drop_column("tasks", "title_source")
