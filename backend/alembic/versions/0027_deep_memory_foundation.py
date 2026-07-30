"""add deep Memory foundation

Revision ID: 0027_deep_memory_foundation
Revises: 0026_scheduled_jobs_heartbeat
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0027_deep_memory_foundation"
down_revision = "0026_scheduled_jobs_heartbeat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_memory_columns() -> None:
    with op.batch_alter_table("memories") as batch_op:
        batch_op.add_column(sa.Column("memory_key", sa.String(length=240), nullable=True))
        batch_op.add_column(
            sa.Column(
                "namespace_type",
                sa.String(length=40),
                nullable=False,
                server_default="run",
            )
        )
        batch_op.add_column(sa.Column("namespace_id", sa.String(length=120), nullable=True))
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=40),
                nullable=False,
                server_default="active",
            )
        )
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("state_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("importance", sa.Float(), nullable=False, server_default="0.5")
        )
        batch_op.add_column(
            sa.Column("utility_score", sa.Float(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("access_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("supersedes_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column(
                "consolidation_generation",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("revoke_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_memories_supersedes_id",
            "memories",
            ["supersedes_id"],
            ["id"],
        )


def _backfill_memory_namespaces() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT
                m.id AS memory_id,
                m.run_id AS run_id,
                m.scope AS scope,
                m.created_at AS created_at,
                t.id AS task_id,
                t.workspace_id AS task_workspace_id,
                t.created_by AS task_created_by
            FROM memories AS m
            LEFT JOIN runs AS r ON r.id = m.run_id
            LEFT JOIN tasks AS t ON t.id = r.task_id
            """
        )
    ).mappings()
    updates: list[dict] = []
    for row in rows:
        run_id = row["run_id"]
        namespace_type = "run"
        namespace_id = run_id or row["memory_id"]
        if row["scope"] == "task" and row["task_id"]:
            namespace_type = "task"
            namespace_id = row["task_id"]
        elif row["scope"] == "workspace" and row["task_workspace_id"]:
            namespace_type = "workspace"
            namespace_id = row["task_workspace_id"]
        elif row["scope"] == "user" and row["task_created_by"]:
            namespace_type = "user"
            namespace_id = row["task_created_by"]
        updates.append(
            {
                "memory_id": row["memory_id"],
                "namespace_type": namespace_type,
                "namespace_id": namespace_id,
                "created_at": row["created_at"],
            }
        )
    if updates:
        bind.execute(
            sa.text(
                """
                UPDATE memories
                SET memory_key = :memory_id,
                    namespace_type = :namespace_type,
                    namespace_id = :namespace_id,
                    observed_at = :created_at,
                    valid_from = :created_at
                WHERE id = :memory_id
                """
            ),
            updates,
        )
    with op.batch_alter_table("memories") as batch_op:
        batch_op.alter_column("memory_key", existing_type=sa.String(length=240), nullable=False)
        batch_op.alter_column(
            "namespace_id", existing_type=sa.String(length=120), nullable=False
        )
        batch_op.alter_column(
            "observed_at", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.alter_column(
            "valid_from", existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_memories_namespace_key_version",
            ["namespace_type", "namespace_id", "memory_key", "version"],
        )
    op.create_index(
        "ix_memories_namespace_status_kind",
        "memories",
        ["namespace_type", "namespace_id", "status", "kind"],
    )
    op.create_index(
        "ix_memories_key_version",
        "memories",
        ["memory_key", "version"],
    )
    op.create_index(
        "ix_memories_status_expiry",
        "memories",
        ["status", "expires_at"],
    )


def _create_memory_support_tables() -> None:
    op.create_table(
        "memory_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("source_ref", sa.String(length=320), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("tool_call_id", sa.String(length=36), nullable=True),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("source_data", sa.JSON(), nullable=False),
        sa.Column("accessible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["tool_call_id"], ["tool_calls.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "memory_id",
            "source_kind",
            "source_ref",
            name="uq_memory_sources_memory_kind_ref",
        ),
    )
    op.create_index("ix_memory_sources_run", "memory_sources", ["run_id"])
    op.create_index(
        "ix_memory_sources_memory_accessible",
        "memory_sources",
        ["memory_id", "accessible"],
    )

    op.create_table(
        "memory_links",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_memory_id", sa.String(length=36), nullable=False),
        sa.Column("target_memory_id", sa.String(length=36), nullable=False),
        sa.Column("relation", sa.String(length=40), nullable=False),
        sa.Column("link_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_memory_id"], ["memories.id"]),
        sa.ForeignKeyConstraint(["target_memory_id"], ["memories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "relation",
            name="uq_memory_links_source_target_relation",
        ),
    )
    op.create_index(
        "ix_memory_links_target_relation",
        "memory_links",
        ["target_memory_id", "relation"],
    )

    op.create_table(
        "memory_recall_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("shadow", sa.Boolean(), nullable=False),
        sa.Column("namespace_manifest", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("selected", sa.JSON(), nullable=False),
        sa.Column("excluded", sa.JSON(), nullable=False),
        sa.Column("feedback", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["agent_turns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_recall_events_run_created",
        "memory_recall_events",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_memory_recall_events_query_hash",
        "memory_recall_events",
        ["query_hash"],
    )

    op.create_table(
        "memory_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_memory_audit_memory_created",
        "memory_audit_events",
        ["memory_id", "created_at"],
    )

    memories = sa.table(
        "memories",
        sa.column("id", sa.String()),
        sa.column("run_id", sa.String()),
        sa.column("provenance", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    memory_sources = sa.table(
        "memory_sources",
        sa.column("id", sa.String()),
        sa.column("memory_id", sa.String()),
        sa.column("source_kind", sa.String()),
        sa.column("source_ref", sa.String()),
        sa.column("source_hash", sa.String()),
        sa.column("run_id", sa.String()),
        sa.column("source_data", sa.JSON()),
        sa.column("accessible", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        memory_sources.insert().from_select(
            [
                "id",
                "memory_id",
                "source_kind",
                "source_ref",
                "source_hash",
                "run_id",
                "source_data",
                "accessible",
                "created_at",
            ],
            sa.select(
                memories.c.id,
                memories.c.id,
                sa.literal("run"),
                memories.c.run_id,
                memories.c.id,
                memories.c.run_id,
                memories.c.provenance,
                sa.true(),
                memories.c.created_at,
            ).where(memories.c.run_id.is_not(None)),
        )
    )


def _create_consolidation_and_evolution_tables() -> None:
    op.create_table(
        "memory_consolidation_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("namespace_type", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("validation", sa.JSON(), nullable=False),
        sa.Column("profile_snapshot", sa.JSON(), nullable=False),
        sa.Column("model_usage", sa.JSON(), nullable=False),
        sa.Column("publish_result", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rollback_of_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["rollback_of_id"],
            ["memory_consolidation_jobs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_memory_consolidation_idempotency",
        ),
    )
    op.create_index(
        "ix_memory_consolidation_namespace_status",
        "memory_consolidation_jobs",
        ["namespace_type", "namespace_id", "status"],
    )
    op.create_index(
        "ix_memory_consolidation_lease",
        "memory_consolidation_jobs",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "agent_evolution_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_key", sa.String(length=240), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("candidate_type", sa.String(length=40), nullable=False),
        sa.Column("target_component", sa.String(length=80), nullable=False),
        sa.Column("namespace_type", sa.String(length=40), nullable=False),
        sa.Column("namespace_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("source_manifest", sa.JSON(), nullable=False),
        sa.Column("source_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("environment_constraints", sa.JSON(), nullable=False),
        sa.Column("current_evaluation_id", sa.String(length=36), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=True),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["agent_evolution_candidates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "namespace_type",
            "namespace_id",
            "candidate_key",
            "revision",
            name="uq_agent_evolution_namespace_key_revision",
        ),
    )
    op.create_index(
        "ix_agent_evolution_namespace_status",
        "agent_evolution_candidates",
        ["namespace_type", "namespace_id", "status"],
    )

    op.create_table(
        "agent_evolution_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("source_kind", sa.String(length=40), nullable=False),
        sa.Column("source_ref", sa.String(length=320), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("memory_id", sa.String(length=36), nullable=True),
        sa.Column("source_data", sa.JSON(), nullable=False),
        sa.Column("accessible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["candidate_id"], ["agent_evolution_candidates.id"]),
        sa.ForeignKeyConstraint(["memory_id"], ["memories.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_id",
            "source_kind",
            "source_ref",
            name="uq_agent_evolution_sources_candidate_kind_ref",
        ),
    )
    op.create_index("ix_agent_evolution_sources_run", "agent_evolution_sources", ["run_id"])

    op.create_table(
        "agent_evolution_evaluations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("evaluator", sa.String(length=160), nullable=False),
        sa.Column("issuer", sa.String(length=160), nullable=False),
        sa.Column("verdict", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["agent_evolution_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_id",
            "version",
            name="uq_agent_evolution_evaluation_version",
        ),
    )
    op.create_index(
        "ix_agent_evolution_evaluation_digest",
        "agent_evolution_evaluations",
        ["manifest_digest"],
    )

    op.create_table(
        "agent_evolution_audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("expected_state_version", sa.Integer(), nullable=True),
        sa.Column("actual_state_version", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["agent_evolution_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_evolution_audit_candidate_created",
        "agent_evolution_audit_events",
        ["candidate_id", "created_at"],
    )


def upgrade() -> None:
    _add_memory_columns()
    _backfill_memory_namespaces()
    _create_memory_support_tables()
    _create_consolidation_and_evolution_tables()


def downgrade() -> None:
    op.drop_index(
        "ix_agent_evolution_audit_candidate_created",
        table_name="agent_evolution_audit_events",
    )
    op.drop_table("agent_evolution_audit_events")
    op.drop_index(
        "ix_agent_evolution_evaluation_digest",
        table_name="agent_evolution_evaluations",
    )
    op.drop_table("agent_evolution_evaluations")
    op.drop_index("ix_agent_evolution_sources_run", table_name="agent_evolution_sources")
    op.drop_table("agent_evolution_sources")
    op.drop_index(
        "ix_agent_evolution_namespace_status",
        table_name="agent_evolution_candidates",
    )
    op.drop_table("agent_evolution_candidates")
    op.drop_index(
        "ix_memory_consolidation_lease",
        table_name="memory_consolidation_jobs",
    )
    op.drop_index(
        "ix_memory_consolidation_namespace_status",
        table_name="memory_consolidation_jobs",
    )
    op.drop_table("memory_consolidation_jobs")
    op.drop_index(
        "ix_memory_audit_memory_created",
        table_name="memory_audit_events",
    )
    op.drop_table("memory_audit_events")
    op.drop_index(
        "ix_memory_recall_events_query_hash",
        table_name="memory_recall_events",
    )
    op.drop_index(
        "ix_memory_recall_events_run_created",
        table_name="memory_recall_events",
    )
    op.drop_table("memory_recall_events")
    op.drop_index(
        "ix_memory_links_target_relation",
        table_name="memory_links",
    )
    op.drop_table("memory_links")
    op.drop_index(
        "ix_memory_sources_memory_accessible",
        table_name="memory_sources",
    )
    op.drop_index("ix_memory_sources_run", table_name="memory_sources")
    op.drop_table("memory_sources")
    op.drop_index("ix_memories_status_expiry", table_name="memories")
    op.drop_index("ix_memories_key_version", table_name="memories")
    op.drop_index("ix_memories_namespace_status_kind", table_name="memories")
    with op.batch_alter_table("memories") as batch_op:
        batch_op.drop_constraint(
            "uq_memories_namespace_key_version",
            type_="unique",
        )
        batch_op.drop_constraint("fk_memories_supersedes_id", type_="foreignkey")
        batch_op.drop_column("revoke_reason")
        batch_op.drop_column("revoked_at")
        batch_op.drop_column("last_accessed_at")
        batch_op.drop_column("consolidation_generation")
        batch_op.drop_column("supersedes_id")
        batch_op.drop_column("valid_to")
        batch_op.drop_column("valid_from")
        batch_op.drop_column("observed_at")
        batch_op.drop_column("access_count")
        batch_op.drop_column("utility_score")
        batch_op.drop_column("importance")
        batch_op.drop_column("state_version")
        batch_op.drop_column("version")
        batch_op.drop_column("status")
        batch_op.drop_column("namespace_id")
        batch_op.drop_column("namespace_type")
        batch_op.drop_column("memory_key")
