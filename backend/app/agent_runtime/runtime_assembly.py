"""Typed result of composing the root-agent runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent_runtime.finalization import AgentFinalizationStage
from app.agent_runtime.progress import ExecutionProgress
from app.agent_runtime.root_iteration import RootAgentIterationStage, RootRuntimeState
from app.db.models.runs import RunRecord
from app.schemas.agent.run_policy import EffectiveReasoningPolicy, RunExecutionProfile


@dataclass(frozen=True)
class RuntimeLimits:
    max_turns: int
    max_tool_calls: int | None
    max_reflections: int
    max_replans: int


@dataclass(frozen=True)
class RootRuntimeAssembly:
    run: RunRecord
    initial_turn_count: int
    profile: RunExecutionProfile
    policy: EffectiveReasoningPolicy
    limits: RuntimeLimits
    progress: ExecutionProgress
    state: RootRuntimeState
    iteration_stage: RootAgentIterationStage
    finalization_stage: AgentFinalizationStage
    tool_outputs: list[dict[str, Any]]
