"""Freeze plugin behavior identities in tool catalog snapshots.

Revision ID: 0007_plugin_tool_catalog_snapshots
Revises: 0006_runtime_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_plugin_tool_catalog_snapshots"
down_revision: str | Sequence[str] | None = "0006_runtime_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_catalog_snapshots",
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "tool_catalog_snapshots",
        sa.Column("behavioral_catalog", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "tool_catalog_snapshots",
        sa.Column("behavioral_digest", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "tool_catalog_snapshots",
        sa.Column("display_digest", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tool_catalog_snapshots", "display_digest")
    op.drop_column("tool_catalog_snapshots", "behavioral_digest")
    op.drop_column("tool_catalog_snapshots", "behavioral_catalog")
    op.drop_column("tool_catalog_snapshots", "schema_version")
