"""Normalize tool output into bounded observations and evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.context_compaction.tool_outputs import ToolOutputGovernanceService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentObservation
from app.common.schemas.context_compaction import CompactionContextReference, ContextOwnerRole
from app.common.schemas.permissions import ActionEffectPlan
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.tools.base import AstraToolSpec


@dataclass(frozen=True)
class NormalizedObservation:
    tool_output: dict[str, Any]
    observation: AgentObservation
    context_observation: AgentObservation
    step_evidence: dict[str, Any]
    validation_inputs: tuple[dict[str, Any], ...] = ()
    completion_signals: tuple[str, ...] = ()


@dataclass
class ObservationNormalizationStage:
    settings: AstraRuntimeSettings
    plugin_runtime: PluginRuntimeState
    normalize_tool_output: Any

    async def execute(
        self,
        *,
        tool_spec: AstraToolSpec,
        tool_call: ToolCallRecord,
        tool_output: dict[str, Any],
        effect_plan: ActionEffectPlan,
        runtime_identity_id: str,
        active_plan_node_id: str | None,
    ) -> NormalizedObservation:
        tool_output = self.normalize_tool_output(tool_spec.name, tool_output)
        tool_output.update(
            tool_call_id=tool_call.id,
            plan_node_id=tool_call.plan_node_id,
            node_execution_id=tool_call.node_execution_id,
        )
        governed_output = await ToolOutputGovernanceService(self.settings).normalize(
            role=ContextOwnerRole.root_execution,
            tool_name=tool_spec.name,
            status="succeeded",
            output=tool_output,
            key_fields={
                "tool_call_id": tool_call.id,
                "plan_node_id": tool_call.plan_node_id,
                "runtime_identity_id": runtime_identity_id,
            },
            persist=self._reference_persister(effect_plan, tool_call),
        )
        processed = self.plugin_runtime.process(
            tool_spec,
            tool_call.input,
            tool_output,
        )
        observation = processed.observation
        step_evidence = {"fragments": list(processed.evidence)}
        if active_plan_node_id is not None:
            observation.plan_node_id = active_plan_node_id
        context_observation = (
            observation.model_copy(
                update={
                    "data": {
                        "tool_name": tool_spec.name,
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
            processed.validation_inputs,
            processed.completion_signals,
        )

    @staticmethod
    def _reference_persister(effect_plan: ActionEffectPlan, tool_call: ToolCallRecord):
        data_labels = tuple(dict.fromkeys(label for effect in effect_plan.effects for label in effect.data_labels))

        async def persist(_serialized: bytes, checksum: str) -> CompactionContextReference:
            return CompactionContextReference(
                kind="tool_call",
                ref=f"tool_call:{tool_call.id}",
                content_hash=checksum,
                data_labels=data_labels,
                allowed_purposes=("agent_context", "completion_validation"),
            )

        return persist
