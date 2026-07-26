"""add persistent parallel node executions

Revision ID: 0018_parallel_node_executions
Revises: 0017_simplify_modes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0018_parallel_node_executions"
down_revision = "0017_simplify_modes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("plan_node_id", sa.String(length=36), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("dispatch_batch_id", sa.String(length=36), nullable=False),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("current_slot", sa.String(length=16), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("wait_reason", sa.String(length=80), nullable=True),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("failure", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["plan_node_id"], ["plan_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_node_id", "attempt", name="uq_node_executions_node_attempt"
        ),
        sa.UniqueConstraint(
            "plan_node_id", "current_slot", name="uq_node_executions_current_slot"
        ),
    )
    op.create_index(
        "ix_node_executions_run_status", "node_executions", ["run_id", "status"]
    )
    op.create_index(
        "ix_node_executions_plan_status", "node_executions", ["plan_id", "status"]
    )
    op.create_index(
        "ix_node_executions_heartbeat", "node_executions", ["status", "heartbeat_at"]
    )
    op.create_table(
        "resource_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("node_execution_id", sa.String(length=36), nullable=False),
        sa.Column("resource_key", sa.String(length=240), nullable=False),
        sa.Column("resource_summary", sa.String(length=160), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["node_execution_id"], ["node_executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "node_execution_id",
            "resource_key",
            "mode",
            name="uq_resource_leases_execution_resource_mode",
        ),
    )
    op.create_index(
        "ix_resource_leases_run_active",
        "resource_leases",
        ["run_id", "released_at", "expires_at"],
    )
    op.create_index(
        "ix_resource_leases_resource_active",
        "resource_leases",
        ["resource_key", "released_at"],
    )
    op.create_table(
        "budget_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("node_execution_id", sa.String(length=36), nullable=False),
        sa.Column("budget_kind", sa.String(length=40), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("consumed", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["node_execution_id"], ["node_executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "node_execution_id",
            "budget_kind",
            name="uq_budget_reservations_execution_kind",
        ),
    )
    op.create_index(
        "ix_budget_reservations_run_status",
        "budget_reservations",
        ["run_id", "status"],
    )
    with op.batch_alter_table("agent_turns") as batch:
        batch.add_column(sa.Column("node_execution_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_agent_turns_node_execution",
            "node_executions",
            ["node_execution_id"],
            ["id"],
        )
    with op.batch_alter_table("tool_calls") as batch:
        batch.add_column(sa.Column("node_execution_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_tool_calls_node_execution",
            "node_executions",
            ["node_execution_id"],
            ["id"],
        )
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("node_execution_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_approval_requests_node_execution",
            "node_executions",
            ["node_execution_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_constraint("fk_approval_requests_node_execution", type_="foreignkey")
        batch.drop_column("node_execution_id")
    with op.batch_alter_table("tool_calls") as batch:
        batch.drop_constraint("fk_tool_calls_node_execution", type_="foreignkey")
        batch.drop_column("node_execution_id")
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("fk_agent_turns_node_execution", type_="foreignkey")
        batch.drop_column("node_execution_id")
    op.drop_index("ix_budget_reservations_run_status", table_name="budget_reservations")
    op.drop_table("budget_reservations")
    op.drop_index("ix_resource_leases_resource_active", table_name="resource_leases")
    op.drop_index("ix_resource_leases_run_active", table_name="resource_leases")
    op.drop_table("resource_leases")
    op.drop_index("ix_node_executions_heartbeat", table_name="node_executions")
    op.drop_index("ix_node_executions_plan_status", table_name="node_executions")
    op.drop_index("ix_node_executions_run_status", table_name="node_executions")
    op.drop_table("node_executions")
