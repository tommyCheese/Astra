from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.application.subagents.governance import FrozenChildCatalog, stable_digest
from app.common.schemas.subagents import (
    DelegationContract,
    EffectiveDelegationScope,
    SubagentBudgetEnvelope,
    SubagentContextCheckpoint,
    SubagentContextGap,
    SubagentContextItem,
    SubagentContextManifest,
    SubagentContinuationAnswer,
    SubagentQuestion,
)
from app.infrastructure.db.model_base import utc_now

FORBIDDEN_CONTEXT_KEYS = frozenset(
    {
        "messages",
        "conversation_history",
        "hidden_reasoning",
        "chain_of_thought",
        "scratchpad",
        "sibling_scratchpad",
        "tool_trace",
        "secrets",
        "credentials",
        "unselected_memories",
    }
)


@dataclass(frozen=True)
class ComposedSubagentContext:
    manifest: SubagentContextManifest
    manifest_hash: str
    gaps: tuple[SubagentContextGap, ...] = ()


@dataclass
class _ContextCollector:
    items: list[SubagentContextItem] = field(default_factory=list)
    gaps: list[SubagentContextGap] = field(default_factory=list)

    def add(
        self,
        *,
        kind: str,
        summary: str,
        ref: str | None = None,
        content: Any | None = None,
        provenance: dict[str, Any] | None = None,
        data_labels: list[str] | None = None,
        allowed_purposes: list[str] | None = None,
    ) -> None:
        rendered = None
        if content is not None:
            rendered = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        payload = rendered if rendered is not None else ref or ""
        item_hash = stable_digest({"kind": kind, "ref": ref, "content": rendered, "summary": summary})
        self.items.append(
            SubagentContextItem(
                id=f"ctx_{item_hash.removeprefix('sha256:')[:24]}",
                kind=kind,
                ref=ref,
                content=rendered,
                summary=summary,
                content_hash=item_hash,
                provenance=provenance or {},
                data_labels=data_labels or [],
                allowed_purposes=allowed_purposes or [],
                estimated_tokens=max(1, (len(payload) + 3) // 4),
                size_bytes=len(payload.encode("utf-8")),
            )
        )


def _input_denial(item, labels, purposes, purpose, scope, permission_check):
    if purposes and purpose not in purposes:
        return SubagentContextGap(
            input_ref=item.ref,
            reason_code="purpose_mismatch",
            summary="Input purpose does not include this delegation.",
        )
    if labels and not _patterns_cover(labels, scope.data_labels):
        return SubagentContextGap(
            input_ref=item.ref,
            reason_code="data_label_denied",
            summary="Input data labels exceed the delegated scope.",
        )
    if permission_check and not permission_check(item.ref, labels, purpose):
        return SubagentContextGap(
            input_ref=item.ref,
            reason_code="permission_denied",
            summary="Input reference is not readable by the child identity.",
        )
    return None


def _inline_size(value) -> int:
    if value is None:
        return 0
    rendered = value if isinstance(value, str) else json.dumps(value)
    return len(rendered.encode("utf-8"))


def _delegation_purpose(contract, scope) -> str:
    purpose = contract.request.objective
    allowed = scope.allowed_purposes
    if allowed and not any(purpose == item or purpose.startswith(f"{item}:") for item in allowed):
        purpose = str(contract.request.resource_scope.get("purpose", ""))
    if allowed and purpose not in allowed:
        raise ValueError("Delegation purpose is outside the effective child scope")
    return purpose


class SubagentContextComposer:
    def __init__(
        self,
        *,
        max_inline_bytes: int = 8_000,
        max_total_tokens: int = 12_000,
    ):
        self.max_inline_bytes = max_inline_bytes
        self.max_total_tokens = max_total_tokens

    def compose(
        self,
        *,
        agent_execution_id: str,
        contract: DelegationContract,
        effective_scope: EffectiveDelegationScope,
        catalog: FrozenChildCatalog,
        profile_layers: list[dict[str, Any]] | None = None,
        selected_facts: dict[str, Any] | None = None,
        budget: SubagentBudgetEnvelope | None = None,
        permission_check: Callable[[str, tuple[str, ...], str], bool] | None = None,
        created_at: datetime | None = None,
    ) -> ComposedSubagentContext:
        purpose = _delegation_purpose(contract, effective_scope)
        selected_facts = deepcopy(selected_facts or {})
        if _contains_forbidden(selected_facts) or _contains_forbidden(profile_layers or []):
            raise ValueError("Forbidden parent-private context was supplied to the child composer")
        now = created_at or utc_now()
        collector = _ContextCollector()
        add = collector.add

        add(
            kind="delegation_contract",
            summary="Frozen child objective, scope, success criteria, and output contract.",
            content=contract.model_dump(mode="json"),
            provenance={"contract_hash": contract.contract_hash},
        )
        add(
            kind="role_protocol",
            summary="Child role and parent-return protocol.",
            content={
                "role": "delegated_worker",
                "may_publish_final_answer": False,
                "may_modify_parent_state": False,
                "return_type": "SubagentResult",
            },
            provenance={"source": "astra.subagent.protocol.v1"},
        )
        for index, layer in enumerate(profile_layers or []):
            add(
                kind="profile",
                summary=str(layer.get("summary") or f"Applicable Profile layer {index + 1}"),
                content=layer,
                provenance={"source": "effective_profile", "ordinal": index},
            )
        self._add_delegated_inputs(collector, contract, effective_scope, selected_facts, purpose, permission_check)
        add(
            kind="catalog",
            summary="Attenuated immutable AstraTool and Skill Catalog references.",
            content={
                "tool_digest": catalog.tool_digest,
                "tools": [item.get("name") for item in catalog.tools],
                "skill_digest": catalog.skill_digest,
                "skills": [item.get("qualified_identity") for item in catalog.skills],
            },
        )
        workspace_scope = {
            "read_roots": list(effective_scope.workspace_read_roots),
            "write_roots": list(effective_scope.workspace_write_roots),
            "private_staging_root": effective_scope.private_staging_root,
        }
        add(
            kind="workspace_view",
            summary="Child Workspace roots and private staging namespace.",
            content=workspace_scope,
        )
        budget = budget or contract.request.budget
        add(kind="budget", summary="Frozen child execution budget.", content=budget.model_dump())
        add(
            kind="termination",
            summary="Stop and return a typed result when complete, blocked, or budget-limited.",
            content={
                "success_criteria": contract.request.success_criteria,
                "output_schema": contract.request.output_schema,
                "deadline_at": contract.request.deadline_at,
            },
        )
        accepted, total = self._accepted_items(collector)
        manifest = SubagentContextManifest(
            agent_execution_id=agent_execution_id,
            purpose=purpose,
            items=tuple(accepted),
            tool_catalog_digest=catalog.tool_digest,
            skill_catalog_digest=catalog.skill_digest,
            workspace_scope=workspace_scope,
            total_estimated_tokens=total,
            created_at=now,
        )
        return ComposedSubagentContext(
            manifest=manifest,
            manifest_hash=stable_digest(manifest.model_dump(mode="json")),
            gaps=tuple(collector.gaps),
        )

    def _add_delegated_inputs(self, collector, contract, effective_scope, selected_facts, purpose, permission_check) -> None:
        for delegated_input in contract.request.inputs:
            labels = tuple(delegated_input.data_labels)
            purposes = tuple(delegated_input.allowed_purposes)
            denial = _input_denial(delegated_input, labels, purposes, purpose, effective_scope, permission_check)
            if denial:
                collector.gaps.append(denial)
                continue
            inline = selected_facts.get(delegated_input.ref)
            if _inline_size(inline) > self.max_inline_bytes:
                collector.gaps.append(
                    SubagentContextGap(
                        input_ref=delegated_input.ref,
                        reason_code="inline_too_large",
                        summary="Large input must be supplied as an Artifact or Evidence reference.",
                    )
                )
                continue
            kind = delegated_input.kind
            collector.add(
                kind=kind,
                summary=delegated_input.summary or f"Delegated {kind} input",
                ref=delegated_input.ref,
                content=inline if kind in {"fact", "structured_data"} else None,
                provenance={"content_hash": delegated_input.content_hash},
                data_labels=list(labels),
                allowed_purposes=list(purposes),
            )

    def _accepted_items(self, collector):
        accepted, total = [], 0
        for item in collector.items:
            if total + item.estimated_tokens > self.max_total_tokens:
                collector.gaps.append(
                    SubagentContextGap(
                        input_ref=item.ref or item.id,
                        reason_code="token_budget_exceeded",
                        summary="Context item exceeded the child context token limit.",
                    )
                )
                continue
            accepted.append(item)
            total += item.estimated_tokens
        required = {
            "delegation_contract",
            "role_protocol",
            "catalog",
            "workspace_view",
            "budget",
            "termination",
        }
        if not required <= {item.kind for item in accepted}:
            raise ValueError("Child context budget cannot fit the mandatory protocol items")
        return accepted, total


class SubagentContinuationService:
    def __init__(self, secret: str, *, max_round_trips: int):
        if not secret:
            raise ValueError("Continuation signing secret is required")
        self.secret = secret.encode("utf-8")
        self.max_round_trips = max_round_trips

    def question(
        self,
        *,
        checkpoint: SubagentContextCheckpoint,
        prompt: str,
        required_fields: list[str],
    ) -> SubagentQuestion:
        round_trip = checkpoint.continuation_round_trips + 1
        if round_trip > self.max_round_trips:
            raise ValueError("Subagent parent round-trip limit exceeded")
        token = self._token(
            checkpoint.agent_execution_id,
            checkpoint.manifest_hash,
            round_trip,
            required_fields,
        )
        return SubagentQuestion(
            prompt=prompt,
            required_fields=sorted(set(required_fields)),
            continuation_token=token,
            round_trip=round_trip,
        )

    def answer(
        self,
        *,
        checkpoint: SubagentContextCheckpoint,
        question: SubagentQuestion,
        values: dict[str, Any],
    ) -> SubagentContextCheckpoint:
        expected = self._token(
            checkpoint.agent_execution_id,
            checkpoint.manifest_hash,
            question.round_trip,
            question.required_fields,
        )
        if not hmac.compare_digest(expected, question.continuation_token):
            raise ValueError("Subagent continuation token is invalid")
        if question.round_trip != checkpoint.continuation_round_trips + 1:
            raise ValueError("Subagent continuation is stale or out of order")
        missing = set(question.required_fields) - set(values)
        if missing:
            raise ValueError(f"Subagent continuation fields are missing: {sorted(missing)}")
        answer = SubagentContinuationAnswer(
            agent_execution_id=checkpoint.agent_execution_id,
            continuation_token=question.continuation_token,
            round_trip=question.round_trip,
            values=deepcopy(values),
            answered_at=utc_now(),
        )
        return checkpoint.model_copy(
            update={
                "continuation_round_trips": question.round_trip,
                "continuation_answers": (*checkpoint.continuation_answers, answer),
                "created_at": utc_now(),
            }
        )

    def _token(
        self,
        execution_id: str,
        manifest_hash: str,
        round_trip: int,
        fields: list[str],
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "execution_id": execution_id,
                "manifest_hash": manifest_hash,
                "round_trip": round_trip,
                "fields": sorted(set(fields)),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return f"v1.{hmac.new(self.secret, payload, hashlib.sha256).hexdigest()}"


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in FORBIDDEN_CONTEXT_KEYS or _contains_forbidden(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _patterns_cover(children: tuple[str, ...], parents: tuple[str, ...]) -> bool:
    from fnmatch import fnmatchcase

    return bool(parents) and all(any(parent == "*" or fnmatchcase(child, parent) for parent in parents) for child in children)
