from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field


class ToolExecutionError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message

    def to_payload(self) -> dict[str, str]:
        return {"category": self.category, "message": self.message}


class ToolSpec(BaseModel):
    name: str
    version: str
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permission: str
    side_effect_level: str
    timeout_seconds: int = 20
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    error_categories: list[str] = Field(default_factory=list)
    idempotent: bool = True
    capabilities: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    risk: str = "low"
    execution_backend: str = "in_process"
    resource_profile: dict[str, Any] = Field(default_factory=dict)
    artifact_behavior: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.permissions:
            self.permissions = [self.permission]
        if not self.capabilities:
            self.capabilities = [self.permission]


class ArtifactRef(BaseModel):
    id: str
    type: str
    mime_type: str
    content_url: str | None = None
    size_bytes: int = 0
    checksum: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResultEnvelope(BaseModel):
    status: str = "succeeded"
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class CapabilityAvailability(BaseModel):
    capability: str
    available: bool
    reason: str | None = None


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    tool_call_id: str
    step_id: str | None
    trace_id: str
    artifact_service: Any
    sandbox_service: Any


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    async def run(
        self, tool_input: dict[str, Any], *, context: ToolExecutionContext | None = None
    ) -> dict[str, Any]:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError("tool_not_allowed", f"Tool is not registered: {name}") from exc

    def specs(self) -> dict[str, ToolSpec]:
        return {name: tool.spec for name, tool in self._tools.items()}

    def extend(self, tools: Iterable[Tool]) -> "ToolRegistry":
        for tool in tools:
            self.register(tool)
        return self

    @classmethod
    def compose(cls, *registries: "ToolRegistry") -> "ToolRegistry":
        combined = cls()
        for registry in registries:
            combined.extend(registry._tools.values())
        return combined
