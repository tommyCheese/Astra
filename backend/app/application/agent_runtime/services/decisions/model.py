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


@dataclass
class StreamedAgentAnswer:
    parts: list[str] = field(default_factory=list)
    completed: bool = False

    @property
    def text(self) -> str:
        return "".join(self.parts).strip()


@dataclass
class ReasoningEventWriter:
    _repository: RunUnitOfWork
    _run_id: str
    _turn_index: int
    _buffer: str = ""
    summary: str = ""
    _last_flush: float = 0.0
    completed: bool = False

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


@dataclass
class ModelDecisionStage:
    """Invoke the model without leaking provider streaming details into orchestration."""

    _repository: RunUnitOfWork
    _model_client: ModelClient
    _on_answer_delta: AnswerDeltaHandler | None
    _reasoning_writer: ReasoningEventWriter | None = None
    candidate_answer: AgentFinalAnswer | None = None
    run_id: str = ""
    goal: str = ""
    turn_index: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    answer_mode: str = "trusted"
    may_stream_answer: bool = False
    active_plan_node_id: str | None = None
    approved_tool_call: ToolCallRecord | None = None
    approved_turn: AgentTurnRecord | None = None

    async def execute(
        self,
        *,
        run_id: str,
        goal: str,
        turn_index: int,
        context: dict[str, Any],
        answer_mode: str,
        may_stream_answer: bool,
        active_plan_node_id: str | None,
        approved_tool_call: ToolCallRecord | None = None,
        approved_turn: AgentTurnRecord | None = None,
    ) -> AgentDecision | AgentObservation:
        self.run_id = run_id
        self.goal = goal
        self.turn_index = turn_index
        self.context = context
        self.answer_mode = answer_mode
        self.may_stream_answer = may_stream_answer
        self.active_plan_node_id = active_plan_node_id
        self.approved_tool_call = approved_tool_call
        self.approved_turn = approved_turn
        self._reasoning_writer = ReasoningEventWriter(
            self._repository,
            run_id,
            turn_index,
        )
        if approved_tool_call and approved_turn:
            return self._forced_decision()
        await self._record_skill_binding()
        streamed_answer = StreamedAgentAnswer()
        try:
            decision, candidate_answer = await self._model_client.decide_with_answer(
                goal,
                context,
                on_delta=(lambda delta: self._accept_answer_delta(streamed_answer, delta)) if may_stream_answer else None,
                on_reasoning_delta=self._reasoning_writer.accept,
            )
        except ModelOutputError as error:
            return await self._recover_invalid_output(streamed_answer, error)
        self.candidate_answer = await self._adopt_streamed_answer(
            streamed_answer,
            decision,
            candidate_answer,
        )
        return decision

    async def complete_reasoning(self, decision: AgentDecision) -> None:
        assert self._reasoning_writer is not None
        await self._reasoning_writer.ensure_completed(decision.reasoning_summary)

    def _forced_decision(self) -> AgentDecision:
        assert self.approved_tool_call is not None
        assert self.approved_turn is not None
        decision = AgentDecision.model_validate(self.approved_turn.decision).model_copy(
            update={
                "tool_name": self.approved_tool_call.tool_name,
                "tool_input": dict(self.approved_tool_call.input),
            }
        )
        self.candidate_answer = None
        return decision

    async def _record_skill_binding(self) -> None:
        if not self.context.get("active_skills"):
            return
        await self._repository.add_event(
            self.run_id,
            "skill.operation_bound",
            {
                "operation": "decision_with_answer",
                "turn_index": self.turn_index,
                "plan_node_id": self.active_plan_node_id,
                "skills": list(self.context["active_skills"]),
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
        streamed_answer: StreamedAgentAnswer,
        decision: AgentDecision,
        candidate_answer: AgentFinalAnswer | None,
    ) -> AgentFinalAnswer | None:
        if decision.decision_type != "finalize" or candidate_answer or not streamed_answer.text:
            return candidate_answer
        logger.warning(
            "agent.decision.streamed_answer_adopted run_id=%s turn=%s mode=%s",
            self.run_id,
            self.turn_index,
            self.answer_mode,
        )
        await self._repository.add_event(
            self.run_id,
            "answer.structure_adopted",
            {"turn_index": self.turn_index, "answer_mode": self.answer_mode},
        )
        return AgentFinalAnswer(
            summary=streamed_answer.text,
            verification_notes=["已采用本轮流式正文；模型未提供独立的结构化答案对象。"],
        )

    async def _recover_invalid_output(
        self,
        streamed_answer: StreamedAgentAnswer,
        error: ModelOutputError,
    ) -> AgentDecision | AgentObservation:
        assert self._reasoning_writer is not None
        if not streamed_answer.text:
            logger.exception(
                "agent.decision.invalid run_id=%s turn=%s",
                self.run_id,
                self.turn_index,
            )
            if self._on_answer_delta:
                await self._on_answer_delta("\0")
            return AgentObservation(
                kind="model_error",
                status="failed",
                summary="模型决策输出无法解析。",
                error={"category": "model_output_error", "message": str(error)},
            )
        if not streamed_answer.completed and self._on_answer_delta:
            await self._on_answer_delta("\1")
        await self._repository.add_event(
            self.run_id,
            "answer.schema_degraded",
            {
                "turn_index": self.turn_index,
                "answer_mode": self.answer_mode,
                "reason": str(error),
            },
        )
        await self._repository.session.commit()
        self.candidate_answer = AgentFinalAnswer(
            summary=streamed_answer.text,
            verification_notes=["辅助结构未通过模型输出协议校验。"],
        )
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary=self._reasoning_writer.summary or "已保留完成生成的回答；辅助结构未通过校验。",
        )
