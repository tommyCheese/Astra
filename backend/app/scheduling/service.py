from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.repositories.schedules import ScheduleRepository
from app.scheduling.dispatcher import ScheduledRunDispatcher

logger = logging.getLogger("astra.scheduler")


class SchedulerService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.instance_id = f"scheduler:{uuid.uuid4()}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._semaphore = asyncio.Semaphore(settings.scheduler_max_dispatch_concurrency)
        self.last_scan_error: str | None = None
        self.last_scan_at: datetime | None = None

    async def startup(self) -> None:
        if not self.settings.scheduler_enabled or self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="astra-scheduler")

    async def shutdown(self) -> None:
        self._stopping.set()
        task = self._task
        self._task = None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=5)
        except TimeoutError:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    async def scan_once(self) -> int:
        async with self.session_factory() as session:
            repository = ScheduleRepository(session)
            recovered = await repository.recover_claimed(
                claimed_by=self.instance_id,
                stale_after_seconds=self.settings.scheduler_lease_seconds,
                batch_size=self.settings.scheduler_batch_size,
            )
            remaining = max(0, self.settings.scheduler_batch_size - len(recovered))
            claimed = await repository.claim_due(
                claimed_by=self.instance_id,
                lease_seconds=self.settings.scheduler_lease_seconds,
                batch_size=remaining,
            )
            await repository.cleanup_runs(
                retention_days=self.settings.scheduler_history_retention_days,
            )
        pending = [*recovered, *claimed]
        if not pending:
            self.last_scan_error = None
            self.last_scan_at = datetime.now(timezone.utc)
            return 0
        await asyncio.gather(
            *(self._dispatch(item.id) for item in pending),
        )
        self.last_scan_error = None
        self.last_scan_at = datetime.now(timezone.utc)
        return len(pending)

    def health(self) -> dict[str, object]:
        enabled = self.settings.scheduler_enabled
        running = self._task is not None and not self._task.done()
        return {
            "enabled": enabled,
            "running": running,
            "ready": not enabled or (running and self.last_scan_error is None),
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_scan_error": self.last_scan_error,
        }

    async def _dispatch(self, schedule_run_id: str) -> None:
        async with self._semaphore:
            await ScheduledRunDispatcher(
                self.settings,
                self.session_factory,
            ).dispatch(schedule_run_id)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_scan_error = type(exc).__name__
                logger.exception("scheduler.scan_failed")
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.scheduler_poll_seconds,
                )
