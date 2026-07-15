"""remove direct planning from persisted conversation preferences

Revision ID: 0013_remove_direct_planning_preference
Revises: 0012_trusted_answer_mode
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0013_remove_direct_planning_preference"
down_revision = "0012_trusted_answer_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE conversation_strategy_preferences "
            "SET planning_strategy = 'adaptive' WHERE planning_strategy = 'direct'"
        )
    )


def downgrade() -> None:
    # The previous direct preference cannot be reconstructed after normalization.
    pass
