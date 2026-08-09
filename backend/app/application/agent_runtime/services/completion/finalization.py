"""Converge an Agent execution into one persisted terminal result."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.services.completion.gate import CompletionGateStage
from app.application.agent_runtime.services.completion.memory_candidates import (
    MemoryCandidateWriter,
)
from app.application.agent_runtime.services.completion.verification import (
    normalize_final_answer_artifact_references,
)
from app.application.agent_runtime.services.shared.progress import ProgressEvaluationStage
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.schemas.agent.execution_state import CompletionDecision
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.common.schemas.agent.run_result import AgentAnswerVerificationReport, AgentFinalAnswer
from app.common.schemas.agent.types import AssuranceLevel, TerminalState
from app.domain.grounding.projection import project_grounded_answer
from app.domain.grounding.validators import grounding_validation_outcomes
from app.infrastructure.db.models.workspaces import ArtifactRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.evidence import EvidenceRepository
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

AnswerDeltaHandler = Callable[[str], Awaitable[None]]

if TYPE_CHECKING:
    from app.infrastructure.runtime.trusted_state import TrustedRuntime

_PreparedAnswer = tuple[
    AgentFinalAnswer,
    dict[str, Any],
    ArtifactRecord | None,
    int,
    list[str],
    str | None,
    str | None,
]


@dataclass
class AgentFinalizationStage:
    _repository: RunUnitOfWork
    _plan_repository: PlanRepository
    _model_client: ModelClient
    _plugin_runtime: PluginRuntimeState
    _memory_writer: MemoryCandidateWriter
    _verifier: Any
    _completion_gate: AgentCompletionGate
    _progress_stage: ProgressEvaluationStage
    _workspace_service: WorkspaceRuntimeService
    _on_answer_delta: AnswerDeltaHandler | None

    async def execute(self, runtime: TrustedRuntime, goal: str) -> dict[str, Any]:
        prepared = await self._prepare_answer(runtime, goal)
        answer, evidence_pack, artifact, invalid_refs, artifact_ids, status, summary = prepared
        final_context = self._final_context(runtime, evidence_pack)
        memory_writes = await self._memory_writer.write_candidates(
            run_id=runtime.run.id,
            goal=goal,
            context=final_context,
        )
        verification = self._verification(runtime.profile, answer, evidence_pack, invalid_refs)
        gate_decision = await CompletionGateStage(self._repository, self._plan_repository, self._completion_gate).evaluate(
            runtime.run.id,
            runtime.profile,
            runtime.progress,
            status,
            verification,
        )
        gate_decision = self._apply_terminal_override(status, summary, gate_decision)
        completion_reflection = await self._completion_reflection(
            status,
            gate_decision,
        )
        return await self._persist_full_result(
            runtime,
            answer,
            artifact,
            artifact_ids,
            verification,
            gate_decision,
            memory_writes,
            completion_reflection,
        )

    async def _prepare_answer(self, runtime: TrustedRuntime, goal: str) -> _PreparedAnswer:
        state = runtime.state
        terminal_status = state.terminal_status
        terminal_summary = state.terminal_summary
        if state.required_subagent_missing and terminal_status is None:
            terminal_status = TerminalState.blocked.value
            terminal_summary = "The Run could not complete because no governed Swarm group was created."
        evidence_pack, artifact = await self._evidence_pack(runtime, goal)
        final_context = self._final_context(runtime, evidence_pack)
        answer = await self._select_answer(
            runtime,
            goal,
            terminal_status,
            terminal_summary,
            final_context,
        )
        answer = project_grounded_answer(answer, self._plugin_runtime.grounding)
        answer, invalid_count, artifact_ids = normalize_final_answer_artifact_references(
            answer,
            await self._repository.list_artifacts(runtime.run.id),
        )
        return (
            answer,
            evidence_pack,
            artifact,
            invalid_count,
            artifact_ids,
            terminal_status,
            terminal_summary,
        )

    async def _evidence_pack(
        self,
        runtime: TrustedRuntime,
        goal: str,
    ) -> tuple[dict[str, Any], ArtifactRecord | None]:
        evidence_pack = self._plugin_runtime.evidence_pack(goal)
        records = self._plugin_runtime.grounding.records()
        artifact = await self._repository.create_artifact(
            runtime.run.id,
            "evidence_pack",
            content_ref=json.dumps(evidence_pack, ensure_ascii=False),
            metadata={
                "format": "json",
                "schema": "grounding-ledger.v1",
                "evidence_records": len(records),
                "audited_sources": len(evidence_pack.get("fetched_sources", [])),
                "failed_sources": len(evidence_pack.get("failed_sources", [])),
            },
        )
        evidence_pack["artifact_id"] = artifact.id
        await EvidenceRepository(self._repository.session).append_with_lineage(
            runtime.run.id,
            records,
            artifact_ids=[artifact.id],
        )
        return evidence_pack, artifact

    async def _select_answer(
        self,
        runtime: TrustedRuntime,
        goal: str,
        terminal_status: str | None,
        terminal_summary: str | None,
        final_context: dict[str, Any],
    ) -> AgentFinalAnswer:
        if terminal_status:
            return AgentFinalAnswer(
                summary=terminal_summary or "任务未能完成。",
                caveats=["运行在满足全部成功条件前停止。"],
                verification_notes=["该响应表示运行状态，不表示任务成功完成。"],
            )
        if runtime.state.streamed_final_answer is not None:
            return runtime.state.streamed_final_answer
        return await self._model_client.finalize(
            goal,
            final_context,
            on_delta=self._on_answer_delta,
        )

    def _verification(
        self,
        profile: RunExecutionProfile,
        answer: AgentFinalAnswer,
        evidence_pack: dict[str, Any],
        invalid_artifact_references: int,
    ) -> AgentAnswerVerificationReport:
        validation_outcomes = []
        if profile.assurance_level == AssuranceLevel.full:
            validation_outcomes = self._plugin_runtime.validate(
                answer.model_dump(mode="json"),
                evidence_pack,
            )
            validation_outcomes.extend(
                grounding_validation_outcomes(
                    answer.model_dump(mode="json"),
                    evidence_pack,
                )
            )
        return self._verifier(
            answer,
            evidence_pack,
            validation_outcomes=validation_outcomes,
            invalid_artifact_references=invalid_artifact_references,
            assurance_level=profile.assurance_level,
        )

    @staticmethod
    def _apply_terminal_override(
        terminal_status: str | None,
        terminal_summary: str | None,
        decision: CompletionDecision,
    ) -> CompletionDecision:
        if terminal_status == "waiting_user":
            return decision.model_copy(
                update={
                    "state": TerminalState.waiting_user,
                    "reason": terminal_summary or decision.reason,
                    "required_user_action": terminal_summary,
                }
            )
        if terminal_status == "blocked":
            return decision.model_copy(
                update={
                    "state": TerminalState.blocked,
                    "reason": terminal_summary or decision.reason,
                }
            )
        if decision.state == TerminalState.continue_run:
            return decision.model_copy(
                update={
                    "state": TerminalState.blocked,
                    "reason": "执行循环结束时活动计划仍有未完成节点。",
                }
            )
        return decision

    async def _completion_reflection(
        self,
        terminal_status: str | None,
        decision: CompletionDecision,
    ) -> Any:
        if decision.state != TerminalState.blocked or terminal_status:
            return None
        return await self._progress_stage.reflect(
            "completion_gate_failed",
            {
                "last_observation": {
                    "kind": "completion_gate",
                    "status": "failed",
                    "summary": decision.reason,
                    "data": decision.model_dump(mode="json"),
                },
                "retry_count": 0,
            },
        )

    async def _persist_full_result(
        self,
        runtime: TrustedRuntime,
        answer: AgentFinalAnswer,
        evidence_artifact: ArtifactRecord | None,
        referenced_artifact_ids: list[str],
        verification: AgentAnswerVerificationReport,
        gate_decision: CompletionDecision,
        memory_writes: list[dict[str, Any]],
        completion_reflection: Any,
    ) -> dict[str, Any]:
        final_status = gate_decision.state.value
        result = answer.model_dump()
        result.update(
            answer_mode=runtime.profile.answer_mode.value,
            assurance_level=runtime.profile.assurance_level.value,
            verification_report=verification.model_dump(),
            audit_refs=self._audit_refs(runtime, evidence_artifact, referenced_artifact_ids),
            completion_decision=gate_decision.model_dump(mode="json"),
        )
        await self._repository.add_event(
            runtime.run.id,
            "reasoning.completion_decided",
            gate_decision.model_dump(mode="json"),
        )
        if runtime.state.final_turn_id:
            await self._repository.update_agent_turn(
                runtime.state.final_turn_id,
                status="completed",
                observation=self._final_observation(answer, final_status),
                artifact_id=evidence_artifact.id if evidence_artifact else None,
                memory_writes=memory_writes,
                reflection=(completion_reflection.model_dump() if completion_reflection else None),
            )
        await self._repository.add_event(
            runtime.run.id,
            "verification.created",
            verification.model_dump(),
        )
        await self._checkpoint_workspace(runtime, final_status)
        await self._repository.session.commit()
        return {"answer": answer, "result": result, "status": final_status}

    def _audit_refs(
        self,
        runtime: TrustedRuntime,
        evidence_artifact: ArtifactRecord | None,
        referenced_artifact_ids: list[str],
    ) -> dict[str, Any]:
        artifact_id = evidence_artifact.id if evidence_artifact else None
        return {
            "evidence_pack_artifact_id": artifact_id,
            "evidence_ledger_artifact_id": artifact_id,
            "evidence_record_count": len(self._plugin_runtime.grounding.records()),
            "agent_turn_count": len(runtime.progress.observations) + (1 if runtime.state.final_turn_id else 0),
            "referenced_artifact_ids": referenced_artifact_ids,
        }

    async def _checkpoint_workspace(
        self,
        runtime: TrustedRuntime,
        final_status: str,
    ) -> None:
        if (
            final_status in {"waiting_user", "executing"}
            or not runtime.state.workspace_changed
            or runtime.state.workspace_path is None
        ):
            return
        checkpoint = await self._workspace_service.create_checkpoint(
            run_id=runtime.run.id,
            workspace_dir=runtime.state.workspace_path,
        )
        await self._repository.add_event(
            runtime.run.id,
            "workspace.checkpoint_created",
            checkpoint,
        )

    @staticmethod
    def _final_context(
        runtime: TrustedRuntime,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "run_id": runtime.run.id,
            "observations": runtime.progress.observations,
            "tool_outputs": runtime.tool_outputs,
            "evidence_pack": evidence_pack,
        }

    @staticmethod
    def _final_observation(answer: AgentFinalAnswer, final_status: str) -> dict[str, str]:
        return {
            "kind": "final_answer",
            "status": final_status,
            "summary": answer.summary,
        }
