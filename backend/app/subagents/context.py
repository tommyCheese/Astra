from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.executions import AgentExecutionRecord
from app.db.models.runs import EvidenceRecord
from app.db.models.workspaces import ArtifactRecord
from app.grounding.repository import EvidenceRepository
from app.grounding.schemas import EvidenceFragment
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.repositories.workspaces import WorkspaceRepository
from app.schemas.subagents import (
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
from app.subagents.governance import FrozenChildCatalog, stable_digest

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


class _ContextCollector:
    def __init__(self):
        self.items: list[SubagentContextItem] = []
        self.gaps: list[SubagentContextGap] = []

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
        item_hash = stable_digest(
            {"kind": kind, "ref": ref, "content": rendered, "summary": summary}
        )
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
        self._add_delegated_inputs(
            collector, contract, effective_scope, selected_facts, purpose, permission_check
        )
        add(
            kind="catalog",
            summary="Attenuated immutable Tool and Skill Catalog references.",
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

    def _add_delegated_inputs(
        self, collector, contract, effective_scope, selected_facts, purpose, permission_check
    ) -> None:
        for delegated_input in contract.request.inputs:
            labels = tuple(delegated_input.data_labels)
            purposes = tuple(delegated_input.allowed_purposes)
            denial = _input_denial(
                delegated_input, labels, purposes, purpose, effective_scope, permission_check
            )
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


class SubagentContextCheckpointService:
    def compress(
        self,
        *,
        composed: ComposedSubagentContext,
        local_summary: str,
        local_facts: list[dict[str, Any]],
        prior: SubagentContextCheckpoint | None = None,
    ) -> SubagentContextCheckpoint:
        return SubagentContextCheckpoint(
            agent_execution_id=composed.manifest.agent_execution_id,
            manifest_hash=composed.manifest_hash,
            local_summary=local_summary,
            local_facts=tuple(deepcopy(local_facts)),
            continuation_round_trips=(prior.continuation_round_trips if prior else 0),
            continuation_answers=(prior.continuation_answers if prior else ()),
            created_at=utc_now(),
        )


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


class SubagentExchangeService:
    """Stages child-owned references and performs explicit verified promotion."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.runs = RunUnitOfWork(session)

    async def stage_artifact(
        self,
        *,
        agent_execution_id: str,
        relative_name: str,
        artifact_type: str,
        content_ref: str,
        mime_type: str,
        size_bytes: int,
        checksum: str,
        provenance: dict[str, Any],
    ) -> ArtifactRecord:
        execution = await self._child(agent_execution_id)
        safe_name = _safe_relative_path(relative_name)
        staging_root = _staging_root(execution)
        return await self.runs.create_artifact(
            execution.run_id,
            artifact_type,
            agent_execution_id=execution.id,
            path=f"{staging_root}/{safe_name}",
            content_ref=content_ref,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum=checksum,
            security_status="pending",
            provenance={
                **deepcopy(provenance),
                "agent_execution_id": execution.id,
                "source_agent_execution_id": execution.id,
                "promotion_status": "private_staging",
            },
            metadata={"private": True, "staging_root": staging_root},
        )

    async def stage_evidence(
        self,
        *,
        agent_execution_id: str,
        fragment: EvidenceFragment,
    ) -> EvidenceRecord:
        execution = await self._child(agent_execution_id)
        return await EvidenceRepository(self.session).append(
            execution.run_id,
            fragment,
            agent_execution_id=execution.id,
        )

    async def promote_artifact(
        self,
        *,
        parent_execution_id: str,
        artifact_id: str,
        public_path: str,
    ) -> ArtifactRecord:
        parent = await self.session.get(AgentExecutionRecord, parent_execution_id)
        artifact = await self.session.get(ArtifactRecord, artifact_id)
        if parent is None or artifact is None or artifact.agent_execution_id is None:
            raise ValueError("Promotion source or parent execution is unavailable")
        child = await self.session.get(AgentExecutionRecord, artifact.agent_execution_id)
        if child is None or child.parent_execution_id != parent.id or child.run_id != parent.run_id:
            raise ValueError("Only a direct child's Artifact can be promoted by this parent")
        if artifact.security_status != "verified":
            raise ValueError("Only a verified child Artifact can be promoted")
        artifact.path = _safe_relative_path(public_path)
        artifact.metadata_ = {**artifact.metadata_, "private": False, "promoted_by": parent.id}
        artifact.provenance = {
            **artifact.provenance,
            "promotion_status": "promoted",
            "promoted_by_execution_id": parent.id,
        }
        workspace = await WorkspaceRepository(self.session).get_or_create(parent.task_id)
        await WorkspaceRepository(self.session).upsert_file(
            workspace.id,
            artifact.path,
            mime_type=artifact.mime_type,
            size_bytes=artifact.size_bytes,
            checksum=artifact.checksum,
            security_status="verified",
            deliverable_candidate=True,
            metadata={
                "artifact_id": artifact.id,
                "promoted_by_agent_execution_id": parent.id,
            },
        )
        await self.session.commit()
        return artifact

    async def promote_verified_facts(
        self,
        *,
        parent_execution_id: str,
        child_execution_id: str,
        facts: list[dict[str, Any]],
        expected_parent_state_version: int,
    ) -> AgentExecutionRecord:
        parent = await self.session.get(AgentExecutionRecord, parent_execution_id)
        child = await self._child(child_execution_id)
        if parent is None or child.parent_execution_id != parent.id:
            raise ValueError("Fact promotion must follow direct Agent lineage")
        verified = [
            deepcopy(item)
            for item in facts
            if item.get("verified") is True and item.get("evidence_refs")
        ]
        if len(verified) != len(facts):
            raise ValueError("Only verified child facts with Evidence can be promoted")
        checkpoint = deepcopy(parent.checkpoint or {})
        checkpoint["promoted_child_facts"] = [
            *(checkpoint.get("promoted_child_facts") or []),
            *[{**item, "source_agent_execution_id": child.id} for item in verified],
        ]
        outcome = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == parent.id,
                AgentExecutionRecord.state_version == expected_parent_state_version,
            )
            .values(
                checkpoint=checkpoint,
                state_version=expected_parent_state_version + 1,
                updated_at=utc_now(),
            )
        )
        if outcome.rowcount != 1:
            raise ValueError("Parent AgentExecution changed during fact promotion")
        await self.session.commit()
        await self.session.refresh(parent)
        return parent

    async def _child(self, execution_id: str) -> AgentExecutionRecord:
        execution = await self.session.get(AgentExecutionRecord, execution_id)
        if execution is None or execution.parent_execution_id is None:
            raise ValueError("A child AgentExecution is required")
        return execution


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).lower() in FORBIDDEN_CONTEXT_KEYS or _contains_forbidden(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    return False


def _patterns_cover(children: tuple[str, ...], parents: tuple[str, ...]) -> bool:
    from fnmatch import fnmatchcase

    return bool(parents) and all(
        any(parent == "*" or fnmatchcase(child, parent) for parent in parents) for child in children
    )


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Subagent Artifact path is unsafe")
    if any(part in {".git", ".astra", ".codex"} for part in path.parts):
        raise ValueError("Subagent Artifact path targets protected metadata")
    return path.as_posix()


def _staging_root(execution: AgentExecutionRecord) -> str:
    scope = ((execution.contract or {}).get("request") or {}).get("resource_scope") or {}
    configured = scope.get("private_staging_root")
    return str(configured or f".astra/subagents/{execution.id}/staging")
