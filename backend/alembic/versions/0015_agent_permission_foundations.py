"""add agent permission and task workspace foundations

Revision ID: 0015_agent_permission_foundations
Revises: 0014_interactive_tool_approvals
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0015_agent_permission_foundations"
down_revision = "0014_interactive_tool_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("approval_requests") as batch:
        batch.add_column(
            sa.Column(
                "frozen_effect_plan",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(sa.Column("effect_plan_hash", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("analyzer_version", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("analyzer_digest", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("reviewer_identity", sa.JSON(), nullable=True))

    with op.batch_alter_table("approval_grants") as batch:
        batch.add_column(sa.Column("task_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_approval_grants_task_id_tasks",
            "tasks",
            ["task_id"],
            ["id"],
        )
        batch.add_column(
            sa.Column("scope", sa.String(length=40), nullable=False, server_default="run")
        )
        batch.add_column(
            sa.Column("subject", sa.JSON(), nullable=False, server_default=sa.text("'{}'"))
        )
        batch.add_column(
            sa.Column("effect_kinds", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(
            sa.Column(
                "resource_matcher",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column(
                "invocation_constraints",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active")
        )
        batch.add_column(sa.Column("max_uses", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            "ix_approval_grants_task_scope", ["task_id", "scope", "status"], unique=False
        )

    op.create_table(
        "task_workspaces",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("quotas", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("task_id", name="uq_task_workspaces_task_id"),
    )
    op.create_table(
        "workspace_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("task_workspaces.id"),
            nullable=False,
        ),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="present"),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=120), nullable=True),
        sa.Column("security_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column(
            "deliverable_candidate", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "relative_path", name="uq_workspace_files_path"
        ),
    )
    op.create_index(
        "ix_workspace_files_workspace_status",
        "workspace_files",
        ["workspace_id", "status"],
    )
    op.create_table(
        "workspace_checkpoints",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("task_workspaces.id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("manifest_hash", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="valid"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_checkpoints_workspace_created",
        "workspace_checkpoints",
        ["workspace_id", "created_at"],
    )
    op.create_table(
        "workspace_changes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(length=36),
            sa.ForeignKey("task_workspaces.id"),
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column(
            "tool_call_id",
            sa.String(length=36),
            sa.ForeignKey("tool_calls.id"),
            nullable=True,
        ),
        sa.Column(
            "checkpoint_id",
            sa.String(length=36),
            sa.ForeignKey("workspace_checkpoints.id"),
            nullable=True,
        ),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("change_kind", sa.String(length=40), nullable=False),
        sa.Column("before_checksum", sa.String(length=120), nullable=True),
        sa.Column("after_checksum", sa.String(length=120), nullable=True),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("security_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column(
            "deliverable_candidate", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_changes_run_created", "workspace_changes", ["run_id", "created_at"]
    )
    op.create_index(
        "ix_workspace_changes_workspace_path",
        "workspace_changes",
        ["workspace_id", "relative_path"],
    )

    op.create_table(
        "agent_identities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column(
            "parent_identity_id",
            sa.String(length=36),
            sa.ForeignKey("agent_identities.id"),
            nullable=True,
        ),
        sa.Column("identity_type", sa.String(length=80), nullable=False),
        sa.Column("principal", sa.String(length=240), nullable=False),
        sa.Column("trust_level", sa.String(length=40), nullable=False, server_default="internal"),
        sa.Column("attributes", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_identities_run_type", "agent_identities", ["run_id", "identity_type"]
    )
    op.create_index(
        "ix_agent_identities_task_type", "agent_identities", ["task_id", "identity_type"]
    )
    op.create_table(
        "agent_delegations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "parent_identity_id",
            sa.String(length=36),
            sa.ForeignKey("agent_identities.id"),
            nullable=False,
        ),
        sa.Column(
            "child_identity_id",
            sa.String(length=36),
            sa.ForeignKey("agent_identities.id"),
            nullable=False,
        ),
        sa.Column("delegated_scope", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "parent_identity_id",
            "child_identity_id",
            name="uq_agent_delegations_parent_child",
        ),
    )
    op.create_index(
        "ix_agent_delegations_parent",
        "agent_delegations",
        ["parent_identity_id", "revoked_at"],
    )
    op.create_table(
        "tool_catalog_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("catalog", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("digest", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_tool_catalog_snapshots_run_id"),
    )
    op.create_table(
        "credential_grants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column(
            "agent_identity_id",
            sa.String(length=36),
            sa.ForeignKey("agent_identities.id"),
            nullable=False,
        ),
        sa.Column("service", sa.String(length=160), nullable=False),
        sa.Column("tenant", sa.String(length=240), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("resources", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("actions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_credential_grants_run_service", "credential_grants", ["run_id", "service"]
    )
    op.create_index(
        "ix_credential_grants_task_revoked", "credential_grants", ["task_id", "revoked_at"]
    )
    op.create_table(
        "data_flow_states",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("trust_sources", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("data_labels", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "allowed_destinations", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "prohibited_destinations",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("retention", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_data_flow_states_run_id"),
    )


def downgrade() -> None:
    op.drop_table("data_flow_states")
    op.drop_index("ix_credential_grants_task_revoked", table_name="credential_grants")
    op.drop_index("ix_credential_grants_run_service", table_name="credential_grants")
    op.drop_table("credential_grants")
    op.drop_table("tool_catalog_snapshots")
    op.drop_index("ix_agent_delegations_parent", table_name="agent_delegations")
    op.drop_table("agent_delegations")
    op.drop_index("ix_agent_identities_task_type", table_name="agent_identities")
    op.drop_index("ix_agent_identities_run_type", table_name="agent_identities")
    op.drop_table("agent_identities")

    op.drop_index("ix_workspace_changes_workspace_path", table_name="workspace_changes")
    op.drop_index("ix_workspace_changes_run_created", table_name="workspace_changes")
    op.drop_table("workspace_changes")
    op.drop_index(
        "ix_workspace_checkpoints_workspace_created", table_name="workspace_checkpoints"
    )
    op.drop_table("workspace_checkpoints")
    op.drop_index("ix_workspace_files_workspace_status", table_name="workspace_files")
    op.drop_table("workspace_files")
    op.drop_table("task_workspaces")

    with op.batch_alter_table("approval_grants") as batch:
        batch.drop_index("ix_approval_grants_task_scope")
        batch.drop_constraint("fk_approval_grants_task_id_tasks", type_="foreignkey")
        batch.drop_column("revoked_at")
        batch.drop_column("last_used_at")
        batch.drop_column("expires_at")
        batch.drop_column("use_count")
        batch.drop_column("max_uses")
        batch.drop_column("status")
        batch.drop_column("invocation_constraints")
        batch.drop_column("resource_matcher")
        batch.drop_column("effect_kinds")
        batch.drop_column("subject")
        batch.drop_column("scope")
        batch.drop_column("task_id")

    with op.batch_alter_table("approval_requests") as batch:
        batch.drop_column("reviewer_identity")
        batch.drop_column("analyzer_digest")
        batch.drop_column("analyzer_version")
        batch.drop_column("effect_plan_hash")
        batch.drop_column("frozen_effect_plan")
