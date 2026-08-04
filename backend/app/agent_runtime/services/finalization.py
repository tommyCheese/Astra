"""Converge an Agent execution into one persisted terminal result."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent_runtime.policies.completion import CompletionGate
from app.agent_runtime.result_adapters import ChartTaskAdapter, WebTaskAdapter
from app.agent_runtime.services.completion import (
    CompletionVerificationStage,
    normalize_final_answer_artifact_references,
)
from app.agent_runtime.services.completion_gate import (
    CompletionGateInput,
    CompletionGateStage,
)
from app.agent_runtime.services.memory_candidates import MemoryCandidateWriter
from app.agent_runtime.services.progress import ExecutionProgress, ProgressEvaluationStage
from app.db.models.workspaces import ArtifactRecord
from app.grounding.projection import project_grounded_answer
from app.grounding.repository import EvidenceRepository, EvidenceWriter
from app.grounding.validators import grounding_validation_outcomes
from app.model_clients.contracts import ModelClient
from app.repositories.plans import PlanRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.execution_state import CompletionDecision
from app.schemas.agent.run_policy import RunExecutionProfile
from app.schemas.agent.run_result import FinalAnswer, VerificationReport
from app.schemas.agent.types import AssuranceLevel, TerminalState
from app.workspaces.runtime import WorkspaceRuntimeService

AnswerDeltaHandler = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class FinalizationInput:
    run_id: str
    goal: str
    profile: RunExecutionProfile
    progress: ExecutionProgress
    tool_outputs: list[dict[str, Any]]
    streamed_final_answer: FinalAnswer | None
    final_turn_id: str | None
    terminal_status: str | None
    terminal_summary: str | None
    required_subagent_missing: bool
    quick_mode: bool
    workspace_changed: bool
    workspace_path: Path | None


@dataclass(frozen=True)
class PreparedFinalAnswer:
    answer: FinalAnswer
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
        web_adapter: WebTaskAdapter,
        chart_adapter: ChartTaskAdapter,
        memory_writer: MemoryCandidateWriter,
        verifier: CompletionVerificationStage,
        completion_gate: CompletionGate,
        progress_stage: ProgressEvaluationStage,
        workspace_service: WorkspaceRuntimeService,
        on_answer_delta: AnswerDeltaHandler | None,
    ) -> None:
        self._repository = repository
        self._plan_repository = plan_repository
        self._model_client = model_client
        self._web_adapter = web_adapter
        self._chart_adapter = chart_adapter
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
        if stage_input.quick_mode:
            return await self._finalize_quick(stage_input, prepared)
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
        answer = project_grounded_answer(answer, self._web_adapter.grounding)
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
        evidence_pack = self._web_adapter.build_evidence(
            stage_input.goal,
            self._web_adapter.attempted,
        )
        if stage_input.quick_mode:
            return evidence_pack, None
        records = self._web_adapter.grounding.records()
        artifact = await self._repository.create_artifact(
            stage_input.run_id,
            "evidence_pack",
            content_ref=json.dumps(evidence_pack, ensure_ascii=False),
            metadata={
                "format": "json",
                "schema": "grounding-ledger.v1",
                "evidence_records": len(records),
                "audited_sources": len(evidence_pack["fetched_sources"]),
                "failed_sources": len(evidence_pack["failed_sources"]),
            },
        )
        evidence_pack["artifact_id"] = artifact.id
        await EvidenceWriter(EvidenceRepository(self._repository.session)).write(
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
    ) -> FinalAnswer:
        if terminal_status:
            return FinalAnswer(
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

    async def _finalize_quick(
        self,
        stage_input: FinalizationInput,
        prepared: PreparedFinalAnswer,
    ) -> dict[str, Any]:
        final_status = prepared.terminal_status or TerminalState.completed.value
        result = prepared.answer.model_dump()
        result.update(
            answer_mode=stage_input.profile.answer_mode.value,
            assurance_level=stage_input.profile.assurance_level.value,
            verification_report=None,
            completion_decision=None,
            audit_refs={
                "evidence_record_count": len(self._web_adapter.grounding.records()),
                "agent_turn_count": await self._repository.count_agent_turns(stage_input.run_id),
                "referenced_artifact_ids": prepared.referenced_artifact_ids,
            },
        )
        if stage_input.final_turn_id:
            await self._repository.update_agent_turn(
                stage_input.final_turn_id,
                status="completed",
                observation=self._final_observation(prepared.answer, final_status),
            )
        await self._checkpoint_workspace(stage_input, final_status)
        await self._repository.session.commit()
        return {"answer": prepared.answer, "result": result, "status": final_status}

    def _verification(
        self,
        profile: RunExecutionProfile,
        prepared: PreparedFinalAnswer,
    ) -> VerificationReport:
        validation_outcomes = []
        if profile.assurance_level == AssuranceLevel.full:
            validation_outcomes = [
                self._chart_adapter.validate(prepared.answer.model_dump(), {})
                if self._chart_adapter.attempted and not self._web_adapter.attempted
                else self._web_adapter.validate(
                    prepared.answer.model_dump(),
                    prepared.evidence_pack,
                )
            ]
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
        verification: VerificationReport,
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
            "evidence_record_count": len(self._web_adapter.grounding.records()),
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
    def _final_observation(answer: FinalAnswer, final_status: str) -> dict[str, str]:
        return {
            "kind": "final_answer",
            "status": final_status,
            "summary": answer.summary,
        }
