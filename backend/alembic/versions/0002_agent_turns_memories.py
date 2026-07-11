"""add agent turns and memories

Revision ID: 0002_agent_turns_memories
Revises: 0001_initial_run_model
Create Date: 2026-07-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_agent_turns_memories"
down_revision: Union[str, None] = "0001_initial_run_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("decision_type", sa.String(length=40), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=False),
        sa.Column("selected_tool", sa.String(length=120), nullable=True),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("observation", sa.JSON(), nullable=True),
        sa.Column("reflection", sa.JSON(), nullable=True),
        sa.Column("tool_call_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("memory_reads", sa.JSON(), nullable=False),
        sa.Column("memory_writes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_turns_run_id_index", "agent_turns", ["run_id", "turn_index"])
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("workspace_id", sa.String(length=120), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_data", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_memories_scope_kind", "memories", ["scope", "kind"])


def downgrade() -> None:
    op.drop_index("ix_memories_scope_kind", table_name="memories")
    op.drop_table("memories")
    op.drop_index("ix_agent_turns_run_id_index", table_name="agent_turns")
    op.drop_table("agent_turns")
