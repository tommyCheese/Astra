"""add persistent model invocation usage ledger

Revision ID: 0005_model_invocations
Revises: 0004_artifacts_sandbox_jobs
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_model_invocations"
down_revision = "0004_artifacts_sandbox_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("turn_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(40), nullable=False, server_default="running"),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("provider_request_id", sa.String(240), nullable=True),
        sa.Column("raw_usage", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(160), nullable=True),
        sa.Column("error_code", sa.String(160), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_model_invocations_run_created", "model_invocations", ["run_id", "created_at"])
    op.create_index("ix_model_invocations_provider_model", "model_invocations", ["provider", "model"])
    op.create_index("ix_model_invocations_status_created", "model_invocations", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_model_invocations_status_created", table_name="model_invocations")
    op.drop_index("ix_model_invocations_provider_model", table_name="model_invocations")
    op.drop_index("ix_model_invocations_run_created", table_name="model_invocations")
    op.drop_table("model_invocations")
