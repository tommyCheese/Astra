"""Child-local projection and automatic semantic context compaction."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.context_compaction import (
    AgentContextCompactionService,
    TokenAccountingService,
    build_compaction_policy,
    evaluate_compaction_trigger,
)
from app.application.subagents.governance import stable_digest
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.context_compaction import (
    ChildCheckpoint,
    CompactionContextEnvelope,
    CompactionContextItem,
    CompactionContextReference,
    ContextOwnerRole,
    ContinuationManifest,
)
from app.common.schemas.subagents import DelegationContract, SubagentContextManifest
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.model_clients.context_windows import resolve_context_window
from app.infrastructure.repositories.context_compaction import ContextCompactionAttemptRepository


def _observation_id(observation: dict[str, Any], index: int) -> str:
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    normalized = data.get("normalized_output") if isinstance(data, dict) else {}
    key_fields = normalized.get("key_fields") if isinstance(normalized, dict) else {}
    stable = key_fields.get("tool_call_id") if isinstance(key_fields, dict) else None
    if stable:
        return f"child-observation:{stable}"
    digest = hashlib.sha256(
        json.dumps(observation, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:20]
    return f"child-observation:{index}:{digest}"


def _references(observations: list[dict[str, Any]]) -> tuple[CompactionContextReference, ...]:
    result: dict[str, CompactionContextReference] = {}
    for observation in observations:
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        normalized = data.get("normalized_output") if isinstance(data, dict) else {}
        raw = normalized.get("reference") if isinstance(normalized, dict) else None
        if isinstance(raw, dict):
            reference = CompactionContextReference.model_validate(raw)
            result[reference.ref] = reference
        for ref in observation.get("evidence_refs", []) or []:
            if ref:
                result[str(ref)] = CompactionContextReference(kind="evidence", ref=str(ref))
    return tuple(result.values())


async def compact_child_context(
    *,
    session,
    settings: AstraRuntimeSettings,
    model_client,
    execution: AgentExecutionRecord,
    contract: DelegationContract,
    manifest: SubagentContextManifest,
    plan: dict[str, Any],
    usage: dict[str, Any],
    observations: list[dict[str, Any]],
    checkpoint: ChildCheckpoint,
) -> tuple[AgentExecutionRecord, ChildCheckpoint, list[dict[str, Any]]]:
    """Compact only one child's local observations when its own window is pressured."""
    policy = build_compaction_policy(settings, ContextOwnerRole.child_execution)
    if not policy.enabled:
        return execution, checkpoint, observations
    accounting = TokenAccountingService()
    prefix = tuple(
        CompactionContextItem(
            id=f"child-prefix:{item.id}",
            kind=item.kind,
            content=item.content or item.ref or item.summary,
            summary=item.summary,
            token_count=max(0, item.estimated_tokens),
            canonical=True,
            data_labels=tuple(item.data_labels),
            allowed_purposes=tuple(item.allowed_purposes),
        )
        for item in manifest.items
    ) + (
        CompactionContextItem(
            id="child-prefix:local-plan",
            kind="local_plan",
            content=plan,
            token_count=accounting.count_value(plan)[0],
            canonical=True,
        ),
    )
    body = tuple(
        CompactionContextItem(
            id=_observation_id(observation, index),
            kind=str(observation.get("kind") or "observation"),
            content=observation,
            summary=str(observation.get("summary") or "")[:8_000] or None,
            token_count=accounting.count_value(observation)[0],
        )
        for index, observation in enumerate(observations)
    )
    persisted = execution.checkpoint if isinstance(execution.checkpoint, dict) else {}
    metadata = persisted.get("context_compaction") or {}
    prior = checkpoint.model_dump(mode="json")
    window = resolve_context_window(
        settings.model_provider,
        settings.model_name,
        fallback_tokens=settings.context_window_fallback_tokens,
    )
    output_reserve = min(
        settings.context_output_reserve_tokens,
        window.max_output_tokens or settings.context_output_reserve_tokens,
    )
    envelope = CompactionContextEnvelope(
        owner_type=ContextOwnerRole.child_execution,
        owner_id=execution.id,
        purpose=contract.request.objective,
        protected_prefix=prefix,
        prior_checkpoint=prior,
        compactable_body=body,
        reference_manifest=_references(observations),
        accounting=accounting.account(
            context_window=window.tokens,
            output_reserve=output_reserve,
            compaction_output_reserve=settings.context_compaction_output_reserve_tokens,
            protected_prefix=prefix,
            checkpoint=(
                CompactionContextItem(
                    id=f"child-checkpoint:{execution.id}",
                    kind="child_context_checkpoint",
                    content=prior,
                    token_count=accounting.count_value(prior)[0],
                ),
            ),
            body=body,
        ),
        continuation=ContinuationManifest(
            owner_type=ContextOwnerRole.child_execution,
            owner_id=execution.id,
            state_version=execution.state_version,
            cancellation_epoch=execution.cancellation_epoch,
            window_number=int(metadata.get("window_number", 0)),
            source_item_ids=tuple(item.id for item in body),
            remaining_budget=dict(execution.budget_envelope or usage),
            contract_hash=contract.contract_hash,
            manifest_hash=stable_digest(manifest.model_dump(mode="json")),
            catalog_digests={
                "tool": manifest.tool_catalog_digest,
                "skill": manifest.skill_catalog_digest,
            },
        ),
    )
    if not evaluate_compaction_trigger(envelope.accounting, policy).should_compact:
        return execution, checkpoint, observations
    attempts = ContextCompactionAttemptRepository(session)
    result = await AgentContextCompactionService(attempts, accounting=accounting).compact(
        envelope,
        policy,
        generate=model_client.generate_context_checkpoint,
        install=attempts.install_agent_checkpoint,
    )
    if result.checkpoint is None:
        return execution, checkpoint, observations
    # Continuation answers are typed, parent-bound state.  They are never left
    # to a lossy model summary and remain available after a V1→V2 migration.
    compacted_checkpoint = result.checkpoint.model_copy(
        update={
            "continuation_round_trips": checkpoint.continuation_round_trips,
            "continuation_answers": checkpoint.continuation_answers,
            "remaining_budget": dict(execution.budget_envelope or usage),
        }
    )
    retained = set(result.retained_tail_ids)
    visible = [item.content for item in body if item.id in retained]
    current = await session.get(AgentExecutionRecord, execution.id)
    assert current is not None
    current.checkpoint = {
        **(current.checkpoint or {}),
        "observations": visible,
        "context_checkpoint": compacted_checkpoint.model_dump(mode="json"),
    }
    await session.flush()
    return current, compacted_checkpoint, visible
