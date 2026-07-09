from abc import ABC, abstractmethod
from typing import Any, Dict

from pydantic import BaseModel


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
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    permission: str
    side_effect_level: str


class Tool(ABC):
    spec: ToolSpec

    @abstractmethod
    async def run(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
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
