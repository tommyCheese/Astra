"""add execution slots and approval attempt bindings

Revision ID: 0019_execution_slots_approval_binding
Revises: 0018_parallel_node_executions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0019_execution_slots_approval_binding"
down_revision = "0018_parallel_node_executions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("node_executions") as batch:
        batch.add_column(sa.Column("slot_index", sa.Integer(), nullable=True))
        batch.create_unique_constraint(
            "uq_node_executions_run_slot",
            ["run_id", "slot_index"],
        )
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(sa.Column("execution_attempt", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("expected_execution_state_version", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_column("expected_execution_state_version")
        batch.drop_column("execution_attempt")
    with op.batch_alter_table("node_executions") as batch:
        batch.drop_constraint("uq_node_executions_run_slot", type_="unique")
        batch.drop_column("slot_index")
