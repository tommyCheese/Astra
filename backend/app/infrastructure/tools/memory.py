from __future__ import annotations

from typing import Any

from app.domain.memory import MemoryConflictError, MemoryValidationError
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolSpec,
    ToolExecutionContext,
    ToolExecutionError,
    ToolResultEnvelope,
)

MEMORY_KINDS = [
    "semantic_fact",
    "user_preference",
    "episodic_experience",
    "procedure",
    "failure_pattern",
    "evaluation_feedback",
]


def _service(context: ToolExecutionContext | None):
    if context is None or context.memory_service is None or context.run_id is None:
        raise ToolExecutionError("memory_unavailable", "Memory management is unavailable")
    return context.memory_service


class RememberTool(AstraTool):
    spec = AstraToolSpec(
        name="remember",
        version="1.0.0",
        description="Create an auditable Memory candidate from the current Run for later human activation.",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "minLength": 1, "maxLength": 50_000},
                "scope": {"type": "string", "enum": ["run", "task", "session", "user"]},
                "kind": {"type": "string", "enum": MEMORY_KINDS},
                "memory_key": {"type": "string", "minLength": 1, "maxLength": 240},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "importance": {"type": "number", "minimum": 0, "maximum": 1},
                "structured_data": {"type": "object"},
                "expires_in_days": {"type": "integer", "minimum": 1, "maximum": 36_500},
            },
            "required": ["content"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "memory_key": {"type": "string"},
                "scope": {"type": "string"},
                "kind": {"type": "string"},
                "status": {"type": "string"},
                "version": {"type": "integer", "minimum": 1},
                "state_version": {"type": "integer", "minimum": 1},
                "deduplicated": {"type": "boolean"},
            },
            "required": [
                "memory_id",
                "memory_key",
                "scope",
                "kind",
                "status",
                "version",
                "state_version",
                "deduplicated",
            ],
            "additionalProperties": False,
        },
        permission="memory_write",
        side_effect_level="memory_write",
        task_capabilities=["memory.remember", "memory.write"],
        risk="sandboxed",
        idempotent=False,
        resource_profile={"memory": "candidate_write", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        service = _service(context)
        assert context is not None
        try:
            result = await service.remember(
                run_id=context.run_id,
                tool_call_id=context.tool_call_id,
                actor=context.runtime_identity_id,
                content=str(tool_input["content"]),
                scope=str(tool_input.get("scope", "task")),
                kind=str(tool_input.get("kind", "semantic_fact")),
                memory_key=tool_input.get("memory_key"),
                confidence=float(tool_input.get("confidence", 0.8)),
                importance=float(tool_input.get("importance", 0.5)),
                structured_data=dict(tool_input.get("structured_data") or {}),
                expires_in_days=tool_input.get("expires_in_days"),
            )
        except MemoryConflictError as exc:
            raise ToolExecutionError("memory_conflict", str(exc)) from exc
        except (MemoryValidationError, ValueError) as exc:
            raise ToolExecutionError("invalid_memory", str(exc)) from exc
        return ToolResultEnvelope(data=result).model_dump(mode="json")


class ForgetTool(AstraTool):
    spec = AstraToolSpec(
        name="forget",
        version="1.0.0",
        description="Revoke a Memory accessible to the current Run while preserving its audit history.",
        input_schema={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "minLength": 1, "maxLength": 36},
                "reason": {"type": "string", "minLength": 3, "maxLength": 2_000},
            },
            "required": ["memory_id", "reason"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "memory_key": {"type": "string"},
                "status": {"type": "string"},
                "state_version": {"type": "integer", "minimum": 1},
                "forgotten": {"type": "boolean"},
            },
            "required": ["memory_id", "memory_key", "status", "state_version", "forgotten"],
            "additionalProperties": False,
        },
        permission="memory_delete",
        side_effect_level="memory_delete",
        task_capabilities=["memory.forget", "memory.revoke"],
        risk="high",
        idempotent=True,
        resource_profile={"memory": "revoke", "network": "none"},
    )

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        service = _service(context)
        assert context is not None
        try:
            result = await service.forget(
                run_id=context.run_id,
                actor=context.runtime_identity_id,
                memory_id=str(tool_input["memory_id"]),
                reason=str(tool_input["reason"]).strip(),
            )
        except MemoryConflictError as exc:
            raise ToolExecutionError("memory_conflict", str(exc)) from exc
        except (MemoryValidationError, ValueError) as exc:
            raise ToolExecutionError("invalid_memory", str(exc)) from exc
        return ToolResultEnvelope(data=result).model_dump(mode="json")


def memory_tools() -> tuple[AstraTool, ...]:
    return RememberTool(), ForgetTool()
