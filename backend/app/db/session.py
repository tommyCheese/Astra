from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import RunEventRecord
from app.runtime_events import run_event_broker

PENDING_RUN_EVENT_IDS = "astra_pending_run_event_ids"


@event.listens_for(Session, "before_flush")
def collect_pending_run_events(session: Session, _flush_context, _instances) -> None:
    run_ids = {
        record.run_id
        for record in session.new
        if isinstance(record, RunEventRecord)
    }
    if run_ids:
        session.info.setdefault(PENDING_RUN_EVENT_IDS, set()).update(run_ids)


class EventAwareAsyncSession(AsyncSession):
    async def commit(self) -> None:
        await self.flush()
        run_ids = set(self.info.pop(PENDING_RUN_EVENT_IDS, set()))
        await super().commit()
        if run_ids:
            run_event_broker.publish_many(run_ids)

    async def rollback(self) -> None:
        self.info.pop(PENDING_RUN_EVENT_IDS, None)
        await super().rollback()


def configure_sqlite_engine(async_engine: AsyncEngine) -> None:
    """Tune SQLite for concurrent runs and SSE readers."""
    if async_engine.url.get_backend_name() != "sqlite":
        return

    @event.listens_for(async_engine.sync_engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()


def engine_options_for_settings(settings: Settings) -> dict[str, int]:
    database_url = make_url(settings.database_url)
    if (
        database_url.get_backend_name() != "sqlite"
        or database_url.database in {None, "", ":memory:"}
    ):
        return {}
    return {
        "pool_size": settings.sqlite_pool_size,
        "max_overflow": settings.sqlite_max_overflow,
    }


settings = get_settings()
engine = create_async_engine(
    settings.database_url,
    future=True,
    **engine_options_for_settings(settings),
)
configure_sqlite_engine(engine)
SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=EventAwareAsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
