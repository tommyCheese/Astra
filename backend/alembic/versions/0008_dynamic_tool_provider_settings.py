"""Add generic provider configuration and tool-settings audit state.

Revision ID: 0008_dynamic_tool_provider_settings
Revises: 0007_plugin_tool_catalog_snapshots
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_dynamic_tool_provider_settings"
down_revision: str | Sequence[str] | None = "0007_plugin_tool_catalog_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_provider_settings",
        sa.Column("provider_id", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider_id"),
    )
    op.create_table(
        "tool_settings_audit",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.String(length=240), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tool_settings_audit_created",
        "tool_settings_audit",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_settings_audit_created", table_name="tool_settings_audit")
    op.drop_table("tool_settings_audit")
    op.drop_table("tool_provider_settings")
