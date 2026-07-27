"""add governed agent skills

Revision ID: 0020_agent_skills
Revises: 0019_execution_slots_approval_binding
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0020_agent_skills"
down_revision = "0019_execution_slots_approval_binding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_nodes",
        sa.Column(
            "required_skill_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.create_table(
        "skill_blobs",
        sa.Column("digest", sa.String(length=80), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("active_revision_id", sa.String(length=36), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_skills_name"),
    )
    op.create_index("ix_skills_origin_enabled", "skills", ["origin", "enabled"])
    op.create_table(
        "skill_drafts",
        sa.Column("skill_id", sa.String(length=36), sa.ForeignKey("skills.id"), primary_key=True),
        sa.Column("revision_token", sa.String(length=36), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "skill_revisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36), sa.ForeignKey("skills.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("frontmatter", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("validation_report", sa.JSON(), nullable=False),
        sa.Column("predecessor_id", sa.String(length=36), sa.ForeignKey("skill_revisions.id"), nullable=True),
        sa.Column("test_only", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_revisions_version"),
    )
    op.create_index(
        "ix_skill_revisions_skill_published",
        "skill_revisions",
        ["skill_id", "published_at"],
    )
    op.create_table(
        "run_skill_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("catalog_digest", sa.String(length=80), nullable=False),
        sa.Column("catalog", sa.JSON(), nullable=False),
        sa.Column("activations", sa.JSON(), nullable=False),
        sa.Column("resource_reads", sa.JSON(), nullable=False),
        sa.Column("answer_mode", sa.String(length=40), nullable=False),
        sa.Column("draft_test", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_run_skill_snapshots_run"),
    )
    op.create_index(
        "ix_run_skill_snapshots_catalog_digest",
        "run_skill_snapshots",
        ["catalog_digest"],
    )


def downgrade() -> None:
    op.drop_table("run_skill_snapshots")
    op.drop_table("skill_revisions")
    op.drop_table("skill_drafts")
    op.drop_table("skills")
    op.drop_table("skill_blobs")
    op.drop_column("plan_nodes", "required_skill_ids")
