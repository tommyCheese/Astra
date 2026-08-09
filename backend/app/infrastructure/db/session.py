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

from app.application.run_management.projections.events import PublishedRunEvent, run_event_broker
from app.common.core.config import AstraRuntimeSettings, get_settings
from app.infrastructure.db.models.metadata import metadata as metadata
from app.infrastructure.db.models.runs import RunEventRecord

PENDING_RUN_EVENTS = "astra_pending_run_events"


@event.listens_for(Session, "before_flush")
def collect_pending_run_events(session: Session, _flush_context, _instances) -> None:
    records = [record for record in session.new if isinstance(record, RunEventRecord)]
    if records:
        session.info.setdefault(PENDING_RUN_EVENTS, []).extend(records)


class EventAwareAsyncSession(AsyncSession):
    async def commit(self) -> None:
        await self.flush()
        records = self.info.pop(PENDING_RUN_EVENTS, [])
        events = [
            PublishedRunEvent(
                id=record.id,
                run_id=record.run_id,
                type=record.type,
                payload=record.payload,
                created_at=record.created_at.isoformat(),
                agent_execution_id=record.agent_execution_id,
            )
            for record in records
        ]
        await super().commit()
        if events:
            run_event_broker.publish_events(events)

    async def rollback(self) -> None:
        self.info.pop(PENDING_RUN_EVENTS, None)
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


def engine_options_for_settings(settings: AstraRuntimeSettings) -> dict[str, int]:
    database_url = make_url(settings.database_url)
    if database_url.get_backend_name() != "sqlite" or database_url.database in {None, "", ":memory:"}:
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
    # Application services and repositories own their transaction boundaries.
    # Using ``SessionLocal.begin()`` here makes an explicit rollback terminal for
    # the request-scoped context, so a service cannot roll back and then refresh
    # state (the run-cancellation flow is one example).
    async with SessionLocal() as session:
        yield session
