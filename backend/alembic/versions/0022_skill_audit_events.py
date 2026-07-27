"""add skill audit events

Revision ID: 0022_skill_audit_events
Revises: 0021_conversation_answer_mode
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0022_skill_audit_events"
down_revision = "0021_conversation_answer_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "skill_id",
            sa.String(length=36),
            sa.ForeignKey("skills.id"),
            nullable=True,
        ),
        sa.Column("type", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_skill_audit_skill_created",
        "skill_audit_events",
        ["skill_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_audit_skill_created",
        table_name="skill_audit_events",
    )
    op.drop_table("skill_audit_events")
