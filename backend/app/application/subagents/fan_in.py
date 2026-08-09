from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas.subagents import (
    DelegationContract,
    SubagentExecutionStatus,
    SubagentJoinPolicy,
    SubagentResult,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import AgentExecutionRecord, AgentJoinRecord
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import EvidenceRecord
from app.infrastructure.db.models.workspaces import ArtifactRecord
from app.infrastructure.repositories.agent_executions import (
    TERMINAL_AGENT_STATUSES,
    AgentExecutionRepository,
)
from app.infrastructure.tools.base import validate_json_schema


class SubagentResultValidationError(ValueError):
    pass


def _validated_execution_result(execution):
    if execution is None or execution.parent_execution_id is None:
        raise SubagentResultValidationError("Child AgentExecution is unavailable")
    contract = DelegationContract.model_validate(execution.contract)
    if not execution.result:
        raise SubagentResultValidationError("Child result is missing")
    result = SubagentResult.model_validate(execution.result)
    if result.status.value != execution.status:
        raise SubagentResultValidationError("Child result status does not match execution")
    if result.provenance.get("agent_execution_id") not in {None, execution.id}:
        raise SubagentResultValidationError("Child result provenance crosses execution lineage")
    if result.provenance.get("contract_hash") not in {None, contract.contract_hash}:
        raise SubagentResultValidationError("Child result contract hash is invalid")
    if result.status not in {
        SubagentExecutionStatus.completed,
        SubagentExecutionStatus.completed_with_warnings,
    }:
        raise SubagentResultValidationError(f"Child is not successfully complete: {result.status.value}")
    try:
        validate_json_schema(result.outputs, contract.request.output_schema, path="outputs")
    except ValueError as exc:
        raise SubagentResultValidationError("Child output schema is invalid") from exc
    return contract, result


def _validate_claims_and_completion(result, found_evidence) -> None:
    for claim in result.claims:
        if claim.get("material", True) and not claim.get("evidence_refs"):
            raise SubagentResultValidationError("Material child claim is missing Evidence references")
        if any(ref not in found_evidence for ref in claim.get("evidence_refs", [])):
            raise SubagentResultValidationError("Child claim references Evidence outside the validated result")
    if result.completion.get("state") not in {"completed", "completed_with_warnings"}:
        raise SubagentResultValidationError("Child Completion Gate did not approve success")


def _join_partitions(child_ids, policy, required_ids, optional_ids):
    required_source = required_ids
    if required_source is None:
        required_source = child_ids if policy == SubagentJoinPolicy.required else []
    required = list(dict.fromkeys(required_source))
    optional_source = optional_ids
    if optional_source is None:
        optional_source = [item for item in child_ids if item not in required]
    optional = list(dict.fromkeys(optional_source))
    if set(required) | set(optional) != set(child_ids) or set(required) & set(optional):
        raise ValueError("Join required/optional sets must partition child executions")
    return required, optional


def _join_status(join, successful, failed, waiting):
    if join.policy == SubagentJoinPolicy.first_success.value:
        if successful:
            losers = [item for item in join.child_execution_ids if item not in successful]
            return "ready", losers
        return ("waiting" if waiting else "blocked"), []
    required = set(join.required_execution_ids)
    if required & set(failed):
        return "blocked", []
    if required & set(waiting):
        return "waiting", []
    return "ready", []


@dataclass(frozen=True)
class ValidatedSubagentResult:
    execution_id: str
    contract: DelegationContract
    result: SubagentResult
    artifact_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass
class SubagentResultValidator:
    session: AsyncSession

    async def validate(self, execution_id: str) -> ValidatedSubagentResult:
        execution = await self.session.get(AgentExecutionRecord, execution_id)
        contract, result = _validated_execution_result(execution)
        artifact_ids = [item.id for item in result.artifacts]
        await self._validate_artifacts(execution, artifact_ids)
        evidence_ids = [item.id for item in result.evidence_refs]
        evidence = await self._validated_evidence(execution, evidence_ids)
        found_evidence = {value for item in evidence for value in (item.id, item.evidence_id)}
        _validate_claims_and_completion(result, found_evidence)
        return ValidatedSubagentResult(
            execution_id=execution.id,
            contract=contract,
            result=result,
            artifact_ids=tuple(artifact_ids),
            evidence_ids=tuple(evidence_ids),
            warnings=tuple(result.open_issues) if result.status == SubagentExecutionStatus.completed_with_warnings else (),
        )

    async def _validate_artifacts(self, execution, artifact_ids) -> None:
        artifacts = (
            list((await self.session.scalars(select(ArtifactRecord).where(ArtifactRecord.id.in_(artifact_ids)))).all())
            if artifact_ids
            else []
        )
        if {item.id for item in artifacts} != set(artifact_ids):
            raise SubagentResultValidationError("Child result references a missing Artifact")
        if any(
            item.run_id != execution.run_id or item.agent_execution_id != execution.id or item.security_status != "verified"
            for item in artifacts
        ):
            raise SubagentResultValidationError("Child Artifact is unverified or outside execution lineage")

    async def _validated_evidence(self, execution, evidence_ids):
        evidence = (
            list(
                (
                    await self.session.scalars(
                        select(EvidenceRecord).where(
                            or_(
                                EvidenceRecord.id.in_(evidence_ids),
                                EvidenceRecord.evidence_id.in_(evidence_ids),
                            )
                        )
                    )
                ).all()
            )
            if evidence_ids
            else []
        )
        found = {value for item in evidence for value in (item.id, item.evidence_id)}
        if any(item not in found for item in evidence_ids):
            raise SubagentResultValidationError("Child result references missing Evidence")
        if any(item.run_id != execution.run_id or item.agent_execution_id != execution.id for item in evidence):
            raise SubagentResultValidationError("Child Evidence crosses execution lineage")
        return evidence


@dataclass(frozen=True)
class JoinEvaluation:
    join_id: str
    status: str
    successful_ids: tuple[str, ...]
    failed_ids: tuple[str, ...]
    waiting_ids: tuple[str, ...]
    loser_ids: tuple[str, ...] = ()


class SubagentJoinService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.validator = SubagentResultValidator(session)

    async def create(
        self,
        *,
        parent_execution_id: str,
        join_key: str,
        child_execution_ids: list[str],
        policy: SubagentJoinPolicy,
        required_execution_ids: list[str] | None = None,
        optional_execution_ids: list[str] | None = None,
        consumer_plan_node_id: str | None = None,
        group_id: str | None = None,
        commit: bool = True,
    ) -> AgentJoinRecord:
        existing = await self.session.scalar(
            select(AgentJoinRecord).where(
                AgentJoinRecord.parent_execution_id == parent_execution_id,
                AgentJoinRecord.join_key == join_key,
            )
        )
        child_ids = list(dict.fromkeys(child_execution_ids))
        if existing is not None:
            if existing.child_execution_ids != child_ids or existing.policy != policy.value:
                raise ValueError("Agent join is immutable")
            return existing
        parent = await self.session.get(AgentExecutionRecord, parent_execution_id)
        children = list(
            (await self.session.scalars(select(AgentExecutionRecord).where(AgentExecutionRecord.id.in_(child_ids)))).all()
        )
        if parent is None or {item.id for item in children} != set(child_ids):
            raise ValueError("Join parent or child is unavailable")
        if any(item.parent_execution_id != parent.id for item in children):
            raise ValueError("Join cannot cross direct parent lineage")
        required, optional = _join_partitions(child_ids, policy, required_execution_ids, optional_execution_ids)
        join = AgentJoinRecord(
            run_id=parent.run_id,
            parent_execution_id=parent.id,
            consumer_plan_node_id=consumer_plan_node_id,
            join_key=join_key,
            group_id=group_id,
            policy=policy.value,
            child_execution_ids=child_ids,
            required_execution_ids=required,
            optional_execution_ids=optional,
            status="waiting",
            result={},
        )
        self.session.add(join)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return join

    async def for_group(self, parent_execution_id: str, group_id: str) -> AgentJoinRecord | None:
        return await self.session.scalar(
            select(AgentJoinRecord).where(
                AgentJoinRecord.parent_execution_id == parent_execution_id,
                AgentJoinRecord.group_id == group_id,
            )
        )

    async def ready_for_parent(self, parent_execution_id: str) -> list[AgentJoinRecord]:
        return list(
            (
                await self.session.scalars(
                    select(AgentJoinRecord)
                    .where(
                        AgentJoinRecord.parent_execution_id == parent_execution_id,
                        AgentJoinRecord.status.in_(["waiting", "ready", "merging", "blocked"]),
                    )
                    .order_by(AgentJoinRecord.created_at)
                )
            ).all()
        )

    async def consumed_for_parent(self, parent_execution_id: str) -> list[AgentJoinRecord]:
        return list(
            (
                await self.session.scalars(
                    select(AgentJoinRecord)
                    .where(
                        AgentJoinRecord.parent_execution_id == parent_execution_id,
                        AgentJoinRecord.status == "consumed",
                    )
                    .order_by(AgentJoinRecord.completed_at, AgentJoinRecord.id)
                )
            ).all()
        )

    async def begin_merge(self, join_id: str, *, expected_version: int) -> AgentJoinRecord:
        outcome = await self.session.execute(
            update(AgentJoinRecord)
            .where(
                AgentJoinRecord.id == join_id,
                AgentJoinRecord.state_version == expected_version,
                AgentJoinRecord.status == "ready",
            )
            .values(
                status="merging",
                state_version=expected_version + 1,
                updated_at=utc_now(),
            )
        )
        if outcome.rowcount != 1:
            raise ValueError("Agent join merge claim is stale")
        await self.session.flush()
        join = await self.session.get(AgentJoinRecord, join_id)
        assert join is not None
        await self.session.refresh(join)
        return join

    async def mark_consumed(
        self,
        join_id: str,
        *,
        expected_version: int,
        parent_state_version: int,
        result: dict[str, Any],
    ) -> AgentJoinRecord:
        outcome = await self.session.execute(
            update(AgentJoinRecord)
            .where(
                AgentJoinRecord.id == join_id,
                AgentJoinRecord.state_version == expected_version,
                AgentJoinRecord.status == "merging",
            )
            .values(
                status="consumed",
                result=deepcopy(result),
                consumed_parent_state_version=parent_state_version,
                state_version=expected_version + 1,
                completed_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        if outcome.rowcount != 1:
            raise ValueError("Agent join consumption is stale")
        await self.session.flush()
        join = await self.session.get(AgentJoinRecord, join_id)
        assert join is not None
        await self.session.refresh(join)
        return join

    async def evaluate(self, join_id: str) -> JoinEvaluation:
        join = await self.session.get(AgentJoinRecord, join_id)
        if join is None:
            raise ValueError("Agent join is unavailable")
        children = list(
            (
                await self.session.scalars(
                    select(AgentExecutionRecord).where(AgentExecutionRecord.id.in_(join.child_execution_ids))
                )
            ).all()
        )
        by_id = {item.id: item for item in children}
        waiting = [child_id for child_id in join.child_execution_ids if by_id[child_id].status not in TERMINAL_AGENT_STATUSES]
        successful: list[str] = []
        failed: list[str] = []
        validation_errors: dict[str, str] = {}
        for child_id in join.child_execution_ids:
            child = by_id[child_id]
            if child.status not in TERMINAL_AGENT_STATUSES:
                continue
            try:
                await self.validator.validate(child_id)
                successful.append(child_id)
            except SubagentResultValidationError as exc:
                failed.append(child_id)
                validation_errors[child_id] = str(exc)
        status, losers = _join_status(join, successful, failed, waiting)
        result = {
            "successful_ids": successful,
            "failed_ids": failed,
            "waiting_ids": waiting,
            "loser_ids": losers,
            "validation_errors": validation_errors,
        }
        if join.status != status or join.result != result:
            join.status = status
            join.result = result
            join.state_version += 1
            join.updated_at = utc_now()
            join.completed_at = utc_now() if status in {"ready", "blocked"} else None
            await self.session.commit()
        return JoinEvaluation(
            join_id=join.id,
            status=status,
            successful_ids=tuple(successful),
            failed_ids=tuple(failed),
            waiting_ids=tuple(waiting),
            loser_ids=tuple(losers),
        )

    async def cancel_safe_first_success_losers(
        self,
        evaluation: JoinEvaluation,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        join = await self.session.get(AgentJoinRecord, evaluation.join_id)
        if join is None or join.policy != SubagentJoinPolicy.first_success.value or evaluation.status != "ready":
            raise ValueError("Loser cancellation requires a ready first-success join")
        cancelled: list[str] = []
        unsafe: list[str] = []
        repository = AgentExecutionRepository(self.session)
        for execution_id in evaluation.loser_ids:
            execution = await repository.require(execution_id)
            if execution.status in TERMINAL_AGENT_STATUSES:
                continue
            active_calls = list(
                (
                    await self.session.scalars(
                        select(ToolCallRecord).where(
                            ToolCallRecord.agent_execution_id == execution.id,
                            ToolCallRecord.status.in_(["running", "awaiting_approval"]),
                        )
                    )
                ).all()
            )
            if any(item.side_effect_level not in {"none", "read", "read_only"} for item in active_calls):
                unsafe.append(execution.id)
                continue
            await repository.transition(
                execution.id,
                expected_state_version=execution.state_version,
                status=SubagentExecutionStatus.cancelled,
                phase="terminal",
                error={"category": "first_success_loser"},
            )
            cancelled.append(execution.id)
        await self.session.commit()
        return tuple(cancelled), tuple(unsafe)


@dataclass(frozen=True)
class SubagentMergeResult:
    facts: tuple[dict[str, Any], ...]
    claims: tuple[dict[str, Any], ...]
    conflicts: tuple[dict[str, Any], ...]
    artifact_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    open_issues: tuple[str, ...]
    warnings: tuple[str, ...]
    source_execution_ids: tuple[str, ...]


def merge_subagent_results(results: list[ValidatedSubagentResult]) -> SubagentMergeResult:
    facts_by_key: dict[str, dict[str, Any]] = {}
    claims_by_key: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    artifacts: list[str] = []
    evidence: list[str] = []
    open_issues: list[str] = []
    warnings: list[str] = []
    for item in results:
        source = item.execution_id
        for key, value in item.result.outputs.items():
            _merge_fact(facts_by_key, conflicts, key, value, item)
        for claim in item.result.claims:
            _merge_claim(claims_by_key, conflicts, claim, source)
        artifacts.extend(item.artifact_ids)
        evidence.extend(item.evidence_ids)
        open_issues.extend(item.result.open_issues)
        warnings.extend(item.warnings)
    return SubagentMergeResult(
        facts=tuple(facts_by_key.values()),
        claims=tuple(claims_by_key.values()),
        conflicts=tuple(conflicts),
        artifact_ids=tuple(dict.fromkeys(artifacts)),
        evidence_ids=tuple(dict.fromkeys(evidence)),
        open_issues=tuple(dict.fromkeys(open_issues)),
        warnings=tuple(dict.fromkeys(warnings)),
        source_execution_ids=tuple(item.execution_id for item in results),
    )


def _merge_fact(facts, conflicts, key, value, item) -> None:
    fact = {
        "key": key,
        "value": deepcopy(value),
        "verified": bool(item.evidence_ids),
        "source_agent_execution_id": item.execution_id,
        "evidence_refs": list(item.evidence_ids),
    }
    previous = facts.get(key)
    if previous is not None and previous["value"] != fact["value"]:
        conflicts.append({"kind": "fact_conflict", "key": key, "values": [previous, fact]})
    elif previous is None:
        facts[key] = fact


def _merge_claim(claims, conflicts, claim, source) -> None:
    key = str(claim.get("key") or claim.get("id") or claim.get("subject") or claim.get("text"))
    normalized = {**deepcopy(claim), "source_agent_execution_ids": [source]}
    previous = claims.get(key)
    previous_value = (previous or {}).get("value", (previous or {}).get("text"))
    value = normalized.get("value", normalized.get("text"))
    if previous is not None and previous_value != value:
        conflicts.append(
            {
                "kind": "claim_conflict",
                "key": key,
                "values": [previous, normalized],
            }
        )
    elif previous is not None:
        previous["evidence_refs"] = list(
            dict.fromkeys(
                [
                    *previous.get("evidence_refs", []),
                    *normalized.get("evidence_refs", []),
                ]
            )
        )
        previous["source_agent_execution_ids"].append(source)
    else:
        claims[key] = normalized
