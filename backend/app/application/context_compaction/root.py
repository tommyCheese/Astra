from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.application.context_compaction import (
    AgentContextCompactionService,
    TokenAccountingService,
    build_compaction_policy,
    evaluate_compaction_trigger,
    project_shadow_compaction,
)
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.context_compaction import (
    CompactionContextEnvelope,
    CompactionContextItem,
    CompactionContextReference,
    ContextOwnerRole,
    ContinuationManifest,
)
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.model_clients.context_windows import resolve_context_window
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.context_compaction import ContextCompactionAttemptRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


@dataclass
class RootCompactionState:
    envelope: CompactionContextEnvelope
    accounting: TokenAccountingService
    body: list[CompactionContextItem]
    observations_by_id: dict[str, dict[str, Any]]
    prior_checkpoint: dict[str, Any] | None
    metadata: dict[str, Any]


def _item_id(observation: dict[str, Any], index: int) -> str:
    data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
    normalized = data.get("normalized_output") if isinstance(data, dict) else {}
    key_fields = normalized.get("key_fields") if isinstance(normalized, dict) else {}
    stable = (key_fields.get("tool_call_id") if isinstance(key_fields, dict) else None) or observation.get("id")
    if stable:
        return f"observation:{stable}"
    digest = hashlib.sha256(json.dumps(observation, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:20]
    return f"observation:{index}:{digest}"


def _reference_manifest(observations: list[dict[str, Any]]) -> tuple[CompactionContextReference, ...]:
    references: dict[str, CompactionContextReference] = {}
    for observation in observations:
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        normalized = data.get("normalized_output") if isinstance(data, dict) else None
        raw = normalized.get("reference") if isinstance(normalized, dict) else None
        if isinstance(raw, dict):
            reference = CompactionContextReference.model_validate(raw)
            references[reference.ref] = reference
        if observation.get("kind") != "subagent_join" or observation.get("status") != "succeeded":
            continue
        join_id = data.get("join_id") if isinstance(data, dict) else None
        execution_ids = data.get("source_execution_ids", ()) if isinstance(data, dict) else ()
        if not join_id or not isinstance(execution_ids, list | tuple):
            continue
        for execution_id in execution_ids:
            if not execution_id:
                continue
            ref = f"subagent_join:{join_id}:child:{execution_id}"
            references[ref] = CompactionContextReference(
                kind="child_result",
                ref=ref,
            )
    return tuple(references.values())


_ContextItems = tuple[CompactionContextItem, ...]


def _protected_prefix(goal: str, context: dict[str, Any], trusted: bool, accounting: TokenAccountingService) -> _ContextItems:
    sections: list[tuple[str, Any]] = [
        ("current_request", goal),
        (
            "authorization",
            {
                "boundary": "Use only the frozen tool manifests and current permission scope.",
                "tool_names": sorted(context.get("tool_manifests", {})),
            },
        ),
        ("skills", context.get("active_skills", [])),
        (
            "budget",
            context.get("reasoning_policy", {}).get("effective", {}).get("budgets", {}),
        ),
        (
            "canonical_runtime_state",
            {
                "state_version": context.get("state_version"),
                "plan_version": context.get("plan_version"),
                "active_node": context.get("active_node"),
            },
        ),
    ]
    if trusted:
        sections.extend(
            [
                ("task_contract", context.get("task_contract", {})),
                ("profile_snapshot", context.get("agent_profile_snapshot", {})),
                (
                    "skill_snapshot",
                    {
                        "catalog": context.get("skill_catalog", []),
                        "active": context.get("active_skills", []),
                    },
                ),
                (
                    "permissions",
                    {
                        "candidate_tools": {
                            name: {
                                key: manifest.get(key)
                                for key in (
                                    "permission",
                                    "permissions",
                                    "side_effect_level",
                                    "risk",
                                    "capabilities",
                                )
                                if manifest.get(key) is not None
                            }
                            for name, manifest in sorted(context.get("tool_manifests", {}).items())
                            if isinstance(manifest, dict)
                        },
                        "selection": context.get("tool_selection", {}),
                        "execution_profile_permissions": context.get("execution_profile", {}).get("permission_bundle"),
                    },
                ),
                ("plan_graph", context.get("plan_graph", {})),
                (
                    "agent_state_versions",
                    {
                        "run_state_version": context.get("state_version"),
                        "plan_version": context.get("plan_version"),
                        "agent_state_version": context.get("agent_state", {}).get("version"),
                        "active_plan_version": context.get("agent_state", {}).get("active_plan_version"),
                        "active_executions": context.get("agent_state", {}).get("active_executions", []),
                    },
                ),
                (
                    "completion_gate",
                    {
                        "success_criteria": context.get("task_contract", {}).get("success_criteria", []),
                        "verification_requirements": context.get("task_contract", {}).get("verification_requirements", []),
                        "evidence_pack": context.get("evidence_pack", {}),
                        "subagent_active_groups": context.get("subagent_active_groups", []),
                        "waiting_state": context.get("agent_state", {}).get("waiting_state"),
                        "terminal_intent": context.get("agent_state", {}).get("terminal_intent"),
                    },
                ),
            ]
        )
    items = []
    for kind, content in sections:
        count, _, _ = accounting.count_value(content)
        items.append(
            CompactionContextItem(
                id=f"root:{kind}",
                kind=kind,
                content=content,
                token_count=count,
                canonical=True,
            )
        )
    return tuple(items)


async def compact_root_context(
    *,
    repo: RunUnitOfWork,
    settings: AstraRuntimeSettings,
    model_client: ModelClient,
    run_id: str,
    goal: str,
    context: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply the root compaction policy immediately before a model call.

    Canonical Run/Plan state remains untouched. Only the lossy model projection is
    replaced by a checkpoint and a deterministic retained/new observation tail.
    """
    policy = build_compaction_policy(settings, ContextOwnerRole.root_execution)
    if not policy.enabled:
        return context

    root = await AgentExecutionRepository(repo.session).root_for_run(run_id)
    if root is None:
        return context
    state = await _prepare_compaction_state(repo, settings, root, run_id, goal, context, observations)
    await _perform_compaction(repo, model_client, run_id, context, policy, state)
    return _project_compacted_context(context, state)


async def _prepare_compaction_state(repo, settings, root, run_id, goal, context, observations) -> RootCompactionState:
    accounting = TokenAccountingService()
    prefix = _protected_prefix(
        goal=goal,
        context=context,
        trusted=context.get("answer_mode") != "standard",
        accounting=accounting,
    )
    body, observations_by_id = _observation_items(observations, accounting)
    root_checkpoint = root.checkpoint if isinstance(root.checkpoint, dict) else {}
    prior_checkpoint = root_checkpoint.get("context_checkpoint")
    metadata = root_checkpoint.get("context_compaction") or {}
    checkpoint_items = _checkpoint_items(root.id, prior_checkpoint, accounting)
    window = resolve_context_window(
        settings.model_provider,
        settings.model_name,
        fallback_tokens=settings.context_window_fallback_tokens,
    )
    output_reserve = min(
        settings.context_output_reserve_tokens,
        window.max_output_tokens or settings.context_output_reserve_tokens,
    )
    token_accounting = accounting.account(
        context_window=window.tokens,
        output_reserve=output_reserve,
        compaction_output_reserve=settings.context_compaction_output_reserve_tokens,
        protected_prefix=prefix,
        checkpoint=checkpoint_items,
        body=body,
    )
    run = await repo.require_run_core(run_id)
    action_idempotency_keys = tuple(
        value
        for value in (
            await repo.session.scalars(
                select(AgentTurnRecord.idempotency_key)
                .where(
                    AgentTurnRecord.run_id == run_id,
                    AgentTurnRecord.idempotency_key.is_not(None),
                )
                .order_by(AgentTurnRecord.turn_index)
            )
        ).all()
        if value
    )
    continuation = ContinuationManifest(
        owner_type=ContextOwnerRole.root_execution,
        owner_id=root.id,
        state_version=root.state_version,
        cancellation_epoch=root.cancellation_epoch,
        window_number=int(metadata.get("window_number", 0)),
        source_item_ids=tuple(item.id for item in body),
        action_idempotency_keys=action_idempotency_keys,
        waiting_state=dict(run.waiting_state or {}),
        remaining_budget=dict(root.budget_envelope or {}),
    )
    envelope = CompactionContextEnvelope(
        owner_type=ContextOwnerRole.root_execution,
        owner_id=root.id,
        purpose=goal,
        protected_prefix=prefix,
        prior_checkpoint=prior_checkpoint if isinstance(prior_checkpoint, dict) else None,
        compactable_body=tuple(body),
        reference_manifest=_reference_manifest(observations),
        accounting=token_accounting,
        continuation=continuation,
    )
    return RootCompactionState(envelope, accounting, body, observations_by_id, prior_checkpoint, metadata)


def _observation_items(observations, accounting):
    body = []
    observations_by_id = {}
    for index, observation in enumerate(observations):
        item_id = _item_id(observation, index)
        count, _, _ = accounting.count_value(observation)
        body.append(
            CompactionContextItem(
                id=item_id,
                kind=str(observation.get("kind") or "observation"),
                content=observation,
                summary=str(observation.get("summary") or "")[:8_000] or None,
                token_count=count,
            )
        )
        observations_by_id[item_id] = observation
    return body, observations_by_id


def _checkpoint_items(root_id, checkpoint, accounting):
    if not isinstance(checkpoint, dict):
        return ()
    checkpoint_count, _, _ = accounting.count_value(checkpoint)
    return (
        CompactionContextItem(
            id=f"root-checkpoint:{root_id}",
            kind="root_context_checkpoint_v2",
            content=checkpoint,
            token_count=checkpoint_count,
        ),
    )


async def _perform_compaction(repo, model_client, run_id, context, policy, state) -> None:
    decision = evaluate_compaction_trigger(state.envelope.accounting, policy)
    if not decision.should_compact:
        return
    attempts = ContextCompactionAttemptRepository(repo.session)
    if policy.shadow_mode:
        projection = project_shadow_compaction(
            state.envelope.accounting,
            policy,
            expected_checkpoint_tokens=max(256, state.envelope.accounting.protected_prefix_tokens // 4),
        )
        context["context_compaction"] = projection.model_dump(mode="json")
        return
    result = await AgentContextCompactionService(attempts, accounting=state.accounting).compact(
        state.envelope,
        policy,
        generate=model_client.generate_context_checkpoint,
        install=attempts.install_agent_checkpoint,
    )
    await repo.add_event(
        run_id,
        "context.compaction",
        {
            "owner_role": "root_execution",
            "status": result.status.value,
            "reasons": list(decision.reasons),
            "token_before": result.token_before,
            "token_after": result.token_after,
            "implementation": result.implementation.value if result.implementation else None,
            "retained_tail_size": len(result.retained_tail_ids),
            "failure_code": result.failure_code,
        },
    )
    await repo.session.commit()
    if result.checkpoint is not None:
        state.prior_checkpoint = result.checkpoint.model_dump(mode="json")
        state.metadata = {
            "source_item_ids": [item.id for item in state.body],
            "retained_tail_ids": list(result.retained_tail_ids),
        }


def _project_compacted_context(context, state: RootCompactionState):
    if not isinstance(state.prior_checkpoint, dict):
        return context
    source_ids = set(state.metadata.get("source_item_ids", []))
    retained_ids = set(state.metadata.get("retained_tail_ids", []))
    visible_ids = retained_ids | (set(state.observations_by_id) - source_ids)
    visible = [state.observations_by_id[item.id] for item in state.body if item.id in visible_ids]
    context["context_checkpoint"] = state.prior_checkpoint
    context["observations"] = visible
    if isinstance(context.get("agent_state"), dict):
        context["agent_state"] = {**context["agent_state"], "observations": visible}
    return context
