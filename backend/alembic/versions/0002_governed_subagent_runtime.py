"""governed subagent runtime persistence

Revision ID: 0002_governed_subagent_runtime
Revises: 0001_current_baseline
Create Date: 2026-08-01 12:00:00.000000
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_governed_subagent_runtime"
down_revision: str | Sequence[str] | None = "0001_current_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql")

LINEAGE_TABLES = (
    "evidence_records",
    "plans",
    "plan_nodes",
    "node_executions",
    "model_invocations",
    "tool_calls",
    "approval_requests",
    "artifacts",
    "run_events",
    "agent_turns",
)


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "cancellation_epoch",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
    op.create_table(
        "agent_executions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("parent_execution_id", sa.String(length=36), nullable=True),
        sa.Column("parent_node_execution_id", sa.String(length=36), nullable=True),
        sa.Column("identity_id", sa.String(length=36), nullable=True),
        sa.Column("delegation_id", sa.String(length=36), nullable=True),
        sa.Column("execution_type", sa.String(length=40), nullable=False),
        sa.Column("root_slot", sa.String(length=16), nullable=True),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("contract", JSON_TYPE, nullable=False),
        sa.Column("context_manifest", JSON_TYPE, nullable=False),
        sa.Column("catalog_snapshot", JSON_TYPE, nullable=False),
        sa.Column("budget_envelope", JSON_TYPE, nullable=False),
        sa.Column("budget_usage", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("wait_reason", sa.String(length=120), nullable=True),
        sa.Column("checkpoint", JSON_TYPE, nullable=False),
        sa.Column("result", JSON_TYPE, nullable=True),
        sa.Column("error", JSON_TYPE, nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("cancellation_epoch", sa.Integer(), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["parent_execution_id"], ["agent_executions.id"]),
        sa.ForeignKeyConstraint(["parent_node_execution_id"], ["node_executions.id"]),
        sa.ForeignKeyConstraint(["identity_id"], ["agent_identities.id"]),
        sa.ForeignKeyConstraint(["delegation_id"], ["agent_delegations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "root_slot", name="uq_agent_executions_run_root"),
        sa.UniqueConstraint(
            "parent_execution_id",
            "request_id",
            name="uq_agent_executions_parent_request",
        ),
    )
    op.create_index(
        "ix_agent_executions_run_status", "agent_executions", ["run_id", "status"]
    )
    op.create_index(
        "ix_agent_executions_parent_status",
        "agent_executions",
        ["parent_execution_id", "status"],
    )
    op.create_index(
        "ix_agent_executions_recovery", "agent_executions", ["status", "heartbeat_at"]
    )
    op.create_index("ix_agent_executions_identity", "agent_executions", ["identity_id"])

    op.create_table(
        "agent_budget_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("parent_execution_id", sa.String(length=36), nullable=False),
        sa.Column("child_execution_id", sa.String(length=36), nullable=False),
        sa.Column("envelope", JSON_TYPE, nullable=False),
        sa.Column("parent_reserve", JSON_TYPE, nullable=False),
        sa.Column("actual_usage", JSON_TYPE, nullable=False),
        sa.Column("returned_budget", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["parent_execution_id"], ["agent_executions.id"]),
        sa.ForeignKeyConstraint(["child_execution_id"], ["agent_executions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "child_execution_id", name="uq_agent_budget_reservations_child"
        ),
    )
    op.create_index(
        "ix_agent_budget_reservations_parent_status",
        "agent_budget_reservations",
        ["parent_execution_id", "status"],
    )
    op.create_index(
        "ix_agent_budget_reservations_run_status",
        "agent_budget_reservations",
        ["run_id", "status"],
    )
    op.create_table(
        "agent_joins",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("parent_execution_id", sa.String(length=36), nullable=False),
        sa.Column("consumer_plan_node_id", sa.String(length=36), nullable=True),
        sa.Column("join_key", sa.String(length=160), nullable=False),
        sa.Column("policy", sa.String(length=40), nullable=False),
        sa.Column("child_execution_ids", JSON_TYPE, nullable=False),
        sa.Column("required_execution_ids", JSON_TYPE, nullable=False),
        sa.Column("optional_execution_ids", JSON_TYPE, nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("result", JSON_TYPE, nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["parent_execution_id"], ["agent_executions.id"]),
        sa.ForeignKeyConstraint(["consumer_plan_node_id"], ["plan_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_execution_id", "join_key", name="uq_agent_joins_parent_key"
        ),
    )
    op.create_index(
        "ix_agent_joins_run_status", "agent_joins", ["run_id", "status"]
    )
    op.create_index(
        "ix_agent_joins_parent_status",
        "agent_joins",
        ["parent_execution_id", "status"],
    )

    for table_name in LINEAGE_TABLES:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(sa.Column("agent_execution_id", sa.String(length=36), nullable=True))
            batch_op.create_foreign_key(
                f"fk_{table_name}_agent_execution_id",
                "agent_executions",
                ["agent_execution_id"],
                ["id"],
            )

    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.add_column(sa.Column("requester_identity_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("delegation_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("catalog_digest", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("continuation_token", sa.String(length=160), nullable=True))
        batch_op.add_column(
            sa.Column("grant_scope", JSON_TYPE, nullable=False, server_default="{}")
        )
        batch_op.create_foreign_key(
            "fk_approval_requests_requester_identity_id",
            "agent_identities",
            ["requester_identity_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_approval_requests_delegation_id",
            "agent_delegations",
            ["delegation_id"],
            ["id"],
        )

    op.create_index(
        "ix_evidence_records_agent_execution",
        "evidence_records",
        ["agent_execution_id"],
    )
    op.create_index(
        "ix_run_events_agent_execution_id",
        "run_events",
        ["agent_execution_id", "id"],
    )
    _backfill_root_executions()


def _backfill_root_executions() -> None:
    bind = op.get_bind()
    runs = sa.table(
        "runs",
        sa.column("id", sa.String()),
        sa.column("task_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("task_contract", JSON_TYPE),
        sa.column("agent_state", JSON_TYPE),
        sa.column("result", JSON_TYPE),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("completed_at", sa.DateTime(timezone=True)),
    )
    executions = sa.table(
        "agent_executions",
        sa.column("id", sa.String()),
        sa.column("run_id", sa.String()),
        sa.column("task_id", sa.String()),
        sa.column("execution_type", sa.String()),
        sa.column("root_slot", sa.String()),
        sa.column("request_id", sa.String()),
        sa.column("depth", sa.Integer()),
        sa.column("ordinal", sa.Integer()),
        sa.column("contract", JSON_TYPE),
        sa.column("context_manifest", JSON_TYPE),
        sa.column("catalog_snapshot", JSON_TYPE),
        sa.column("budget_envelope", JSON_TYPE),
        sa.column("budget_usage", JSON_TYPE),
        sa.column("status", sa.String()),
        sa.column("phase", sa.String()),
        sa.column("checkpoint", JSON_TYPE),
        sa.column("result", JSON_TYPE),
        sa.column("fencing_token", sa.Integer()),
        sa.column("cancellation_epoch", sa.Integer()),
        sa.column("state_version", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("queued_at", sa.DateTime(timezone=True)),
        sa.column("claimed_at", sa.DateTime(timezone=True)),
        sa.column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.column("finished_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(sa.select(runs)).mappings()
    roots: dict[str, str] = {}
    terminal_statuses = {
        "completed",
        "completed_with_warnings",
        "blocked",
        "failed",
        "cancelled",
    }
    for row in rows:
        execution_id = str(uuid.uuid4())
        roots[row["id"]] = execution_id
        run_status = row["status"]
        if run_status in terminal_statuses:
            status = run_status
            phase = "terminal"
        elif run_status == "waiting_user":
            status = "waiting_parent"
            phase = "waiting_parent"
        elif run_status in {"created", "planning"}:
            status = "queued"
            phase = "planning"
        else:
            status = "running"
            phase = "executing"
        bind.execute(
            executions.insert(),
            {
                "id": execution_id,
                "run_id": row["id"],
                "task_id": row["task_id"],
                "execution_type": "root",
                "root_slot": "root",
                "request_id": "root",
                "depth": 0,
                "ordinal": 0,
                "contract": row["task_contract"] or {},
                "context_manifest": {},
                "catalog_snapshot": {},
                "budget_envelope": {},
                "budget_usage": {},
                "status": status,
                "phase": phase,
                "checkpoint": row["agent_state"] or {},
                "result": row["result"],
                "fencing_token": 0,
                "cancellation_epoch": 0,
                "state_version": 1,
                "created_at": row["created_at"],
                "queued_at": row["created_at"],
                "claimed_at": row["started_at"],
                "heartbeat_at": row["completed_at"] or row["started_at"],
                "finished_at": row["completed_at"],
                "updated_at": row["completed_at"] or row["started_at"] or row["created_at"],
            },
        )

    for table_name in (
        "evidence_records",
        "plans",
        "node_executions",
        "model_invocations",
        "tool_calls",
        "approval_requests",
        "artifacts",
        "run_events",
        "agent_turns",
    ):
        for run_id, execution_id in roots.items():
            bind.execute(
                sa.text(
                    f"UPDATE {table_name} SET agent_execution_id = :execution_id "
                    "WHERE run_id = :run_id AND agent_execution_id IS NULL"
                ),
                {"execution_id": execution_id, "run_id": run_id},
            )
    for run_id, execution_id in roots.items():
        bind.execute(
            sa.text(
                "UPDATE plan_nodes SET agent_execution_id = :execution_id "
                "WHERE agent_execution_id IS NULL AND plan_id IN "
                "(SELECT id FROM plans WHERE run_id = :run_id)"
            ),
            {"execution_id": execution_id, "run_id": run_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch_op:
        batch_op.drop_constraint(
            "fk_approval_requests_delegation_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_approval_requests_requester_identity_id", type_="foreignkey"
        )
        batch_op.drop_column("grant_scope")
        batch_op.drop_column("continuation_token")
        batch_op.drop_column("catalog_digest")
        batch_op.drop_column("delegation_id")
        batch_op.drop_column("requester_identity_id")
    op.drop_index("ix_run_events_agent_execution_id", table_name="run_events")
    op.drop_index("ix_evidence_records_agent_execution", table_name="evidence_records")
    for table_name in reversed(LINEAGE_TABLES):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table_name}_agent_execution_id",
                type_="foreignkey",
            )
            batch_op.drop_column("agent_execution_id")
    op.drop_index("ix_agent_joins_parent_status", table_name="agent_joins")
    op.drop_index("ix_agent_joins_run_status", table_name="agent_joins")
    op.drop_table("agent_joins")
    op.drop_index(
        "ix_agent_budget_reservations_run_status",
        table_name="agent_budget_reservations",
    )
    op.drop_index(
        "ix_agent_budget_reservations_parent_status",
        table_name="agent_budget_reservations",
    )
    op.drop_table("agent_budget_reservations")
    op.drop_index("ix_agent_executions_identity", table_name="agent_executions")
    op.drop_index("ix_agent_executions_recovery", table_name="agent_executions")
    op.drop_index("ix_agent_executions_parent_status", table_name="agent_executions")
    op.drop_index("ix_agent_executions_run_status", table_name="agent_executions")
    op.drop_table("agent_executions")
    with op.batch_alter_table("runs") as batch_op:
        batch_op.drop_column("cancellation_epoch")
