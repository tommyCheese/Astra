"""preserve scheduled job delivery conversations

Revision ID: 0004_detach_scheduled_jobs
Revises: 0003_concurrent_subagent_supervision
Create Date: 2026-08-02 17:00:00.000000
"""

from collections.abc import Sequence

revision: str = "0004_detach_scheduled_jobs"
down_revision: str | Sequence[str] | None = "0003_concurrent_subagent_supervision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The delivery conversation remains the durable location for task output.
    pass


def downgrade() -> None:
    pass
