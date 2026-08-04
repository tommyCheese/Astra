import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.model_base import Base
from app.run_management.dispatcher import InProcessRunDispatcher
from app.scheduling.service import SchedulerService


@pytest.mark.asyncio
async def test_scheduler_lifecycle_reports_readiness_and_stops_cleanly(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = SchedulerService(
        Settings(scheduler_poll_seconds=0.1, scheduler_history_retention_days=1),
        sessions,
        InProcessRunDispatcher(),
    )

    assert service.health()["ready"] is False
    await service.startup()
    for _ in range(20):
        if service.last_scan_at is not None:
            break
        await asyncio.sleep(0.01)
    assert service.health() == {
        "enabled": True,
        "running": True,
        "ready": True,
        "last_scan_at": service.last_scan_at.isoformat(),
        "last_scan_error": None,
    }

    await service.shutdown()
    assert service.health()["running"] is False
    await engine.dispose()


def test_disabled_scheduler_is_ready_without_a_worker():
    service = SchedulerService(
        Settings(scheduler_enabled=False),
        None,
        InProcessRunDispatcher(),
    )
    assert service.health()["ready"] is True
    assert service.health()["running"] is False
