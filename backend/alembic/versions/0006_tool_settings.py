"""add persistent tool settings

Revision ID: 0006_tool_settings
Revises: 0005_model_invocations
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_tool_settings"
down_revision = "0005_model_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_settings",
        sa.Column("tool_name", sa.String(120), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tool_settings")
