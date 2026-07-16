"""add interactive tool approvals

Revision ID: 0014_interactive_tool_approvals
Revises: 0013_remove_direct_planning_preference
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0014_interactive_tool_approvals"
down_revision = "0013_remove_direct_planning_preference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("turn_id", sa.String(length=36), sa.ForeignKey("agent_turns.id"), nullable=False),
        sa.Column("tool_call_id", sa.String(length=36), sa.ForeignKey("tool_calls.id"), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_version", sa.String(length=40), nullable=False),
        sa.Column("frozen_input", sa.JSON(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("preview", sa.Text(), nullable=False),
        sa.Column("permission", sa.String(length=80), nullable=False),
        sa.Column("impact", sa.String(length=80), nullable=False),
        sa.Column("similar_matcher", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("decision", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tool_call_id", name="uq_approval_requests_tool_call_id"),
    )
    op.create_index("ix_approval_requests_run_status", "approval_requests", ["run_id", "status"])
    op.create_table(
        "approval_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_version", sa.String(length=40), nullable=False),
        sa.Column("matcher", sa.JSON(), nullable=False),
        sa.Column("source_approval_id", sa.String(length=36), sa.ForeignKey("approval_requests.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_grants_run_tool", "approval_grants", ["run_id", "tool_name"])


def downgrade() -> None:
    op.drop_index("ix_approval_grants_run_tool", table_name="approval_grants")
    op.drop_table("approval_grants")
    op.drop_index("ix_approval_requests_run_status", table_name="approval_requests")
    op.drop_table("approval_requests")
