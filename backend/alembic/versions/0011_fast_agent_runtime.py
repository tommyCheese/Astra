"""Add versioned Fast Agent runtime identity and snapshot state.

Revision ID: 0011_fast_agent_runtime
Revises: 0010_remove_builtin_web_tools
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_fast_agent_runtime"
down_revision: str | Sequence[str] | None = "0010_remove_builtin_web_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "runtime_kind",
            sa.String(length=40),
            nullable=False,
            server_default="fast-v1",
        ),
    )
    op.add_column(
        "runs",
        sa.Column("runtime_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "runs",
        sa.Column("fast_runtime_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "runs",
        sa.Column("fast_state_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE runs SET runtime_kind = CASE "
        "WHEN answer_mode = 'trusted' THEN 'trusted-v1' "
        "ELSE 'fast-v1' END"
    )


def downgrade() -> None:
    op.drop_column("runs", "fast_state_version")
    op.drop_column("runs", "fast_runtime_snapshot")
    op.drop_column("runs", "runtime_version")
    op.drop_column("runs", "runtime_kind")
