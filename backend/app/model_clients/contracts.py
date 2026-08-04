import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.agent_profile import AgentProfile
from app.core.config import Settings
from app.schemas.agent.execution_state import AgentDecision, AgentReflection
from app.schemas.agent.planning import PlanDraft, TaskContract
from app.schemas.agent.run_result import FinalAnswer, MemoryRecord
from app.schemas.agent.types import ReasoningEffort
from app.schemas.models import ModelThinkingSnapshot

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


def model_http_client_options(settings: Settings) -> dict[str, Any]:
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
    ) -> FinalAnswer:
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
    ) -> tuple[AgentDecision, FinalAnswer | None]:
        decision = await self.decide(goal, context)
        if on_reasoning_delta:
            await on_reasoning_delta(decision.reasoning_summary)
            await on_reasoning_delta("\1")
        return decision, None

    @abstractmethod
    async def reflect(self, goal: str, context: dict[str, Any]) -> AgentReflection:
        raise NotImplementedError

    @abstractmethod
    async def finalize(
        self, goal: str, context: dict[str, Any], *, on_delta: AnswerDeltaCallback | None = None
    ) -> FinalAnswer:
        raise NotImplementedError

    @abstractmethod
    async def extract_memory_candidates(
        self,
        goal: str,
        context: dict[str, Any],
    ) -> list[MemoryRecord]:
        raise NotImplementedError
