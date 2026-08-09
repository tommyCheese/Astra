"""Shared structural objects for the root Agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.application.agent_runtime.services.shared.progress import ExecutionProgress
from app.common.schemas.agent.run_policy import EffectiveReasoningPolicy, RunExecutionProfile
from app.domain.execution.contracts import SubagentSupervisorPort

if TYPE_CHECKING:
    from app.application.agent_runtime.services.completion.finalization import (
        AgentFinalizationStage,
    )
    from app.application.agent_runtime.services.completion.node_completion import (
        NodeCompletionStage,
    )
    from app.application.agent_runtime.services.context.turn_preparation import (
        RootTurnPreparationStage,
    )
    from app.application.agent_runtime.services.decisions.control import ControlDecisionStage
    from app.application.agent_runtime.services.decisions.root import RootDecisionStage
    from app.application.agent_runtime.services.execution.tool_action import InvocationPipeline
    from app.common.schemas.agent.execution_state import AgentDecision
    from app.infrastructure.db.models.permissions import AgentIdentityRecord, ToolCallRecord
    from app.infrastructure.db.models.plans import PlanNodeRecord
    from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord

AgentRuntimeLimits = tuple[int, int | None, int, int]


@dataclass(frozen=True)
class ToolActionInput:
    """Persisted root-action context shared by every tool boundary."""

    run: RunRecord
    run_id: str
    goal: str
    turn_index: int
    turn: AgentTurnRecord
    decision: AgentDecision
    main_identity: AgentIdentityRecord
    active_node: PlanNodeRecord | None
    active_node_execution_id: str | None
    model_context: dict[str, Any]
    execution_mode: str
    is_approved_resume: bool
    approved_request_snapshot: dict[str, Any] | None
    approved_tool_call: ToolCallRecord | None
    workspace_path: str | None
    subagent_supervisor: SubagentSupervisorPort | None


@dataclass
class RootRuntimeState:
    """Trusted execution data that does not cross the canonical Loop boundary."""

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
class RootRuntimeAssembly:
    run: RunRecord
    initial_turn_count: int
    profile: RunExecutionProfile
    policy: EffectiveReasoningPolicy
    max_turns: int
    max_tool_calls: int | None
    max_reflections: int
    max_replans: int
    progress: ExecutionProgress
    state: RootRuntimeState
    preparation_stage: RootTurnPreparationStage
    decision_stage: RootDecisionStage
    completion_stage: NodeCompletionStage
    control_stage: ControlDecisionStage
    tool_stage: InvocationPipeline
    subagent_supervisor: SubagentSupervisorPort | None
    execution_mode: str
    finalization_stage: AgentFinalizationStage
    tool_outputs: list[dict[str, Any]]
