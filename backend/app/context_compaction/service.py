from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from app.context_compaction.accounting import TokenAccountingService
from app.context_compaction.emergency import deterministic_emergency_checkpoint
from app.context_compaction.parsing import extract_json_object
from app.context_compaction.policy import CompactionPolicy, recent_tail_budget, select_recent_tail
from app.context_compaction.prompts import build_compaction_prompt
from app.context_compaction.validation import CheckpointV2, validate_checkpoint_payload
from app.repositories.context_compaction import ContextCompactionAttemptRepository
from app.schemas.context_compaction import (
    CompactionImplementation,
    CompactionLifecycleStatus,
    CompactionMetadata,
    ContextEnvelope,
)


class ContextCapacityError(RuntimeError):
    pass


class CompactionGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    output: str | dict[str, Any]
    provider: str
    model: str
    usage: dict[str, Any] = {}
    cost_usd: float | None = None


class OrdinaryCompactionGenerator(Protocol):
    async def __call__(self, prompt: str) -> CompactionGeneration: ...


InstallCheckpoint = Callable[[ContextEnvelope, CheckpointV2, tuple[str, ...]], Awaitable[bool]]


@dataclass(frozen=True)
class CompactionResult:
    status: CompactionLifecycleStatus
    checkpoint: CheckpointV2 | None
    retained_tail_ids: tuple[str, ...]
    token_before: int
    token_after: int | None
    reused: bool = False
    implementation: CompactionImplementation | None = None
    failure_code: str | None = None


