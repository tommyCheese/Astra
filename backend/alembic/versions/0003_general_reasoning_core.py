"""add general reasoning core state

Revision ID: 0003_general_reasoning_core
Revises: 0002_agent_turns_memories
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_general_reasoning_core"
down_revision = "0002_agent_turns_memories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("reasoning_policy", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("task_contract", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("plan_graph", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("agent_state", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("state_version", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("terminal_reason", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("waiting_state", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("task_adapter", sa.String(length=80), nullable=False, server_default="web"))
    with op.batch_alter_table("agent_turns") as batch:
        batch.add_column(sa.Column("evaluation", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("reflection_patch", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("state_version_before", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("state_version_after", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("phase", sa.String(length=40), nullable=False, server_default="created"))
        batch.add_column(sa.Column("idempotency_key", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("paused_node", sa.String(length=80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        for column in ("paused_node", "idempotency_key", "phase", "plan_version", "state_version_after", "state_version_before", "reflection_patch", "evaluation"):
            batch.drop_column(column)
    with op.batch_alter_table("runs") as batch:
        for column in ("task_adapter", "waiting_state", "terminal_reason", "state_version", "agent_state", "plan_graph", "task_contract", "reasoning_policy"):
            batch.drop_column(column)
