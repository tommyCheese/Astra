from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field


class ToolExecutionError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message

    def to_payload(self) -> Dict[str, str]:
        return {"category": self.category, "message": self.message}


class ToolSpec(BaseModel):
    name: str
    version: str
    description: str = ""
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    permission: str
    side_effect_level: str
    timeout_seconds: int = 20
    retry_policy: Dict[str, Any] = Field(default_factory=dict)
    error_categories: List[str] = Field(default_factory=list)
    idempotent: bool = True
    capabilities: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    risk: str = "low"
    execution_backend: str = "in_process"
    resource_profile: Dict[str, Any] = Field(default_factory=dict)
    artifact_behavior: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if not self.permissions:
            self.permissions = [self.permission]
        if not self.capabilities:
            self.capabilities = [self.permission]


class ArtifactRef(BaseModel):
    id: str
    type: str
    mime_type: str
    content_url: Optional[str] = None
    size_bytes: int = 0
    checksum: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ToolResultEnvelope(BaseModel):
    status: str = "succeeded"
    data: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[ArtifactRef] = Field(default_factory=list)


class CapabilityAvailability(BaseModel):
    capability: str
    available: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    tool_call_id: str
    step_id: Optional[str]
    trace_id: str
    artifact_service: Any
    sandbox_service: Any


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    async def run(self, tool_input: Dict[str, Any], *, context: Optional[ToolExecutionContext] = None) -> Dict[str, Any]:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolExecutionError("tool_not_allowed", f"Tool is not registered: {name}") from exc

    def specs(self) -> Dict[str, ToolSpec]:
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
