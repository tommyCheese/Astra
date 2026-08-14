"""Add durable AG-UI Run and interrupt bindings.

Revision ID: 0013_ag_ui_bindings
Revises: 0012_fast_runtime_only
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_ag_ui_bindings"
down_revision: str | Sequence[str] | None = "0012_fast_runtime_only"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ag_ui_run_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("principal_id", sa.String(240), nullable=False),
        sa.Column("thread_id", sa.String(200), nullable=False),
        sa.Column("protocol_run_id", sa.String(200), nullable=False),
        sa.Column("parent_protocol_run_id", sa.String(200), nullable=True),
        sa.Column("internal_task_id", sa.String(36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("internal_run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("lifecycle_status", sa.String(40), nullable=False),
        sa.Column("profile_version", sa.String(80), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "principal_id", "thread_id", "protocol_run_id", name="uq_ag_ui_run_bindings_principal_thread_run"
        ),
    )
    op.create_index("ix_ag_ui_run_bindings_internal_run", "ag_ui_run_bindings", ["internal_run_id"])
    op.create_index(
        "ix_ag_ui_run_bindings_thread_status",
        "ag_ui_run_bindings",
        ["principal_id", "thread_id", "lifecycle_status"],
    )
    op.create_table(
        "ag_ui_interrupt_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("interrupt_id", sa.String(200), nullable=False),
        sa.Column("run_binding_id", sa.String(36), sa.ForeignKey("ag_ui_run_bindings.id"), nullable=False),
        sa.Column("internal_run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("approval_id", sa.String(36), sa.ForeignKey("approval_requests.id"), nullable=True),
        sa.Column("waiting_kind", sa.String(80), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("expected_state_version", sa.Integer(), nullable=True),
        sa.Column("response_schema", sa.JSON(), nullable=False),
        sa.Column("server_binding", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_outcome", sa.JSON(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("interrupt_id", name="uq_ag_ui_interrupt_bindings_interrupt_id"),
    )
    op.create_index(
        "ix_ag_ui_interrupt_bindings_protocol_run", "ag_ui_interrupt_bindings", ["run_binding_id", "status"]
    )
    op.create_index(
        "ix_ag_ui_interrupt_bindings_internal_run", "ag_ui_interrupt_bindings", ["internal_run_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("ag_ui_interrupt_bindings")
    op.drop_table("ag_ui_run_bindings")
