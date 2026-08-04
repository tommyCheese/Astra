from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.plugins.contracts import PluginContribution, PluginDescriptor
from app.schemas.agent.execution_state import AgentObservation
from app.schemas.agent.run_result import ValidationOutcome
from app.schemas.permissions import ActionEffectPlan
from app.tools.base import ToolExecutionContext, ToolSpec


@dataclass(frozen=True)
class HealthReport:
    healthy: bool
    reason: str | None = None


@dataclass(frozen=True)
class ProcessorOutput:
    observation: AgentObservation
    evidence: dict[str, Any] = field(default_factory=dict)
    validation_input: dict[str, Any] = field(default_factory=dict)
    completion_signals: tuple[str, ...] = ()


class ToolProviderPlugin(ABC):
    descriptor: PluginDescriptor

    @abstractmethod
    def contribute(self) -> PluginContribution: ...


class ToolExecutor(ABC):
    @abstractmethod
    async def execute(
        self,
        spec: ToolSpec,
        tool_input: dict[str, Any],
        *,
        context: ToolExecutionContext,
    ) -> dict[str, Any]: ...


class EffectAnalyzer(ABC):
    @abstractmethod
    def analyze(
        self,
        spec: ToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan: ...


class ResultProcessor(ABC):
    @abstractmethod
    def process(
        self,
        spec: ToolSpec,
        tool_input: dict[str, Any],
        result: dict[str, Any],
    ) -> ProcessorOutput: ...


class ResultAdapter(ABC):
    @abstractmethod
    def adapt(self, result: dict[str, Any]) -> dict[str, Any]: ...


class Validator(ABC):
    @abstractmethod
    def validate(
        self,
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> ValidationOutcome: ...


class ApprovalPresenter(ABC):
    @abstractmethod
    def safe_preview(self, spec: ToolSpec, tool_input: dict[str, Any]) -> str: ...

    @abstractmethod
    def similar_matcher(
        self, spec: ToolSpec, tool_input: dict[str, Any]
    ) -> dict[str, Any] | None: ...


class HealthProbe(ABC):
    @abstractmethod
    async def check(self) -> HealthReport: ...
