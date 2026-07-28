from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
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

settings = get_settings()
engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=EventAwareAsyncSession,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
