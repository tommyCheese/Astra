"""concurrent subagent supervision

Revision ID: 0003_concurrent_subagent_supervision
Revises: 0002_governed_subagent_runtime
Create Date: 2026-08-02 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_concurrent_subagent_supervision"
down_revision: str | Sequence[str] | None = "0002_governed_subagent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_joins") as batch_op:
        batch_op.add_column(sa.Column("group_id", sa.String(length=160), nullable=True))
        batch_op.add_column(
            sa.Column("consumed_parent_state_version", sa.Integer(), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_agent_joins_parent_group", ["parent_execution_id", "group_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_joins") as batch_op:
        batch_op.drop_constraint("uq_agent_joins_parent_group", type_="unique")
        batch_op.drop_column("consumed_parent_state_version")
        batch_op.drop_column("group_id")
