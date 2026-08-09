"""Mutable state and composed operations for one Trusted execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.application.agent_runtime.services.shared.progress import ExecutionProgress
from app.common.schemas.agent.run_policy import EffectiveReasoningPolicy, RunExecutionProfile
from app.domain.execution.contracts import SubagentSupervisorPort

if TYPE_CHECKING:
    from app.application.agent_runtime.services.completion.finalization import AgentFinalizationStage
    from app.application.agent_runtime.services.completion.node_completion import NodeCompletionStage
    from app.application.agent_runtime.services.context.turn_preparation import RootTurnPreparationStage
    from app.application.agent_runtime.services.decisions.control import ControlDecisionStage
    from app.application.agent_runtime.services.decisions.root import RootDecisionStage
    from app.application.agent_runtime.services.execution.tool_action import InvocationPipeline
    from app.infrastructure.db.models.runs import RunRecord


@dataclass
class TrustedRuntimeState:
    run: RunRecord
    profile: RunExecutionProfile
    approved_tool_call: Any = None
    approved_turn: Any = None
    approved_request_snapshot: dict | None = None
    workspace_path: str | None = None
    workspace_changed: bool = False
    required_subagent_missing: bool = False
    final_turn_id: str | None = None
    streamed_final_answer: Any = None
    terminal_status: str | None = None
    terminal_summary: str | None = None


@dataclass(frozen=True)
class TrustedRuntime:
    run: RunRecord
    initial_turn_count: int
    profile: RunExecutionProfile
    policy: EffectiveReasoningPolicy
    max_turns: int
    max_tool_calls: int | None
    max_reflections: int
    max_replans: int
    progress: ExecutionProgress
    state: TrustedRuntimeState
    preparation_stage: RootTurnPreparationStage
    decision_stage: RootDecisionStage
    completion_stage: NodeCompletionStage
    control_stage: ControlDecisionStage
    tool_stage: InvocationPipeline
    subagent_supervisor: SubagentSupervisorPort | None
    execution_mode: str
    finalization_stage: AgentFinalizationStage
    tool_outputs: list[dict[str, Any]]
