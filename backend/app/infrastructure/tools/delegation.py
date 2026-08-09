from __future__ import annotations

from typing import Any

from app.application.subagents.governance import DelegationAuthorizationError
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.subagents import SubagentFanoutRequest, SubagentFanoutResult
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolExecutionContext,
    ToolExecutionError,
    ToolResultEnvelope,
)
from app.infrastructure.tools.memory import memory_tools
from app.infrastructure.tools.workspace import workspace_tools


class SwarmTool(AstraTool):
    """Astra control-plane facade for governed concurrent delegation."""

    spec = AstraToolSpec(
        name="swarm",
        version="1.0.0",
        description=(
            "Delegate two or more independent pieces of trusted work to a bounded "
            "Astra child-Agent group and join their verified results."
        ),
        input_schema=SubagentFanoutRequest.model_json_schema(),
        output_schema=SubagentFanoutResult.model_json_schema(),
        permission="delegation_create",
        permissions=["delegation_create"],
        capabilities=["delegation_create"],
        task_capabilities=[
            "parallel_delegation",
            "independent_research",
            "independent_review",
        ],
        side_effect_level="control_plane",
        execution_backend="astra.runtime",
        idempotent=True,
        timeout_seconds=20,
        retry_policy={"max_attempts": 1},
        error_categories=[
            "subagent_unavailable",
            "delegation_rejected",
            "fanout_conflict",
        ],
        provider_id="astra.builtin",
        provider_digest="builtin",
        trust_level="platform",
    )

    async def run(
        self,
        tool_input: dict[str, Any],
        *,
        context: ToolExecutionContext | None = None,
    ) -> dict[str, Any]:
        if context is None or context.delegation_context is None:
            raise ToolExecutionError("subagent_unavailable", "Swarm requires an Astra supervisor context")
        fanout = SubagentFanoutRequest.model_validate(tool_input)
        dispatcher = context.delegation_context
        if not hasattr(dispatcher, "delegate_tasks"):
            raise ToolExecutionError("subagent_unavailable", "Swarm dispatcher is unavailable")
        try:
            result = await dispatcher.delegate_tasks(fanout)
        except DelegationAuthorizationError as exc:
            raise ToolExecutionError(exc.issue.code.value, exc.issue.message) from exc
        except ValueError as exc:
            raise ToolExecutionError("delegation_rejected", str(exc)) from exc
        return ToolResultEnvelope(data=SubagentFanoutResult.model_validate(result).model_dump(mode="json")).model_dump(
            mode="json"
        )


def build_runtime_tool_registry(
    settings: AstraRuntimeSettings | None = None,
) -> AstraToolRegistry:
    registry = AstraToolRegistry()
    tools = (SwarmTool(), *workspace_tools(), *memory_tools())
    registry.extend(
        tool
        for tool in tools
        if settings is None
        or (
            (tool.spec.name != "remember" or settings.agent_memory_write_enabled)
            and settings.tool_enabled(
                tool.spec.name,
                default=tool.spec.enabled_by_default,
            )
        )
    )
    return registry
