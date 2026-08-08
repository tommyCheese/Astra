"""Model decision invocation, streamed reasoning, and output recovery."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.model_clients.contracts import ModelClient, ModelOutputError
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

logger = logging.getLogger("astra.agent_decision")
AnswerDeltaHandler = Callable[[str], Awaitable[None]]
REASONING_FLUSH_INTERVAL_SECONDS = 0.05
REASONING_FLUSH_MAX_CHARS = 128


@dataclass(frozen=True)
class DecisionStageInput:
    run_id: str
    goal: str
    turn_index: int
    context: dict[str, Any]
    answer_mode: str
    legacy_standard_mode: bool
    may_stream_answer: bool
    active_plan_node_id: str | None
    approved_tool_call: ToolCallRecord | None = None
    approved_turn: AgentTurnRecord | None = None


@dataclass
class StreamedAgentAnswer:
    parts: list[str] = field(default_factory=list)
    completed: bool = False

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()


@dataclass(frozen=True)
class DecisionStageResult:
    decision: AgentDecision
    candidate_answer: AgentFinalAnswer | None
    reasoning_summary: str
    reasoning_completed: bool


@dataclass(frozen=True)
class DecisionStageFailure:
    observation: AgentObservation
    reasoning_summary: str


class ReasoningEventWriter:
    def __init__(self, repository: RunUnitOfWork, run_id: str, turn_index: int) -> None:
        self._repository = repository
        self._run_id = run_id
        self._turn_index = turn_index
        self._buffer = ""
        self.summary = ""
        self._last_flush = 0.0
        self.completed = False

    async def accept(self, delta: str) -> None:
        if delta == "\1":
            await self._complete()
            return
        if not delta or len(self.summary) >= 4000:
            return
        safe_delta = delta[: 4000 - len(self.summary)]
        self.summary += safe_delta
        self._buffer += safe_delta
        current_time = time.monotonic()
        should_flush = (
            self._last_flush == 0.0
            or current_time - self._last_flush >= REASONING_FLUSH_INTERVAL_SECONDS
            or len(self._buffer) >= REASONING_FLUSH_MAX_CHARS
        )
        if should_flush:
            await self._flush_delta()
            self._last_flush = current_time

    async def ensure_completed(self, fallback_summary: str) -> None:
        if self.completed:
            return
        await self._flush_delta()
        await self._repository.add_event(
            self._run_id,
            "reasoning.summary.completed",
            {
                "turn_index": self._turn_index,
                "summary": (self.summary or fallback_summary)[:4000],
            },
        )
        self.completed = True
        await self._repository.session.commit()

    async def _complete(self) -> None:
        await self._flush_delta()
        await self._repository.add_event(
            self._run_id,
            "reasoning.summary.completed",
            {"turn_index": self._turn_index, "summary": self.summary[:4000]},
        )
        self.completed = True
        await self._repository.session.commit()

    async def _flush_delta(self) -> None:
        if not self._buffer:
            return
        await self._repository.add_event(
            self._run_id,
            "reasoning.summary.delta",
            {"turn_index": self._turn_index, "delta": self._buffer},
        )
        self._buffer = ""
        await self._repository.session.commit()


class ModelDecisionStage:
    """Invoke the model without leaking provider streaming details into orchestration."""

    def __init__(
        self,
        repository: RunUnitOfWork,
        model_client: ModelClient,
        on_answer_delta: AnswerDeltaHandler | None,
    ) -> None:
        self._repository = repository
        self._model_client = model_client
        self._on_answer_delta = on_answer_delta
        self._reasoning_writer: ReasoningEventWriter | None = None

    async def execute(
        self,
        stage_input: DecisionStageInput,
    ) -> DecisionStageResult | DecisionStageFailure:
        self._reasoning_writer = ReasoningEventWriter(
            self._repository,
            stage_input.run_id,
            stage_input.turn_index,
        )
        if stage_input.approved_tool_call and stage_input.approved_turn:
            return self._forced_decision(stage_input)
        await self._record_skill_binding(stage_input)
        if stage_input.legacy_standard_mode:
            await self._repository.session.commit()
        streamed_answer = StreamedAgentAnswer()
        try:
            decision, candidate_answer = await self._model_client.decide_with_answer(
                stage_input.goal,
                stage_input.context,
                on_delta=(lambda delta: self._accept_answer_delta(streamed_answer, delta))
                if stage_input.may_stream_answer
                else None,
                on_reasoning_delta=self._reasoning_writer.accept,
            )
        except ModelOutputError as error:
            return await self._recover_invalid_output(stage_input, streamed_answer, error)
        candidate_answer = await self._adopt_streamed_answer(
            stage_input,
            streamed_answer,
            decision,
            candidate_answer,
        )
        return DecisionStageResult(
            decision,
            candidate_answer,
            self._reasoning_writer.summary,
            self._reasoning_writer.completed,
        )

    async def complete_reasoning(self, decision: AgentDecision) -> None:
        assert self._reasoning_writer is not None
        await self._reasoning_writer.ensure_completed(decision.reasoning_summary)

    def _forced_decision(self, stage_input: DecisionStageInput) -> DecisionStageResult:
        assert stage_input.approved_tool_call is not None
        assert stage_input.approved_turn is not None
        decision = AgentDecision.model_validate(stage_input.approved_turn.decision).model_copy(
            update={
                "tool_name": stage_input.approved_tool_call.tool_name,
                "tool_input": dict(stage_input.approved_tool_call.input),
            }
        )
        return DecisionStageResult(decision, None, "", False)

    async def _record_skill_binding(self, stage_input: DecisionStageInput) -> None:
        if not stage_input.context.get("active_skills"):
            return
        await self._repository.add_event(
            stage_input.run_id,
            "skill.operation_bound",
            {
                "operation": "decision_with_answer",
                "turn_index": stage_input.turn_index,
                "plan_node_id": stage_input.active_plan_node_id,
                "skills": list(stage_input.context["active_skills"]),
            },
        )

    async def _accept_answer_delta(
        self,
        streamed_answer: StreamedAgentAnswer,
        delta: str,
    ) -> None:
        if delta == "\1":
            streamed_answer.completed = True
        elif delta and delta != "\0":
            streamed_answer.parts.append(delta)
        if self._on_answer_delta is not None:
            await self._on_answer_delta(delta)

    async def _adopt_streamed_answer(
        self,
        stage_input: DecisionStageInput,
        streamed_answer: StreamedAgentAnswer,
        decision: AgentDecision,
        candidate_answer: AgentFinalAnswer | None,
    ) -> AgentFinalAnswer | None:
        if decision.decision_type != "finalize" or candidate_answer or not streamed_answer.text:
            return candidate_answer
        logger.warning(
            "agent.decision.streamed_answer_adopted run_id=%s turn=%s mode=%s",
            stage_input.run_id,
            stage_input.turn_index,
            stage_input.answer_mode,
        )
        await self._repository.add_event(
            stage_input.run_id,
            "answer.structure_adopted",
            {"turn_index": stage_input.turn_index, "answer_mode": stage_input.answer_mode},
        )
        return AgentFinalAnswer(
            summary=streamed_answer.text,
            verification_notes=["已采用本轮流式正文；模型未提供独立的结构化答案对象。"],
        )

    async def _recover_invalid_output(
        self,
        stage_input: DecisionStageInput,
        streamed_answer: StreamedAgentAnswer,
        error: ModelOutputError,
    ) -> DecisionStageResult | DecisionStageFailure:
        assert self._reasoning_writer is not None
        if not streamed_answer.text:
            logger.exception(
                "agent.decision.invalid run_id=%s turn=%s",
                stage_input.run_id,
                stage_input.turn_index,
            )
            if self._on_answer_delta:
                await self._on_answer_delta("\0")
            return DecisionStageFailure(
                AgentObservation(
                    kind="model_error",
                    status="failed",
                    summary="模型决策输出无法解析。",
                    error={"category": "model_output_error", "message": str(error)},
                ),
                self._reasoning_writer.summary,
            )
        if not streamed_answer.completed and self._on_answer_delta:
            await self._on_answer_delta("\1")
        await self._repository.add_event(
            stage_input.run_id,
            "answer.schema_degraded",
            {
                "turn_index": stage_input.turn_index,
                "answer_mode": stage_input.answer_mode,
                "reason": str(error),
            },
        )
        await self._repository.session.commit()
        return DecisionStageResult(
            AgentDecision(
                decision_type="finalize",
                reasoning_summary=self._reasoning_writer.summary
                or "已保留完成生成的回答；辅助结构未通过校验。",
            ),
            AgentFinalAnswer(
                summary=streamed_answer.text,
                verification_notes=["辅助结构未通过模型输出协议校验。"],
            ),
            self._reasoning_writer.summary,
            self._reasoning_writer.completed,
        )
