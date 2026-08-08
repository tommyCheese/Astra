from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FastAgentAction(BaseModel):
    """The complete model-to-runtime protocol for fast-v1."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    action: Literal["answer", "call_tool", "ask_user", "stop"]
    content: str | None = Field(default=None, max_length=200_000)
    tool_name: str | None = Field(default=None, max_length=200)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_action_shape(self) -> "FastAgentAction":
        if self.action == "call_tool" and not self.tool_name:
            raise ValueError("call_tool requires tool_name")
        if self.action in {"answer", "ask_user"} and not self.content:
            raise ValueError(f"{self.action} requires content")
        if self.action != "call_tool" and (self.tool_name or self.tool_input):
            raise ValueError("only call_tool may include tool fields")
        return self


class FastObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool_result", "tool_error", "model_error", "system"]
    status: Literal["succeeded", "failed", "denied"]
    summary: str = Field(max_length=4_000)
    tool_name: str | None = None
    tool_call_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class FastExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "waiting_user", "blocked"]
    answer: str
    observations: list[FastObservation] = Field(default_factory=list)
    model_call_count: int = 0
    tool_action_count: int = 0
    first_token_latency_ms: int | None = None
    elapsed_ms: int = 0
