"""add scheduled jobs and heartbeat foundation

Revision ID: 0026_scheduled_jobs_heartbeat
Revises: 0025_grounding_evidence
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0026_scheduled_jobs_heartbeat"
down_revision = "0025_grounding_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("system_key", sa.String(length=320), nullable=True),
        sa.Column("system_managed", sa.Boolean(), nullable=False),
        sa.Column("owner_principal", sa.String(length=240), nullable=True),
        sa.Column(
            "target_task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            nullable=True,
        ),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule_type", sa.String(length=40), nullable=False),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("misfire_policy", sa.String(length=40), nullable=False),
        sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("overlap_policy", sa.String(length=40), nullable=False),
        sa.Column("execution", sa.JSON(), nullable=False),
        sa.Column("heartbeat", sa.JSON(), nullable=False),
        sa.Column("next_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=240), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("system_key", name="uq_scheduled_jobs_system_key"),
    )
    op.create_index(
        "ix_scheduled_jobs_due",
        "scheduled_jobs",
        ["enabled", "deleted_at", "next_fire_at", "lease_expires_at"],
    )
    op.create_index(
        "ix_scheduled_jobs_target",
        "scheduled_jobs",
        ["target_task_id", "kind"],
    )
    op.create_table(
        "scheduled_job_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("scheduled_jobs.id"),
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=320), nullable=False),
        sa.Column("trigger_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("claimed_by", sa.String(length=240), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id"),
            nullable=True,
        ),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("runs.id"),
            nullable=True,
        ),
        sa.Column("outcome", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id",
            "scheduled_for",
            name="uq_scheduled_job_runs_job_scheduled_for",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_scheduled_job_runs_idempotency_key",
        ),
    )
    op.create_index(
        "ix_scheduled_job_runs_job_created",
        "scheduled_job_runs",
        ["job_id", "created_at"],
    )
    op.create_index(
        "ix_scheduled_job_runs_status_claimed",
        "scheduled_job_runs",
        ["status", "claimed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduled_job_runs_status_claimed",
        table_name="scheduled_job_runs",
    )
    op.drop_index(
        "ix_scheduled_job_runs_job_created",
        table_name="scheduled_job_runs",
    )
    op.drop_table("scheduled_job_runs")
    op.drop_index("ix_scheduled_jobs_target", table_name="scheduled_jobs")
    op.drop_index("ix_scheduled_jobs_due", table_name="scheduled_jobs")
    op.drop_table("scheduled_jobs")
