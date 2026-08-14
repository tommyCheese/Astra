"""Strict wire schemas for the supported Astra AG-UI profile."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgUiEventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"
    ACTIVITY_SNAPSHOT = "ACTIVITY_SNAPSHOT"
    ACTIVITY_DELTA = "ACTIVITY_DELTA"
    REASONING_START = "REASONING_START"
    REASONING_MESSAGE_START = "REASONING_MESSAGE_START"
    REASONING_MESSAGE_CONTENT = "REASONING_MESSAGE_CONTENT"
    REASONING_MESSAGE_END = "REASONING_MESSAGE_END"
    REASONING_END = "REASONING_END"
    RAW = "RAW"
    CUSTOM = "CUSTOM"


class AgUiMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    role: Literal["developer", "system", "user", "assistant", "tool", "activity", "reasoning"]
    content: str | list[dict[str, Any]] = ""
    name: str | None = Field(default=None, max_length=200)
    toolCallId: str | None = Field(default=None, max_length=200)
    toolCalls: list[dict[str, Any]] | None = None
    activityType: str | None = Field(default=None, max_length=200)


class AgUiTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4_000)
    parameters: dict[str, Any] = Field(default_factory=dict)


class AgUiContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=4_000)
    value: str = Field(max_length=32_000)


class AgUiResumeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interruptId: str = Field(min_length=1, max_length=200)
    status: Literal["resolved", "cancelled"]
    payload: Any = None


class AgUiAstraProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profileVersion: Literal["astra-ag-ui-v1"] = "astra-ag-ui-v1"
    answerMode: Literal["standard", "trusted"] = "standard"
    planExecution: Literal["auto", "confirm"] | None = None
    model: dict[str, Any] | None = None
    skillIds: list[str] = Field(default_factory=list, max_length=8)
    subagentMode: Literal["auto", "required"] = "auto"
    sessionId: str | None = Field(default=None, min_length=1, max_length=120)


class AgUiForwardedProperties(BaseModel):
    model_config = ConfigDict(extra="forbid")

    astra: AgUiAstraProperties = Field(default_factory=AgUiAstraProperties)


class AgUiRunAgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threadId: str = Field(min_length=1, max_length=200)
    runId: str = Field(min_length=1, max_length=200)
    parentRunId: str | None = Field(default=None, max_length=200)
    state: Any = Field(default_factory=dict)
    messages: list[AgUiMessage] = Field(default_factory=list, max_length=2_000)
    tools: list[AgUiTool] = Field(default_factory=list, max_length=100)
    context: list[AgUiContext] = Field(default_factory=list, max_length=100)
    forwardedProps: AgUiForwardedProperties = Field(default_factory=AgUiForwardedProperties)
    resume: list[AgUiResumeItem] | None = Field(default=None, max_length=100)

KNOWN_EVENT_REQUIREMENTS: dict[AgUiEventType, tuple[str, ...]] = {
    AgUiEventType.RUN_STARTED: ("threadId", "runId"),
    AgUiEventType.RUN_FINISHED: ("threadId", "runId"),
    AgUiEventType.RUN_ERROR: ("message",),
    AgUiEventType.TEXT_MESSAGE_START: ("messageId", "role"),
    AgUiEventType.TEXT_MESSAGE_CONTENT: ("messageId", "delta"),
    AgUiEventType.TEXT_MESSAGE_END: ("messageId",),
    AgUiEventType.TOOL_CALL_START: ("toolCallId", "toolCallName"),
    AgUiEventType.TOOL_CALL_ARGS: ("toolCallId", "delta"),
    AgUiEventType.TOOL_CALL_END: ("toolCallId",),
    AgUiEventType.TOOL_CALL_RESULT: ("messageId", "toolCallId", "content"),
    AgUiEventType.STATE_SNAPSHOT: ("snapshot",),
    AgUiEventType.STATE_DELTA: ("delta",),
    AgUiEventType.MESSAGES_SNAPSHOT: ("messages",),
    AgUiEventType.ACTIVITY_SNAPSHOT: ("messageId", "activityType", "content"),
    AgUiEventType.ACTIVITY_DELTA: ("messageId", "activityType", "patch"),
    AgUiEventType.REASONING_START: ("messageId",),
    AgUiEventType.REASONING_MESSAGE_START: ("messageId", "role"),
    AgUiEventType.REASONING_MESSAGE_CONTENT: ("messageId", "delta"),
    AgUiEventType.REASONING_MESSAGE_END: ("messageId",),
    AgUiEventType.REASONING_END: ("messageId",),
    AgUiEventType.RAW: ("event",),
    AgUiEventType.CUSTOM: ("name", "value"),
}


def validate_public_event(event: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()) > 256_000:
        raise ValueError("AG-UI event exceeds the public size limit")
    event_type = _public_event_type(event)
    _validate_required_fields(event_type, event)
    _validate_content_fields(event_type, event)
    return event


def _public_event_type(event: dict[str, Any]) -> AgUiEventType:
    try:
        return AgUiEventType(event.get("type"))
    except (TypeError, ValueError) as error:
        raise ValueError("Unsupported AG-UI event type") from error


def _validate_required_fields(event_type: AgUiEventType, event: dict[str, Any]) -> None:
    missing = [field for field in KNOWN_EVENT_REQUIREMENTS[event_type] if field not in event]
    if missing:
        raise ValueError(f"Missing AG-UI event fields: {', '.join(missing)}")


def _validate_content_fields(event_type: AgUiEventType, event: dict[str, Any]) -> None:
    if event_type in {AgUiEventType.TEXT_MESSAGE_CONTENT, AgUiEventType.REASONING_MESSAGE_CONTENT} and (
        not isinstance(event["delta"], str) or not event["delta"]
    ):
        raise ValueError("AG-UI content delta must be a non-empty string")
    if event_type == AgUiEventType.REASONING_MESSAGE_START and event["role"] != "reasoning":
        raise ValueError("AG-UI reasoning messages must use the reasoning role")