class AgentContextCompactionService:
    def __init__(
        self,
        attempts: ContextCompactionAttemptRepository,
        *,
        accounting: TokenAccountingService | None = None,
    ):
        self.attempts = attempts
        self.accounting = accounting or TokenAccountingService()

    async def compact(
        self,
        envelope: ContextEnvelope,
        policy: CompactionPolicy,
        *,
        generate: OrdinaryCompactionGenerator,
        install: InstallCheckpoint,
    ) -> CompactionResult:
        base_metadata = _compaction_metadata(envelope, policy)
        completed = await self.attempts.completed(base_metadata)
        if completed is not None and completed.checkpoint:
            return _reused_compaction_result(completed, envelope)

        tail = select_recent_tail(
            envelope.compactable_body,
            recent_tail_budget(policy, envelope.accounting),
        )
        metadata = base_metadata.model_copy(
            update={"retained_tail_ids": tuple(i.id for i in tail.items)}
        )
        attempt = await self.attempts.start(metadata)
        (
            checkpoint,
            generation,
            failure,
            implementation,
            duration_ms,
        ) = await self._generate_checkpoint(envelope, policy, generate)
        if checkpoint is None:
            return await self._finish_generation_failure(
                attempt, envelope, policy, tail, generation, failure, duration_ms
            )

        checkpoint_tokens, _, _ = self.accounting.count_value(checkpoint.model_dump(mode="json"))
        token_after = (
            envelope.accounting.protected_prefix_tokens + checkpoint_tokens + tail.token_count
        )
        recovery_target = int(envelope.accounting.usable_input * policy.recovery_ratio)
        if token_after > recovery_target and tail.items:
            tail = select_recent_tail(tail.items, max(0, tail.token_count // 2))
            token_after = (
                envelope.accounting.protected_prefix_tokens + checkpoint_tokens + tail.token_count
            )
        if token_after > recovery_target:
            await self.attempts.finish(
                attempt,
                status="failed",
                checkpoint=checkpoint.model_dump(mode="json"),
                token_after=token_after,
                duration_ms=duration_ms,
                failure_stage="post_budget",
                failure_code="recovery_waterline_not_met",
            )
            raise ContextCapacityError(policy.capacity_exit.value)

        tail_ids = tuple(item.id for item in tail.items)
        installed = await install(envelope, checkpoint, tail_ids)
        if not installed:
            return await self._finish_superseded(
                attempt, envelope, checkpoint, tail_ids, token_after, duration_ms, implementation
            )
        attempt.implementation = implementation.value
        attempt.generation_provider = generation.provider if generation else "astra"
        attempt.generation_model = generation.model if generation else "deterministic"
        await self.attempts.finish(
            attempt,
            status="completed",
            checkpoint=checkpoint.model_dump(mode="json"),
            token_after=token_after,
            duration_ms=duration_ms,
            usage=generation.usage if generation else {},
            cost_usd=generation.cost_usd if generation else None,
        )
        return CompactionResult(
            status=CompactionLifecycleStatus.completed,
            checkpoint=checkpoint,
            retained_tail_ids=tuple(item.id for item in tail.items),
            token_before=envelope.accounting.total_tokens,
            token_after=token_after,
            implementation=implementation,
        )

    async def _finish_superseded(
        self, attempt, envelope, checkpoint, tail_ids, token_after, duration_ms, implementation
    ) -> CompactionResult:
        await self.attempts.finish(
            attempt,
            status="superseded",
            checkpoint=checkpoint.model_dump(mode="json"),
            token_after=token_after,
            duration_ms=duration_ms,
            failure_stage="install",
            failure_code="state_or_cancellation_epoch_changed",
        )
        return CompactionResult(
            status=CompactionLifecycleStatus.superseded,
            checkpoint=None,
            retained_tail_ids=tail_ids,
            token_before=envelope.accounting.total_tokens,
            token_after=token_after,
            implementation=implementation,
        )

    async def _generate_checkpoint(self, envelope, policy, generate):
        started = time.perf_counter()
        checkpoint = generation = failure = None
        implementation = CompactionImplementation.astra_semantic
        for _ in range(policy.max_attempts):
            try:
                generation = await generate(build_compaction_prompt(envelope, policy))
                checkpoint = validate_checkpoint_payload(
                    extract_json_object(generation.output), envelope
                )
                break
            except Exception as exc:  # bounded provider/validation failure
                failure = exc
        if checkpoint is None and policy.deterministic_emergency:
            try:
                checkpoint = deterministic_emergency_checkpoint(envelope)
                checkpoint = validate_checkpoint_payload(
                    checkpoint.model_dump(mode="json"), envelope
                )
                implementation = CompactionImplementation.deterministic_emergency
            except Exception as exc:
                failure = exc
        duration_ms = round((time.perf_counter() - started) * 1000)
        return checkpoint, generation, failure, implementation, duration_ms

    async def _finish_generation_failure(
        self, attempt, envelope, policy, tail, generation, failure, duration_ms
    ) -> CompactionResult:
        failure_code = type(failure).__name__ if failure else "unknown"
        await self.attempts.finish(
            attempt,
            status="failed",
            duration_ms=duration_ms,
            usage=generation.usage if generation else {},
            cost_usd=generation.cost_usd if generation else None,
            failure_stage="validation_or_generation",
            failure_code=failure_code,
        )
        if envelope.accounting.total_tokens >= envelope.accounting.usable_input:
            raise ContextCapacityError(policy.capacity_exit.value) from failure
        return CompactionResult(
            status=CompactionLifecycleStatus.failed,
            checkpoint=None,
            retained_tail_ids=tuple(item.id for item in tail.items),
            token_before=envelope.accounting.total_tokens,
            token_after=None,
            failure_code=failure_code,
        )


def _input_digest(envelope: ContextEnvelope) -> str:
    payload = envelope.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _compaction_metadata(envelope: ContextEnvelope, policy: CompactionPolicy) -> CompactionMetadata:
    return CompactionMetadata(
        owner_type=envelope.owner_type,
        owner_id=envelope.owner_id,
        window_number=envelope.continuation.window_number,
        input_digest=_input_digest(envelope),
        policy_version=policy.version,
        checkpoint_schema_version=2,
        implementation=CompactionImplementation.astra_semantic,
        status=CompactionLifecycleStatus.started,
        state_version=envelope.continuation.state_version,
        cancellation_epoch=envelope.continuation.cancellation_epoch,
        token_before=envelope.accounting.total_tokens,
        source_item_ids=tuple(item.id for item in envelope.compactable_body),
    )


def _reused_compaction_result(completed, envelope) -> CompactionResult:
    checkpoint = validate_checkpoint_payload(completed.checkpoint, envelope)
    return CompactionResult(
        status=CompactionLifecycleStatus.completed,
        checkpoint=checkpoint,
        retained_tail_ids=tuple(completed.retained_tail_ids or ()),
        token_before=completed.token_before,
        token_after=completed.token_after,
        reused=True,
        implementation=CompactionImplementation(completed.implementation),
    )
