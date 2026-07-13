"""add immutable Agent Profile snapshots to runs

Revision ID: 0007_agent_profile_snapshot
Revises: 0006_tool_settings
"""

import sqlalchemy as sa

from alembic import op

revision = "0007_agent_profile_snapshot"
down_revision = "0006_tool_settings"
branch_labels = None
depends_on = None


LEGACY_SNAPSHOT = {
    "version": "legacy-unversioned",
    "composition_schema_version": 0,
    "documents": {},
    "role_documents": {},
    "source": "legacy",
}


def upgrade() -> None:
    op.add_column("runs", sa.Column("agent_profile_snapshot", sa.JSON(), nullable=True))
    runs = sa.table("runs", sa.column("agent_profile_snapshot", sa.JSON()))
    op.execute(sa.update(runs).values(agent_profile_snapshot=LEGACY_SNAPSHOT))
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("agent_profile_snapshot", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("agent_profile_snapshot")
