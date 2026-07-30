"""add run-scoped grounding evidence

Revision ID: 0025_grounding_evidence
Revises: 0024_conversation_context
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0025_grounding_evidence"
down_revision = "0024_conversation_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=40), nullable=False),
        sa.Column("evidence_key", sa.String(length=320), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("fragment", sa.JSON(), nullable=False),
        sa.Column("plan_node_id", sa.String(length=36), nullable=True),
        sa.Column("node_execution_id", sa.String(length=36), nullable=True),
        sa.Column("tool_call_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id", "evidence_key", name="uq_evidence_records_run_key"
        ),
    )
    op.create_index(
        "ix_evidence_records_run_kind",
        "evidence_records",
        ["run_id", "kind"],
    )
    op.create_index(
        "ix_evidence_records_tool_call",
        "evidence_records",
        ["tool_call_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_records_tool_call", table_name="evidence_records")
    op.drop_index("ix_evidence_records_run_kind", table_name="evidence_records")
    op.drop_table("evidence_records")
