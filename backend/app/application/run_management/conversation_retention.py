from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.run_management.conversation_lifecycle import ConversationLifecycleService
from app.common.core.config import Settings
from app.infrastructure.repositories.conversations import ConversationRepository

logger = logging.getLogger("astra.conversation_retention")


@dataclass(frozen=True)
class ConversationRetentionSweep:
    selected: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: int = 0


class ConversationRetentionService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lifecycle: ConversationLifecycleService | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.lifecycle = lifecycle or ConversationLifecycleService(settings)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        if not self.settings.conversation_retention_enabled:
            logger.info("conversation_retention.disabled")
            return
        if self._task is not None:
            return
        self._stop.clear()
        await self._sweep_safely(trigger="startup")
        self._task = asyncio.create_task(
            self._run(), name="astra-conversation-retention"
        )

    async def shutdown(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        await task
        self._task = None

    async def _run(self) -> None:
        interval = self.settings.conversation_retention_sweep_seconds
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                await self._sweep_safely(trigger="periodic")

    async def _sweep_safely(self, *, trigger: str) -> ConversationRetentionSweep:
        try:
            result = await self.sweep()
        except Exception:
            logger.exception("conversation_retention.sweep_failed trigger=%s", trigger)
            return ConversationRetentionSweep(failed=1)
        logger.info(
            "conversation_retention.sweep_complete "
            "trigger=%s selected=%s deleted=%s skipped=%s failed=%s",
            trigger,
            result.selected,
            result.deleted,
            result.skipped,
            result.failed,
        )
        return result

    async def sweep(
        self, *, now: datetime | None = None
    ) -> ConversationRetentionSweep:
        if not self.settings.conversation_retention_enabled:
            return ConversationRetentionSweep()
        reference = now or datetime.now(timezone.utc)
        cutoff = reference - timedelta(days=self.settings.conversation_retention_days)
        async with self.session_factory() as session:
            candidate_ids = await ConversationRepository(
                session
            ).retention_candidate_ids(
                cutoff=cutoff,
                limit=self.settings.conversation_retention_batch_size,
            )

        deleted = skipped = failed = 0
        for conversation_id in candidate_ids:
            try:
                async with self.session_factory() as session:
                    repo = ConversationRepository(session)
                    if not await repo.is_retention_eligible(
                        conversation_id, cutoff=cutoff
                    ):
                        skipped += 1
                        continue
                    task = await repo.get(conversation_id)
                    if task is None:
                        skipped += 1
                        continue
                    await self.lifecycle.delete(repo, task)
                    deleted += 1
            except Exception:
                failed += 1
                logger.warning(
                    "conversation_retention.delete_failed conversation_id=%s",
                    conversation_id,
                    exc_info=True,
                )

        return ConversationRetentionSweep(
            selected=len(candidate_ids),
            deleted=deleted,
            skipped=skipped,
            failed=failed,
        )
