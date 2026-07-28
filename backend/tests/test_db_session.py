from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.session import configure_sqlite_engine


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
