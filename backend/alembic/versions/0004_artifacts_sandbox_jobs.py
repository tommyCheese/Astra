"""add artifact storage metadata and sandbox jobs

Revision ID: 0004_artifacts_sandbox_jobs
Revises: 0003_general_reasoning_core
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_artifacts_sandbox_jobs"
down_revision = "0003_general_reasoning_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sandbox_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("tool_call_id", sa.String(36), sa.ForeignKey("tool_calls.id"), nullable=True),
        sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
        sa.Column("runtime_profile", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("resource_limits", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("input_artifact_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("output_artifact_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("executor", sa.String(80), nullable=False),
        sa.Column("runtime_name", sa.String(120), nullable=True),
        sa.Column("image_digest", sa.String(240), nullable=True),
        sa.Column("exit_reason", sa.String(120), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("stdout_summary", sa.Text(), nullable=True),
        sa.Column("stderr_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("tool_call_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("sandbox_job_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("mime_type", sa.String(160), nullable=True))
        batch.add_column(sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("checksum", sa.String(80), nullable=True))
        batch.add_column(sa.Column("storage_key", sa.String(500), nullable=True))
        batch.add_column(sa.Column("preview_key", sa.String(500), nullable=True))
        batch.add_column(sa.Column("security_status", sa.String(40), nullable=False, server_default="pending"))
        batch.add_column(sa.Column("provenance", sa.JSON(), nullable=False, server_default="{}"))
        batch.create_foreign_key("fk_artifact_tool_call", "tool_calls", ["tool_call_id"], ["id"])
        batch.create_foreign_key("fk_artifact_sandbox_job", "sandbox_jobs", ["sandbox_job_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("fk_artifact_sandbox_job", type_="foreignkey")
        batch.drop_constraint("fk_artifact_tool_call", type_="foreignkey")
        for name in ("provenance", "security_status", "preview_key", "storage_key", "checksum", "size_bytes", "mime_type", "sandbox_job_id", "tool_call_id"):
            batch.drop_column(name)
    op.drop_table("sandbox_jobs")
