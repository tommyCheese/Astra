import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision, AgentReflection
from app.common.schemas.agent.planning import PlanDraft, TaskContract
from app.common.schemas.agent.run_result import AgentFinalAnswer, AgentRunMemoryCandidate
from app.common.schemas.agent.types import ReasoningEffort
from app.common.schemas.model_providers import ModelThinkingSnapshot
from app.domain.agent_profile import AgentProfile

logger = logging.getLogger("astra.model")

AnswerDeltaCallback = Callable[[str], Awaitable[None]]
StreamFieldCallbacks = dict[str, AnswerDeltaCallback]
ModelThinkingObserver = Callable[[dict[str, Any]], Awaitable[None]]


class DeferredUsageInvocation:
    """Keep usage-ledger writes out of the first-token critical path."""

    def __init__(
        self,
        recorder,
        *,
        provider: str,
        model: str,
        operation: str,
        attempt: int,
    ):
        self.recorder = recorder
        self.params = {
            "provider": provider,
            "model": model,
            "operation": operation,
            "attempt": attempt,
        }
        self.task: asyncio.Task[str | None] | None = None

    def start(self) -> None:
        if self.recorder is not None and self.task is None:
            self.task = asyncio.create_task(self.recorder.start(**self.params))

    async def resolve(self) -> str | None:
        self.start()
        return await self.task if self.task is not None else None


def model_http_client_options(settings: AstraRuntimeSettings) -> dict[str, Any]:
    """Build the shared transport policy used by every real model provider."""
    return {
        "http2": settings.model_http2_enabled,
        "timeout": httpx.Timeout(
            connect=settings.model_http_connect_timeout_seconds,
            read=settings.model_http_read_timeout_seconds,
            write=settings.model_http_write_timeout_seconds,
            pool=settings.model_http_pool_timeout_seconds,
        ),
        "limits": httpx.Limits(
            max_connections=settings.model_http_max_connections,
            max_keepalive_connections=settings.model_http_max_keepalive_connections,
            keepalive_expiry=settings.model_http_keepalive_expiry_seconds,
        ),
    }


class ModelConfigurationError(RuntimeError):
    pass


class ModelOutputError(RuntimeError):
    pass


class ModelClient(ABC):
    async def aclose(self) -> None:
        """Release transport resources owned by this client."""
        return None

    def bind_agent_profile(self, profile: AgentProfile) -> None:
        """Bind the immutable Profile selected for the current Run."""
        return None

    def bind_reasoning_effort(self, effort: ReasoningEffort | str) -> None:
        """Bind the immutable effective reasoning effort selected for the current Run."""
        return None

    def bind_model_thinking(self, thinking: ModelThinkingSnapshot | dict[str, Any] | None) -> None:
        """Bind the immutable effective model-thinking selection for the current Run."""
        return None

    def bind_model_thinking_observer(self, observer: ModelThinkingObserver | None) -> None:
        """Observe Provider-visible thinking text without changing model method contracts."""
        return None

    def bind_skills(self, skills: list[dict[str, Any]]) -> None:
        """Bind revision-pinned Skill instruction blocks selected for the current Run."""
        return None

    def bind_agent_execution(self, agent_execution_id: str | None) -> None:
        """Bind usage attribution for an isolated AgentExecution."""
        recorder = getattr(self, "usage_recorder", None)
        if recorder is not None and hasattr(recorder, "agent_execution_id"):
            recorder.agent_execution_id = agent_execution_id

    async def generate_context_checkpoint(self, prompt: str):
        """Use ordinary text generation for an Astra-owned checkpoint request."""
        raise ModelOutputError("This ordinary model client cannot generate checkpoints")

    @abstractmethod
    async def contract(self, goal: str) -> TaskContract:
        raise NotImplementedError

    @abstractmethod
    async def plan(
        self,
        goal: str,
        *,
        contract: TaskContract,
    ) -> PlanDraft:
        raise NotImplementedError

    @abstractmethod
    async def synthesize(
        self,
        goal: str,
        tool_outputs: list[dict[str, Any]],
        *,
        on_delta: AnswerDeltaCallback | None = None,
    ) -> AgentFinalAnswer:
        raise NotImplementedError

    @abstractmethod
    async def decide(self, goal: str, context: dict[str, Any]) -> AgentDecision:
        raise NotImplementedError

    async def decide_with_answer(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        on_delta: AnswerDeltaCallback | None = None,
        on_reasoning_delta: AnswerDeltaCallback | None = None,
    ) -> tuple[AgentDecision, AgentFinalAnswer | None]:
        decision = await self.decide(goal, context)
        if on_reasoning_delta:
            await on_reasoning_delta(decision.reasoning_summary)
            await on_reasoning_delta("\1")
        return decision, None

    async def standard_decide(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        on_delta: AnswerDeltaCallback | None = None,
    ) -> dict[str, Any]:
        """Return the compact transport vocabulary used by standard composition."""
        streamed_parts: list[str] = []

        async def capture(delta: str) -> None:
            if delta and delta not in {"\0", "\1"}:
                streamed_parts.append(delta)
            if on_delta is not None:
                await on_delta(delta)

        async def ignore_reasoning(_delta: str) -> None:
            return None

        try:
            decision, answer = await self.decide_with_answer(
                goal,
                context,
                on_delta=capture if on_delta is not None else None,
                on_reasoning_delta=ignore_reasoning,
            )
        except ModelOutputError:
            streamed = "".join(streamed_parts).strip()
            if not streamed:
                raise
            return {
                "protocol_version": 1,
                "action": "answer",
                "content": streamed,
                "tool_name": None,
                "tool_input": {},
                "reason": "Adopted the already-streamed answer after a protocol error.",
            }
        mapping = {
            "finalize": "answer",
            "call_tool": "call_tool",
            "ask_user": "ask_user",
            "blocked": "stop",
        }
        action = mapping.get(decision.decision_type, "stop")
        adopted_stream = all((answer is None, bool("".join(streamed_parts).strip())))
        content = answer.summary if answer is not None else "".join(streamed_parts).strip() or None
        if action == "answer" and content is None:
            synthesized = await self.synthesize(
                goal,
                list(context.get("recent_observations") or []),
                on_delta=on_delta,
            )
            content = synthesized.summary
        if action == "ask_user":
            content = decision.expected_observation or "请告诉我你希望我完成的具体任务或问题。"
        return {
            "protocol_version": 1,
            "action": action,
            "content": content,
            "tool_name": decision.tool_name if action == "call_tool" else None,
            "tool_input": decision.tool_input if action == "call_tool" else {},
            "reason": (
                "Adopted the streamed answer because no structured answer was returned."
                if action == "answer" and adopted_stream
                else decision.reasoning_summary
            ),
        }

    @abstractmethod
    async def reflect(self, goal: str, context: dict[str, Any]) -> AgentReflection:
        raise NotImplementedError

    async def finalize(
        self, goal: str, context: dict[str, Any], *, on_delta: AnswerDeltaCallback | None = None
    ) -> AgentFinalAnswer:
        return await self.synthesize(goal, [{"evidence_pack": context.get("evidence_pack", {})}], on_delta=on_delta)

    @abstractmethod
    async def extract_memory_candidates(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[AgentRunMemoryCandidate]:
        raise NotImplementedError
