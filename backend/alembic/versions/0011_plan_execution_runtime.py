"""add canonical plan execution runtime

Revision ID: 0011_plan_execution_runtime
Revises: 0010_conversation_strategy_tool_limit
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0011_plan_execution_runtime"
down_revision = "0010_conversation_strategy_tool_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("active_plan_id", sa.String(length=36), nullable=True))
    op.create_table(
        "plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("supersedes_plan_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["supersedes_plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "version", name="uq_plans_run_version"),
    )
    op.create_index("ix_plans_run_status", "plans", ["run_id", "status"])
    op.create_table(
        "plan_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("node_key", sa.String(length=120), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("required_capabilities", sa.JSON(), nullable=False),
        sa.Column("success_criteria_refs", sa.JSON(), nullable=False),
        sa.Column("expected_outcome", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("optional", sa.Boolean(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("failure", sa.JSON(), nullable=True),
        sa.Column("lineage_node_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["lineage_node_id"], ["plan_nodes.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "index", name="uq_plan_nodes_plan_index"),
        sa.UniqueConstraint("plan_id", "node_key", name="uq_plan_nodes_plan_key"),
    )
    op.create_index("ix_plan_nodes_plan_status", "plan_nodes", ["plan_id", "status"])
    op.create_table(
        "plan_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("predecessor_id", sa.String(length=36), nullable=False),
        sa.Column("successor_id", sa.String(length=36), nullable=False),
        sa.Column("dependency_type", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.ForeignKeyConstraint(["predecessor_id"], ["plan_nodes.id"]),
        sa.ForeignKeyConstraint(["successor_id"], ["plan_nodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id", "predecessor_id", "successor_id", name="uq_plan_edges_nodes"
        ),
    )
    op.create_index("ix_plan_edges_successor", "plan_edges", ["successor_id"])
    with op.batch_alter_table("tool_calls") as batch:
        batch.add_column(sa.Column("plan_node_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_tool_calls_plan_node", "plan_nodes", ["plan_node_id"], ["id"]
        )
    with op.batch_alter_table("agent_turns") as batch:
        batch.add_column(sa.Column("plan_node_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_agent_turns_plan_node", "plan_nodes", ["plan_node_id"], ["id"]
        )
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("plan_node_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_artifacts_plan_node", "plan_nodes", ["plan_node_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_constraint("fk_artifacts_plan_node", type_="foreignkey")
        batch.drop_column("plan_node_id")
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("fk_agent_turns_plan_node", type_="foreignkey")
        batch.drop_column("plan_node_id")
    with op.batch_alter_table("tool_calls") as batch:
        batch.drop_constraint("fk_tool_calls_plan_node", type_="foreignkey")
        batch.drop_column("plan_node_id")
    op.drop_index("ix_plan_edges_successor", table_name="plan_edges")
    op.drop_table("plan_edges")
    op.drop_index("ix_plan_nodes_plan_status", table_name="plan_nodes")
    op.drop_table("plan_nodes")
    op.drop_index("ix_plans_run_status", table_name="plans")
    op.drop_table("plans")
    op.drop_column("runs", "active_plan_id")
