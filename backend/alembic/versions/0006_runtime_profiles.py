"""Persist runtime profiles and build state.

Revision ID: 0006_runtime_profiles
Revises: 0005_agent_context_compaction
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_runtime_profiles"
down_revision: str | Sequence[str] | None = "0005_agent_context_compaction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_profiles",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("active_image", sa.String(length=500), nullable=False),
        sa.Column("dependency_digest", sa.String(length=160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "runtime_builds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=80), nullable=False),
        sa.Column("dependencies", sa.JSON(), nullable=False),
        sa.Column("dependency_digest", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("phase", sa.String(length=160), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("log_summary", sa.Text(), nullable=False),
        sa.Column("staging_image", sa.String(length=500), nullable=True),
        sa.Column("activated_image", sa.String(length=500), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["runtime_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_builds_profile_created",
        "runtime_builds",
        ["profile_id", "created_at"],
    )
    op.create_index(
        "ix_runtime_builds_status_updated",
        "runtime_builds",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_builds_status_updated", table_name="runtime_builds")
    op.drop_index("ix_runtime_builds_profile_created", table_name="runtime_builds")
    op.drop_table("runtime_builds")
    op.drop_table("runtime_profiles")
