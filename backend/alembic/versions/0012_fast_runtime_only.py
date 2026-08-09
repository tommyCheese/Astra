"""Make the fast runtime the standard-mode database default.

Revision ID: 0012_fast_runtime_only
Revises: 0011_fast_agent_runtime
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_fast_runtime_only"
down_revision: str | Sequence[str] | None = "0011_fast_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column(
            "runtime_kind",
            existing_type=sa.String(length=40),
            nullable=False,
            server_default="fast-v1",
        )


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch_op:
        batch_op.alter_column(
            "runtime_kind",
            existing_type=sa.String(length=40),
            nullable=False,
            server_default=None,
        )
