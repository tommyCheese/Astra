from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.session import configure_sqlite_engine, engine_options_for_settings


async def test_sqlite_engine_uses_concurrent_runtime_pragmas(tmp_path):
    database_path = tmp_path / "runtime.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    configure_sqlite_engine(engine)

    async with engine.connect() as connection:
        journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
        synchronous = await connection.scalar(text("PRAGMA synchronous"))
        busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
        temp_store = await connection.scalar(text("PRAGMA temp_store"))

    await engine.dispose()
    assert journal_mode == "wal"
    assert synchronous == 1
    assert busy_timeout == 5000
    assert temp_store == 2


def test_file_sqlite_uses_bounded_connection_pool():
    settings = Settings(database_url="sqlite+aiosqlite:///runtime.db")

    assert engine_options_for_settings(settings) == {
        "pool_size": 5,
        "max_overflow": 0,
    }


def test_non_file_databases_keep_driver_pool_defaults():
    assert engine_options_for_settings(
        Settings(database_url="sqlite+aiosqlite:///:memory:")
    ) == {}
    assert engine_options_for_settings(
        Settings(database_url="postgresql+asyncpg://astra@db/astra")
    ) == {}
