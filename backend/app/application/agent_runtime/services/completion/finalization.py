"""Converge an Agent execution into one persisted terminal result."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.services.completion.gate import (
    CompletionGateInput,
    CompletionGateStage,
)
from app.application.agent_runtime.services.completion.memory_candidates import (
    MemoryCandidateWriter,
)
from app.application.agent_runtime.services.completion.verification import (
    CompletionVerificationStage,
    normalize_final_answer_artifact_references,
)
from app.application.agent_runtime.services.shared.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
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


@dataclass(frozen=True)
class FinalizationInput:
    run_id: str
    goal: str
    profile: RunExecutionProfile
    progress: ExecutionProgress
    tool_outputs: list[dict[str, Any]]
    streamed_final_answer: AgentFinalAnswer | None
    final_turn_id: str | None
    terminal_status: str | None
    terminal_summary: str | None
    required_subagent_missing: bool
    workspace_changed: bool
    workspace_path: Path | None


@dataclass(frozen=True)
class PreparedFinalAnswer:
    answer: AgentFinalAnswer
    evidence_pack: dict[str, Any]
    evidence_artifact: ArtifactRecord | None
    invalid_artifact_references: int
    referenced_artifact_ids: list[str]
    terminal_status: str | None
    terminal_summary: str | None


class AgentFinalizationStage:
    def __init__(
        self,
        *,
        repository: RunUnitOfWork,
        plan_repository: PlanRepository,
        model_client: ModelClient,
        plugin_runtime: PluginRuntimeState,
        memory_writer: MemoryCandidateWriter,
        verifier: CompletionVerificationStage,
        completion_gate: AgentCompletionGate,
        progress_stage: ProgressEvaluationStage,
        workspace_service: WorkspaceRuntimeService,
        on_answer_delta: AnswerDeltaHandler | None,
    ) -> None:
        self._repository = repository
        self._plan_repository = plan_repository
        self._model_client = model_client
        self._plugin_runtime = plugin_runtime
        self._memory_writer = memory_writer
        self._verifier = verifier
        self._completion_gate = completion_gate
        self._completion_gate_stage = CompletionGateStage(
            repository,
            plan_repository,
            completion_gate,
        )
        self._progress_stage = progress_stage
        self._workspace_service = workspace_service
        self._on_answer_delta = on_answer_delta

    async def execute(self, stage_input: FinalizationInput) -> dict[str, Any]:
        prepared = await self._prepare_answer(stage_input)
        final_context = self._final_context(stage_input, prepared.evidence_pack)
        memory_writes = await self._memory_writer.write_candidates(
            run_id=stage_input.run_id,
            goal=stage_input.goal,
            context=final_context,
        )
        verification = self._verification(stage_input.profile, prepared)
        gate_decision = await self._completion_gate_stage.evaluate(
            CompletionGateInput(
                run_id=stage_input.run_id,
                profile=stage_input.profile,
                progress=stage_input.progress,
                terminal_status=prepared.terminal_status,
            ),
            verification,
        )
        gate_decision = self._apply_terminal_override(stage_input, prepared, gate_decision)
        completion_reflection = await self._completion_reflection(
            prepared.terminal_status,
            gate_decision,
        )
        return await self._persist_full_result(
            stage_input,
            prepared,
            verification,
            gate_decision,
            memory_writes,
            completion_reflection,
        )

    async def _prepare_answer(self, stage_input: FinalizationInput) -> PreparedFinalAnswer:
        terminal_status = stage_input.terminal_status
        terminal_summary = stage_input.terminal_summary
        if stage_input.required_subagent_missing and terminal_status is None:
            terminal_status = TerminalState.blocked.value
            terminal_summary = (
                "The Run could not complete because no governed Swarm group was created."
            )
        evidence_pack, artifact = await self._evidence_pack(stage_input)
        final_context = self._final_context(stage_input, evidence_pack)
        answer = await self._select_answer(
            stage_input,
            terminal_status,
            terminal_summary,
            final_context,
        )
        answer = project_grounded_answer(answer, self._plugin_runtime.grounding)
        answer, invalid_count, artifact_ids = normalize_final_answer_artifact_references(
            answer,
            await self._repository.list_artifacts(stage_input.run_id),
        )
        return PreparedFinalAnswer(
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
        stage_input: FinalizationInput,
    ) -> tuple[dict[str, Any], ArtifactRecord | None]:
        evidence_pack = self._plugin_runtime.evidence_pack(stage_input.goal)
        records = self._plugin_runtime.grounding.records()
        artifact = await self._repository.create_artifact(
            stage_input.run_id,
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
            stage_input.run_id,
            records,
            artifact_ids=[artifact.id],
        )
        return evidence_pack, artifact

    async def _select_answer(
        self,
        stage_input: FinalizationInput,
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
        if stage_input.streamed_final_answer is not None:
            return stage_input.streamed_final_answer
        return await self._model_client.finalize(
            stage_input.goal,
            final_context,
            on_delta=self._on_answer_delta,
        )

    def _verification(
        self,
        profile: RunExecutionProfile,
        prepared: PreparedFinalAnswer,
    ) -> AgentAnswerVerificationReport:
        validation_outcomes = []
        if profile.assurance_level == AssuranceLevel.full:
            validation_outcomes = self._plugin_runtime.validate(
                prepared.answer.model_dump(mode="json"),
                prepared.evidence_pack,
            )
            validation_outcomes.extend(
                grounding_validation_outcomes(
                    prepared.answer.model_dump(mode="json"),
                    prepared.evidence_pack,
                )
            )
        return self._verifier.verify(
            prepared.answer,
            prepared.evidence_pack,
            validation_outcomes=validation_outcomes,
            invalid_artifact_references=prepared.invalid_artifact_references,
            assurance_level=profile.assurance_level,
        )

    @staticmethod
    def _apply_terminal_override(
        stage_input: FinalizationInput,
        prepared: PreparedFinalAnswer,
        decision: CompletionDecision,
    ) -> CompletionDecision:
        if prepared.terminal_status == "waiting_user":
            return decision.model_copy(
                update={
                    "state": TerminalState.waiting_user,
                    "reason": prepared.terminal_summary or decision.reason,
                    "required_user_action": prepared.terminal_summary,
                }
            )
        if prepared.terminal_status == "blocked":
            return decision.model_copy(
                update={
                    "state": TerminalState.blocked,
                    "reason": prepared.terminal_summary or decision.reason,
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
        stage_input: FinalizationInput,
        prepared: PreparedFinalAnswer,
        verification: AgentAnswerVerificationReport,
        gate_decision: CompletionDecision,
        memory_writes: list[dict[str, Any]],
        completion_reflection: Any,
    ) -> dict[str, Any]:
        final_status = gate_decision.state.value
        result = prepared.answer.model_dump()
        result.update(
            answer_mode=stage_input.profile.answer_mode.value,
            assurance_level=stage_input.profile.assurance_level.value,
            verification_report=verification.model_dump(),
            audit_refs=self._audit_refs(stage_input, prepared),
            completion_decision=gate_decision.model_dump(mode="json"),
        )
        await self._repository.add_event(
            stage_input.run_id,
            "reasoning.completion_decided",
            gate_decision.model_dump(mode="json"),
        )
        if stage_input.final_turn_id:
            await self._repository.update_agent_turn(
                stage_input.final_turn_id,
                status="completed",
                observation=self._final_observation(prepared.answer, final_status),
                artifact_id=prepared.evidence_artifact.id if prepared.evidence_artifact else None,
                memory_writes=memory_writes,
                reflection=(completion_reflection.model_dump() if completion_reflection else None),
            )
        await self._repository.add_event(
            stage_input.run_id,
            "verification.created",
            verification.model_dump(),
        )
        await self._checkpoint_workspace(stage_input, final_status)
        await self._repository.session.commit()
        return {"answer": prepared.answer, "result": result, "status": final_status}

    def _audit_refs(
        self,
        stage_input: FinalizationInput,
        prepared: PreparedFinalAnswer,
    ) -> dict[str, Any]:
        artifact_id = prepared.evidence_artifact.id if prepared.evidence_artifact else None
        return {
            "evidence_pack_artifact_id": artifact_id,
            "evidence_ledger_artifact_id": artifact_id,
            "evidence_record_count": len(self._plugin_runtime.grounding.records()),
            "agent_turn_count": len(stage_input.progress.observations)
            + (1 if stage_input.final_turn_id else 0),
            "referenced_artifact_ids": prepared.referenced_artifact_ids,
        }

    async def _checkpoint_workspace(
        self,
        stage_input: FinalizationInput,
        final_status: str,
    ) -> None:
        if (
            final_status in {"waiting_user", "executing"}
            or not stage_input.workspace_changed
            or stage_input.workspace_path is None
        ):
            return
        checkpoint = await self._workspace_service.create_checkpoint(
            run_id=stage_input.run_id,
            workspace_dir=stage_input.workspace_path,
        )
        await self._repository.add_event(
            stage_input.run_id,
            "workspace.checkpoint_created",
            checkpoint,
        )

    @staticmethod
    def _final_context(
        stage_input: FinalizationInput,
        evidence_pack: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "run_id": stage_input.run_id,
            "observations": stage_input.progress.observations,
            "tool_outputs": stage_input.tool_outputs,
            "evidence_pack": evidence_pack,
        }

    @staticmethod
    def _final_observation(answer: AgentFinalAnswer, final_status: str) -> dict[str, str]:
        return {
            "kind": "final_answer",
            "status": final_status,
            "summary": answer.summary,
        }
