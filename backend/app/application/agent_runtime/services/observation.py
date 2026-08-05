"""Normalize tool output into bounded observations and evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.agent_runtime.result_adapters import AgentToolResultProcessorRegistry
from app.application.context_compaction.tool_outputs import ToolOutputGovernanceService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.context_compaction import CompactionContextReference, ContextOwnerRole
from app.common.schemas.permissions import ActionEffectPlan
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.tools.base import AstraToolSpec


@dataclass(frozen=True)
class ObservationStageInput:
    tool_spec: AstraToolSpec
    tool_call: ToolCallRecord
    tool_output: dict[str, Any]
    effect_plan: ActionEffectPlan
    runtime_identity_id: str
    active_plan_node_id: str | None


@dataclass(frozen=True)
class NormalizedObservation:
    tool_output: dict[str, Any]
    observation: AgentObservation
    context_observation: AgentObservation
    step_evidence: dict[str, Any]


class ObservationNormalizationStage:
    def __init__(
        self,
        settings: AstraRuntimeSettings,
        processors: AgentToolResultProcessorRegistry,
        normalize_tool_output,
    ) -> None:
        self._governance = ToolOutputGovernanceService(settings)
        self._processors = processors
        self._normalize_tool_output = normalize_tool_output

    async def execute(self, stage_input: ObservationStageInput) -> NormalizedObservation:
        tool_output = self._with_call_references(stage_input)
        governed_output = await self._governance.normalize(
            role=ContextOwnerRole.root_execution,
            tool_name=stage_input.tool_spec.name,
            status="succeeded",
            output=tool_output,
            key_fields={
                "tool_call_id": stage_input.tool_call.id,
                "plan_node_id": stage_input.tool_call.plan_node_id,
                "runtime_identity_id": stage_input.runtime_identity_id,
            },
            persist=self._reference_persister(stage_input),
        )
        observation, step_evidence = self._processor_observation(stage_input, tool_output)
        if stage_input.active_plan_node_id is not None:
            observation.plan_node_id = stage_input.active_plan_node_id
        context_observation = (
            observation.model_copy(
                update={
                    "data": {
                        "tool_name": stage_input.tool_spec.name,
                        "normalized_output": governed_output.model_dump(
                            mode="json",
                            exclude_none=True,
                        ),
                    }
                }
            )
            if governed_output.externalized
            else observation
        )
        return NormalizedObservation(
            tool_output,
            observation,
            context_observation,
            step_evidence,
        )

    def _with_call_references(self, stage_input: ObservationStageInput) -> dict[str, Any]:
        tool_output = self._normalize_tool_output(
            stage_input.tool_spec.name,
            stage_input.tool_output,
        )
        tool_output.update(
            tool_call_id=stage_input.tool_call.id,
            plan_node_id=stage_input.tool_call.plan_node_id,
            node_execution_id=stage_input.tool_call.node_execution_id,
        )
        return tool_output

    def _processor_observation(
        self,
        stage_input: ObservationStageInput,
        tool_output: dict[str, Any],
    ) -> tuple[AgentObservation, dict[str, Any]]:
        processor = self._processors.for_tool(stage_input.tool_spec.name)
        if processor:
            return processor.process(stage_input.tool_spec.name, tool_output)
        return (
            AgentObservation(
                kind="tool_result",
                status="succeeded",
                summary=f"{stage_input.tool_spec.name} completed",
                data={"tool_name": stage_input.tool_spec.name, **tool_output},
            ),
            {},
        )

    @staticmethod
    def _reference_persister(stage_input: ObservationStageInput):
        data_labels = tuple(
            dict.fromkeys(
                label for effect in stage_input.effect_plan.effects for label in effect.data_labels
            )
        )

        async def persist(_serialized: bytes, checksum: str) -> CompactionContextReference:
            return CompactionContextReference(
                kind="tool_call",
                ref=f"tool_call:{stage_input.tool_call.id}",
                content_hash=checksum,
                data_labels=data_labels,
                allowed_purposes=("agent_context", "completion_validation"),
            )

        return persist
