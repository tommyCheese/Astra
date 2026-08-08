import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.infrastructure.db.model_base import AstraOrmRecordBase

BACKEND_ROOT = Path(__file__).parents[1]
BASELINE_REVISION = "0001_current_baseline"
HEAD_REVISION = "0011_fast_agent_runtime"


def _alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    for retired in (
        "WEB_SEARCH_PROVIDER",
        "WEB_SEARCH_API_KEY",
        "GOOGLE_SEARCH_API_KEY",
        "GOOGLE_SEARCH_ENGINE_ID",
        "GOOGLE_SEARCH_RESULT_COUNT",
        "GOOGLE_SEARCH_LANGUAGE",
        "GOOGLE_SEARCH_REGION",
        "GOOGLE_SEARCH_SAFE",
        "CRAWLER_MAX_CONTENT_CHARS",
        "CRAWLER_MAX_RESPONSE_BYTES",
        "CRAWLER_MIN_QUALITY_CHARS",
        "CRAWLER_ALLOW_PROXY_FAKE_IP",
        "SANDBOX_WEB_RUNTIME_IMAGE",
    ):
        environment.pop(retired, None)
    environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{database_path}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_current_baseline_creates_the_complete_orm_schema(tmp_path: Path):
    database_path = tmp_path / "astra.db"

    upgraded = _alembic(database_path, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        memory_columns = {row[1] for row in connection.execute("PRAGMA table_info(memories)")}
        recall_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memory_recall_events)")
        }
    finally:
        connection.close()

    assert set(AstraOrmRecordBase.metadata.tables) <= tables
    assert revision == (HEAD_REVISION,)
    assert "workspace_id" not in memory_columns
    assert "shadow" not in recall_columns

    checked = _alembic(database_path, "check")
    assert checked.returncode == 0, checked.stderr


def test_obsolete_revision_has_no_upgrade_path(tmp_path: Path):
    database_path = tmp_path / "obsolete.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32))")
        connection.execute(
            "INSERT INTO alembic_version(version_num) VALUES ('0028_memory_session_scope')"
        )
        connection.commit()
    finally:
        connection.close()

    upgraded = _alembic(database_path, "upgrade", "head")

    assert upgraded.returncode != 0
    assert "reset the database" in upgraded.stderr


def test_legacy_chart_tool_state_is_migrated_to_canonical_identity(tmp_path: Path):
    database_path = tmp_path / "legacy-tool-state.db"
    upgraded = _alembic(database_path, "upgrade", "0008_dynamic_tool_provider_settings")
    assert upgraded.returncode == 0, upgraded.stderr

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO tool_settings (tool_name, enabled, created_at, updated_at)
            VALUES ('chart_render', 0, '2026-08-01 00:00:00', '2026-08-01 00:00:00')
            """
        )
        connection.commit()
    finally:
        connection.close()

    migrated = _alembic(database_path, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT tool_name, enabled FROM tool_settings ORDER BY tool_name"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [("chart.render", 0)]


def test_retired_web_settings_are_removed_without_touching_other_settings(tmp_path: Path):
    database_path = tmp_path / "retired-web-settings.db"
    upgraded = _alembic(database_path, "upgrade", "0009_remove_legacy_tool_settings")
    assert upgraded.returncode == 0, upgraded.stderr

    connection = sqlite3.connect(database_path)
    try:
        connection.executemany(
            """
            INSERT INTO tool_settings (tool_name, enabled, created_at, updated_at)
            VALUES (?, 1, '2026-08-01 00:00:00', '2026-08-01 00:00:00')
            """,
            [("web_search",), ("web_fetch",), ("chart.render",)],
        )
        connection.executemany(
            """
            INSERT INTO tool_provider_settings (
                provider_id, enabled, configuration, configuration_revision,
                created_at, updated_at
            ) VALUES (?, 1, '{}', 1, '2026-08-01 00:00:00', '2026-08-01 00:00:00')
            """,
            [("astra.web",), ("astra.chart",)],
        )
        connection.commit()
    finally:
        connection.close()

    migrated = _alembic(database_path, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    connection = sqlite3.connect(database_path)
    try:
        tools = connection.execute(
            "SELECT tool_name FROM tool_settings ORDER BY tool_name"
        ).fetchall()
        providers = connection.execute(
            "SELECT provider_id FROM tool_provider_settings ORDER BY provider_id"
        ).fetchall()
    finally:
        connection.close()

    assert tools == [("chart.render",)]
    assert providers == [("astra.chart",)]


def test_subagent_migration_backfills_root_execution_and_lineage(tmp_path: Path):
    database_path = tmp_path / "existing.db"
    baseline = _alembic(database_path, "upgrade", BASELINE_REVISION)
    assert baseline.returncode == 0, baseline.stderr

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO tasks (
                id, title, description, status, preferred_answer_mode, title_source,
                context_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task-existing",
                "Existing task",
                "Existing task",
                "created",
                "trusted",
                "auto",
                "{}",
                "2026-08-01 00:00:00",
                "2026-08-01 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO runs (
                id, task_id, status, mode, answer_mode, execution_profile, model_policy,
                agent_profile_snapshot, reasoning_policy, task_contract, plan_graph,
                agent_state, state_version, task_adapter, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-existing",
                "task-existing",
                "executing",
                "web_agent",
                "trusted",
                "{}",
                "{}",
                "{}",
                "{}",
                '{"original_goal":"existing"}',
                "{}",
                '{"version":3,"budget_usage":{"model_calls":1}}',
                3,
                "web",
                "2026-08-01 00:00:00",
                "2026-08-01 00:01:00",
            ),
        )
        connection.execute(
            "INSERT INTO run_events (run_id, type, payload, created_at) VALUES (?, ?, ?, ?)",
            ("run-existing", "run.created", "{}", "2026-08-01 00:00:00"),
        )
        connection.commit()
    finally:
        connection.close()

    upgraded = _alembic(database_path, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    connection = sqlite3.connect(database_path)
    try:
        root = connection.execute(
            """
            SELECT id, execution_type, root_slot, depth, status, phase, contract, checkpoint
            FROM agent_executions WHERE run_id = ?
            """,
            ("run-existing",),
        ).fetchone()
        event_execution_id = connection.execute(
            "SELECT agent_execution_id FROM run_events WHERE run_id = ?",
            ("run-existing",),
        ).fetchone()
    finally:
        connection.close()

    assert root is not None
    assert root[1:6] == ("root", "root", 0, "running", "executing")
    assert "existing" in root[6]
    assert '"version": 3' in root[7]
    assert event_execution_id == (root[0],)

    downgraded = _alembic(database_path, "downgrade", BASELINE_REVISION)
    assert downgraded.returncode == 0, downgraded.stderr
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        run_row = connection.execute(
            "SELECT id, status FROM runs WHERE id = ?", ("run-existing",)
        ).fetchone()
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(run_events)")
        }
    finally:
        connection.close()

    assert "agent_executions" not in tables
    assert "agent_execution_id" not in event_columns
    assert run_row == ("run-existing", "executing")
