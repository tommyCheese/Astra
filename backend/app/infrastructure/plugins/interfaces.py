from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.agent.run_result import AgentValidationOutcome
from app.common.schemas.permissions import ActionEffectPlan
from app.infrastructure.plugins.contracts import PluginContribution, PluginDescriptor
from app.infrastructure.tools.base import AstraToolSpec, ToolExecutionContext


@dataclass(frozen=True)
class PluginHealthReport:
    healthy: bool
    reason: str | None = None


@dataclass(frozen=True)
class PluginResultProcessingOutput:
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
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]: ...


class RuntimeBackend(ToolExecutor):
    """Host-owned execution backend; providers receive no host service objects."""



class ToolEffectAnalyzer(ABC):
    @abstractmethod
    def analyze(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        *,
        task_id: str,
    ) -> ActionEffectPlan: ...


class PluginResultProcessor(ABC):
    @abstractmethod
    def process(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        result: dict[str, Any],
    ) -> PluginResultProcessingOutput: ...

    def process_failure(
        self,
        spec: AstraToolSpec,
        tool_input: dict[str, Any],
        error: dict[str, Any],
    ) -> dict[str, Any]:
        return {}


class PluginResultAdapter(ABC):
    @abstractmethod
    def adapt(self, result: dict[str, Any]) -> dict[str, Any]: ...


class PluginResultValidator(ABC):
    @abstractmethod
    def validate(
        self,
        result: dict[str, Any],
        evidence: dict[str, Any],
    ) -> AgentValidationOutcome: ...


class PluginApprovalPresenter(ABC):
    @abstractmethod
    def safe_preview(self, spec: AstraToolSpec, tool_input: dict[str, Any]) -> str: ...

    @abstractmethod
    def similar_matcher(
        self, spec: AstraToolSpec, tool_input: dict[str, Any]
    ) -> dict[str, Any] | None: ...


class PluginHealthProbe(ABC):
    @abstractmethod
    async def check(self) -> PluginHealthReport: ...
