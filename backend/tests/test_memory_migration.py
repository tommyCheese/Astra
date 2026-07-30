from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _alembic_upgrade(database_path: Path, revision: str) -> None:
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def _required_value(name: str, column_type: str):
    normalized = column_type.upper()
    if "JSON" in normalized:
        return "{}"
    if "DATE" in normalized or "TIME" in normalized or name.endswith("_at"):
        return "2026-01-01T00:00:00+00:00"
    if "INT" in normalized:
        return 1
    if any(token in normalized for token in ("REAL", "FLOAT", "NUMERIC")):
        return 0.5
    if "BOOL" in normalized:
        return 1
    if "BLOB" in normalized:
        return b"x"
    return f"{name}-value"


def _insert_required(
    connection: sqlite3.Connection,
    table_name: str,
    overrides: dict,
) -> None:
    columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    values = dict(overrides)
    for _, name, column_type, not_null, default, _ in columns:
        if name in values or default is not None or not not_null:
            continue
        values[name] = _required_value(name, column_type)
    names = list(values)
    placeholders = ", ".join("?" for _ in names)
    connection.execute(
        f"INSERT INTO {table_name} ({', '.join(names)}) VALUES ({placeholders})",
        [values[name] for name in names],
    )


def test_deep_memory_migration_backfills_safe_namespaces_and_sources(tmp_path):
    database_path = tmp_path / "legacy.db"
    _alembic_upgrade(database_path, "0026_scheduled_jobs_heartbeat")

    connection = sqlite3.connect(database_path)
    _insert_required(
        connection,
        "tasks",
        {
            "id": "task-1",
            "title": "Legacy task",
            "description": "Legacy task",
            "workspace_id": "workspace-1",
            "created_by": "user-1",
        },
    )
    _insert_required(
        connection,
        "runs",
        {
            "id": "run-1",
            "task_id": "task-1",
        },
    )
    for memory_id, run_id, scope in (
        ("memory-workspace", "run-1", "workspace"),
        ("memory-user", "run-1", "user"),
        ("memory-isolated", None, "user"),
    ):
        _insert_required(
            connection,
            "memories",
            {
                "id": memory_id,
                "run_id": run_id,
                "scope": scope,
                "kind": "preference",
                "content": memory_id,
                "structured_data": "{}",
                "provenance": '{"run_id": "run-1"}',
                "confidence": 0.8,
            },
        )
    connection.commit()
    connection.close()

    _alembic_upgrade(database_path, "0027_deep_memory_foundation")

    connection = sqlite3.connect(database_path)
    rows = {
        row[0]: row[1:]
        for row in connection.execute(
            """
            SELECT id, namespace_type, namespace_id, memory_key, status, version,
                   observed_at, valid_from
            FROM memories
            """
        )
    }
    assert rows["memory-workspace"][:5] == (
        "workspace",
        "workspace-1",
        "memory-workspace",
        "active",
        1,
    )
    assert rows["memory-user"][:5] == (
        "user",
        "user-1",
        "memory-user",
        "active",
        1,
    )
    assert rows["memory-isolated"][:5] == (
        "run",
        "memory-isolated",
        "memory-isolated",
        "active",
        1,
    )
    assert all(row[5] and row[6] for row in rows.values())

    sources = connection.execute(
        "SELECT memory_id, source_kind, source_ref, accessible FROM memory_sources "
        "ORDER BY memory_id"
    ).fetchall()
    assert sources == [
        ("memory-user", "run", "run-1", 1),
        ("memory-workspace", "run", "run-1", 1),
    ]

    memory_columns = {
        row[1]: row for row in connection.execute("PRAGMA table_info(memories)")
    }
    assert memory_columns["memory_key"][3] == 1
    assert memory_columns["namespace_id"][3] == 1
    assert memory_columns["observed_at"][3] == 1
    assert memory_columns["valid_from"][3] == 1
    memory_indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(memories)")
    }
    assert {
        "ix_memories_namespace_status_kind",
        "ix_memories_key_version",
        "ix_memories_status_expiry",
        "sqlite_autoindex_memories_1",
    }.issubset(memory_indexes)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "memory_sources",
        "memory_links",
        "memory_recall_events",
        "memory_audit_events",
        "memory_consolidation_jobs",
        "agent_evolution_candidates",
        "agent_evolution_sources",
        "agent_evolution_evaluations",
        "agent_evolution_audit_events",
    }.issubset(tables)
    connection.close()
