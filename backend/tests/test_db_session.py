import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.core.config import Settings
from app.infrastructure.db import session as session_module
from app.infrastructure.db.session import configure_sqlite_engine, engine_options_for_settings


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


@pytest.mark.asyncio
async def test_request_session_can_be_reused_after_service_owned_rollback(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_module, "SessionLocal", session_factory)

    dependency = session_module.get_session()
    request_session = await anext(dependency)
    try:
        assert await request_session.scalar(text("SELECT 1")) == 1
        await request_session.rollback()
        assert await request_session.scalar(text("SELECT 2")) == 2
    finally:
        await dependency.aclose()
        await engine.dispose()
