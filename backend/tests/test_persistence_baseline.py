import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.db.base import Base

BACKEND_ROOT = Path(__file__).parents[1]
BASELINE_REVISION = "0001_current_baseline"


def _alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "DATABASE_URL": f"sqlite+aiosqlite:///{database_path}",
    }
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

    assert set(Base.metadata.tables) <= tables
    assert revision == (BASELINE_REVISION,)
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
