from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_profile import load_agent_profile
from app.core.config import Settings
from app.db.model_base import utc_now
from app.db.models.memory import MemoryConsolidationJobRecord
from app.memory.autodream_profile import autodream_profile_snapshot
from app.memory.consolidation import ConsolidationInputManifest, FrozenMemoryInput
from app.memory.consolidation_generation import deterministic_duplicate_proposal
from app.memory.consolidation_validation import validate_proposal
from app.repositories.memory_consolidation import (
    MemoryConsolidationRepository,
    cooldown_elapsed,
    model_usage_for_job,
    proposal_failure_payload,
    scan_idempotency_key,
)

logger = logging.getLogger("astra.memory.autodream")


class AutoDreamProcessor:
    """Runs one deterministic, model-free consolidation attempt."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def prepare_job(
        self,
        session: AsyncSession,
        job_id: str,
        *,
        owner: str,
    ) -> MemoryConsolidationJobRecord:
        repository = MemoryConsolidationRepository(session)
        claimed = await repository.claim(
            job_id,
            owner=owner,
            lease_seconds=self.settings.agent_memory_autodream_lease_seconds,
        )
        if claimed is None:
            return await repository.require(job_id, refresh=True)
        claimed_id = claimed.id
        claimed_state_version = claimed.state_version
        try:
            return await self._prepare_claimed_job(repository, claimed)
        except Exception as exc:
            await session.rollback()
            logger.exception(
                "autodream.job_failed job_id=%s cause=%s",
                claimed_id,
                type(exc).__name__,
            )
            failed = await repository.fail_running(
                claimed_id,
                expected_state_version=claimed_state_version,
                **proposal_failure_payload(exc),
            )
            if failed is not None:
                return failed
            raise

    async def _prepare_claimed_job(
        self,
        repository: MemoryConsolidationRepository,
        claimed: MemoryConsolidationJobRecord,
    ) -> MemoryConsolidationJobRecord:
        records = await repository.eligible_memories(
            namespace_type=claimed.namespace_type,
            namespace_id=claimed.namespace_id,
            limit=self.settings.agent_memory_autodream_max_records_per_job,
        )
        usage = self._model_usage(claimed)
        profile_snapshot = autodream_profile_snapshot(load_agent_profile())
        if len(records) < self.settings.agent_memory_autodream_min_candidates:
            return await repository.complete_proposal(
                claimed.id,
                expected_state_version=claimed.state_version,
                manifest=None,
                proposal=None,
                validation=self._insufficient_input_validation(len(records)),
                profile_snapshot=profile_snapshot,
                status="insufficient_input",
                model_usage=usage,
            )
        manifest = ConsolidationInputManifest.build(
            namespace_type=claimed.namespace_type,
            namespace_id=claimed.namespace_id,
            items=(FrozenMemoryInput.from_record(record) for record in records),
        )
        return await self._complete_generated_proposal(
            repository, claimed, manifest, profile_snapshot, usage
        )

    async def _complete_generated_proposal(
        self,
        repository: MemoryConsolidationRepository,
        claimed: MemoryConsolidationJobRecord,
        manifest: ConsolidationInputManifest,
        profile_snapshot: dict[str, Any],
        usage: dict[str, Any],
    ) -> MemoryConsolidationJobRecord:
        proposal = deterministic_duplicate_proposal(manifest)
        report = validate_proposal(manifest, proposal)
        if not proposal.operations:
            return await repository.complete_proposal(
                claimed.id,
                expected_state_version=claimed.state_version,
                manifest=manifest,
                proposal=proposal,
                validation=self._no_changes_validation(report.to_dict()),
                profile_snapshot=profile_snapshot,
                status="insufficient_input",
                model_usage=usage,
            )
        error = None
        if not report.valid:
            error = {
                "code": "proposal_validation_failed",
                "message": "AutoDream proposal failed closed",
            }
        return await repository.complete_proposal(
            claimed.id,
            expected_state_version=claimed.state_version,
            manifest=manifest,
            proposal=proposal,
            validation=report.to_dict(),
            profile_snapshot=profile_snapshot,
            status="proposed" if report.valid else "failed",
            model_usage=usage,
            error=error,
        )

    def _model_usage(self, claimed: MemoryConsolidationJobRecord) -> dict[str, Any]:
        usage = model_usage_for_job(claimed, provider="deterministic", calls=0)
        usage["budgets"] = {
            "max_records": self.settings.agent_memory_autodream_max_records_per_job,
            "max_model_calls": self.settings.agent_memory_autodream_max_model_calls,
        }
        return usage

    @staticmethod
    def _insufficient_input_validation(eligible_count: int) -> dict[str, Any]:
        return {
            "valid": False,
            "issues": [
                {
                    "code": "insufficient_input",
                    "detail": "Eligible input count is below the configured minimum",
                }
            ],
            "eligible_count": eligible_count,
        }

    @staticmethod
    def _no_changes_validation(report: dict[str, Any]) -> dict[str, Any]:
        return {
            **report,
            "valid": False,
            "issues": [
                {
                    "code": "no_consolidation_changes",
                    "detail": ("The bounded input contains no deterministic duplicate group"),
                }
            ],
        }


class AutoDreamService:
    """Disabled-by-default scanner and worker with persistent leases."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.settings = settings
        self.session_factory = session_factory
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return self.settings.agent_memory_autodream_enabled

    async def startup(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stop.clear()
        async with self.session_factory() as session:
            recovered = await MemoryConsolidationRepository(session).recover_expired()
        if recovered:
            logger.warning("autodream.recovered count=%s", recovered)
        self._task = asyncio.create_task(
            self._run_loop(),
            name="astra-autodream",
        )

    async def shutdown(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def run_once(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "created_job_ids": [],
                "processed_job_ids": [],
            }
        created = await self._enqueue_eligible()
        processed: list[str] = []
        async with self.session_factory() as session:
            queued = await MemoryConsolidationRepository(session).queued_job_ids(
                limit=self.settings.agent_memory_autodream_batch_size
            )
        processor = AutoDreamProcessor(self.settings)
        for job_id in queued:
            try:
                async with self.session_factory() as session:
                    await processor.prepare_job(
                        session,
                        job_id,
                        owner="autodream-worker",
                    )
                processed.append(job_id)
            except Exception:
                logger.exception(
                    "autodream.job_isolated_failure job_id=%s",
                    job_id,
                )
        return {
            "enabled": True,
            "created_job_ids": created,
            "processed_job_ids": processed,
        }

    async def _enqueue_eligible(self) -> list[str]:
        created: list[str] = []
        async with self.session_factory() as session:
            repository = MemoryConsolidationRepository(session)
            namespaces = await repository.eligible_namespaces(
                minimum_count=self.settings.agent_memory_autodream_min_candidates,
                limit=self.settings.agent_memory_autodream_batch_size,
            )
            for namespace_type, namespace_id, _count in namespaces:
                latest = await repository.latest_job_for_namespace(
                    namespace_type=namespace_type,
                    namespace_id=namespace_id,
                )
                if not cooldown_elapsed(
                    latest,
                    now=utc_now(),
                    cooldown_seconds=(self.settings.agent_memory_autodream_cooldown_seconds),
                ):
                    continue
                fingerprint = await repository.candidate_fingerprint(
                    namespace_type=namespace_type,
                    namespace_id=namespace_id,
                    limit=(self.settings.agent_memory_autodream_max_records_per_job),
                )
                job = await repository.create_job(
                    namespace_type=namespace_type,
                    namespace_id=namespace_id,
                    idempotency_key=scan_idempotency_key(
                        namespace_type,
                        namespace_id,
                        fingerprint,
                    ),
                )
                if job.status == "queued" and job.id not in created:
                    created.append(job.id)
        return created

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                logger.exception("autodream.loop_failure")
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.settings.agent_memory_autodream_scan_seconds,
                )
            except TimeoutError:
                continue
