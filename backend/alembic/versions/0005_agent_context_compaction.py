"""Astra-owned Agent context compaction attempts

Revision ID: 0005_agent_context_compaction
Revises: 0004_detach_scheduled_jobs
Create Date: 2026-08-02 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_agent_context_compaction"
down_revision: str | Sequence[str] | None = "0004_detach_scheduled_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "context_compaction_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_type", sa.String(length=40), nullable=False),
        sa.Column("owner_id", sa.String(length=160), nullable=False),
        sa.Column("window_number", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(length=160), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("checkpoint_schema_version", sa.Integer(), nullable=False),
        sa.Column("implementation", sa.String(length=40), nullable=False),
        sa.Column("generation_provider", sa.String(length=120), nullable=True),
        sa.Column("generation_model", sa.String(length=240), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("cancellation_epoch", sa.Integer(), nullable=False),
        sa.Column("source_item_ids", JSON_TYPE, nullable=False),
        sa.Column("retained_tail_ids", JSON_TYPE, nullable=False),
        sa.Column("token_before", sa.Integer(), nullable=False),
        sa.Column("token_after", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("failure_stage", sa.String(length=120), nullable=True),
        sa.Column("failure_code", sa.String(length=160), nullable=True),
        sa.Column("checkpoint", JSON_TYPE, nullable=True),
        sa.Column("usage", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_type",
            "owner_id",
            "window_number",
            "input_digest",
            "policy_version",
            name="uq_context_compaction_idempotency",
        ),
    )
    op.create_index(
        "ix_context_compaction_owner_window",
        "context_compaction_attempts",
        ["owner_type", "owner_id", "window_number"],
    )
    op.create_index(
        "ix_context_compaction_status_created",
        "context_compaction_attempts",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_context_compaction_status_created", table_name="context_compaction_attempts")
    op.drop_index("ix_context_compaction_owner_window", table_name="context_compaction_attempts")
    op.drop_table("context_compaction_attempts")
