"""Compose execution stages after infrastructure and policy are prepared."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReflectionGate,
)
from app.application.agent_runtime.services.completion.finalization import AgentFinalizationStage
from app.application.agent_runtime.services.completion.memory_candidates import (
    MemoryCandidateWriter,
)
from app.application.agent_runtime.services.completion.node_completion import NodeCompletionStage
from app.application.agent_runtime.services.completion.verification import verify_completion
from app.application.agent_runtime.services.context.assembler import assemble_agent_context
from app.application.agent_runtime.services.context.turn_preparation import RootTurnPreparationStage
from app.application.agent_runtime.services.decisions.control import ControlDecisionStage
from app.application.agent_runtime.services.decisions.root import RootDecisionStage
from app.application.agent_runtime.services.decisions.skills import SkillActionStage
from app.application.agent_runtime.services.execution.tool_action import InvocationPipeline
from app.application.agent_runtime.services.shared.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.application.agent_runtime.services.tooling.approval import ApprovalRoutingStage
from app.application.agent_runtime.services.tooling.authorization import (
    PermissionAuthorizationStage,
)
from app.application.agent_runtime.services.tooling.failure import ToolFailureStage
from app.application.agent_runtime.services.tooling.invocation import ToolInvocationStage
from app.application.agent_runtime.services.tooling.observation import ObservationNormalizationStage
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.planning.scheduler import PlanScheduler
from app.application.skills.activation import SkillActivationService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import EffectiveReasoningPolicy, RunExecutionProfile
from app.domain.execution.contracts import SubagentSupervisorPort
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.router import ToolRouter


@dataclass
class _TrustedRuntimeState:
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
    state: _TrustedRuntimeState
    preparation_stage: RootTurnPreparationStage
    decision_stage: RootDecisionStage
    completion_stage: NodeCompletionStage
    control_stage: ControlDecisionStage
    tool_stage: InvocationPipeline
    subagent_supervisor: SubagentSupervisorPort | None
    execution_mode: str
    finalization_stage: AgentFinalizationStage
    tool_outputs: list[dict[str, Any]]


@dataclass(frozen=True)
class RuntimeInfrastructure:
    permissions: Any
    artifact_service: Any
    workspace_repository: Any
    workspace_service: Any
    sandbox_service: Any
    memory_service: Any


@dataclass(frozen=True)
class RuntimeBuildValues:
    repository: RunUnitOfWork
    run_id: str
    goal: str
    on_answer_delta: Callable[[str], Awaitable[None]] | None
    run: Any
    initial_turn_count: int
    profile: Any
    policy: EffectiveReasoningPolicy
    limits: tuple[int, int | None, int, int]
    progress: ExecutionProgress
    approved_call: Any
    approved_turn: Any
    approved_snapshot: dict[str, Any] | None
    terminal: tuple[str | None, str | None]
    permission_runtime: Any
    infrastructure: RuntimeInfrastructure
    supervisor: Any


@dataclass(frozen=True)
class RuntimeCollaborators:
    authorization: PermissionAuthorizationStage
    approval: ApprovalRoutingStage
    invocation: ToolInvocationStage
    observation: ObservationNormalizationStage
    failure: ToolFailureStage
    tool_outputs: list[dict[str, Any]]
    skill: SkillActionStage
    progress_stage: ProgressEvaluationStage
    memory_writer: MemoryCandidateWriter


@dataclass
class TrustedCapabilityFactory:
    _settings: AstraRuntimeSettings
    _model_client: ModelClient
    _tool_registry: AstraToolRegistry
    _tool_router: ToolRouter
    _plugin_runtime: PluginRuntimeState
    _evaluator: AgentObservationEvaluator
    _reflection_gate: AgentReflectionGate
    _completion_gate: AgentCompletionGate
    _normalize_tool_output: Callable[[str, dict[str, Any]], dict[str, Any]]

    def compose(self, values: RuntimeBuildValues) -> TrustedRuntime:
        repository: RunUnitOfWork = values.repository
        activation = SkillActivationService(
            repository.session,
            max_active=self._settings.skills_max_active,
            max_resource_bytes=self._settings.skills_max_resource_bytes_per_run,
        )
        plans = PlanRepository(repository.session)
        scheduler = self._scheduler(plans)
        collaborators = self._execution_collaborators(
            values,
            plans,
            activation,
        )
        return self._assembly_result(
            values,
            collaborators,
            plans,
            scheduler,
            values.infrastructure,
        )

    def _scheduler(self, plans: PlanRepository) -> PlanScheduler:
        return PlanScheduler(
            plans,
            server_max_parallel_nodes=self._settings.agent_max_parallel_nodes,
            parallel_execution_enabled=self._settings.agent_parallel_execution_enabled,
            provider_concurrency_limit=self._settings.agent_provider_concurrency_limit,
            capability_concurrency_limit=self._settings.agent_capability_concurrency_limit,
        )

    def _execution_collaborators(
        self,
        values: RuntimeBuildValues,
        plans: PlanRepository,
        activation: SkillActivationService,
    ) -> RuntimeCollaborators:
        repository: RunUnitOfWork = values.repository
        progress: ExecutionProgress = values.progress
        policy: EffectiveReasoningPolicy = values.policy
        _, _, max_reflections, _ = values.limits
        progress_stage = ProgressEvaluationStage(
            _run_id=values.run_id,
            _goal=values.goal,
            _repository=repository,
            _plan_repository=plans,
            _model_client=self._model_client,
            _tool_registry=self._tool_registry,
            _policy=policy,
            _reflection_gate=self._reflection_gate,
            _evaluator=self._evaluator,
            _max_reflections=max_reflections,
            progress=progress,
        )
        memory_writer = MemoryCandidateWriter(
            self._settings,
            repository,
            self._model_client,
        )
        return self._leaf_stages(values, activation, progress_stage, memory_writer)

    def _leaf_stages(
        self,
        values: RuntimeBuildValues,
        activation: SkillActivationService,
        progress_stage: ProgressEvaluationStage,
        memory_writer: MemoryCandidateWriter,
    ) -> RuntimeCollaborators:
        repository: RunUnitOfWork = values.repository
        progress: ExecutionProgress = values.progress
        infrastructure = values.infrastructure
        permissions = infrastructure.permissions
        authorization = PermissionAuthorizationStage(
            self._settings,
            repository,
            permissions,
            self._tool_router,
            self._plugin_runtime,
        )
        invocation = ToolInvocationStage(
            repository,
            permissions,
            infrastructure.workspace_repository,
            infrastructure.workspace_service,
            infrastructure.artifact_service,
            infrastructure.sandbox_service,
            activation,
            self._plugin_runtime,
            infrastructure.memory_service,
        )
        return RuntimeCollaborators(
            authorization=authorization,
            approval=ApprovalRoutingStage(
                self._settings,
                repository,
                self._plugin_runtime,
            ),
            invocation=invocation,
            observation=ObservationNormalizationStage(
                self._settings,
                self._plugin_runtime,
                self._normalize_tool_output,
            ),
            failure=ToolFailureStage(
                repository,
                self._plugin_runtime,
                self._tool_registry,
                progress,
                progress_stage,
            ),
            tool_outputs=[],
            skill=SkillActionStage(repository, activation, self._model_client, progress),
            progress_stage=progress_stage,
            memory_writer=memory_writer,
        )

    def _assembly_result(
        self,
        values: RuntimeBuildValues,
        collaborators: RuntimeCollaborators,
        plans: PlanRepository,
        scheduler: PlanScheduler,
        infrastructure: RuntimeInfrastructure,
    ) -> TrustedRuntime:
        repository: RunUnitOfWork = values.repository
        progress: ExecutionProgress = values.progress
        _, max_tool_calls, _, max_replans = values.limits
        progress_stage = collaborators.progress_stage
        control = ControlDecisionStage(
            repository,
            plans,
            scheduler,
            progress,
            progress_stage,
            _max_replans=max_replans,
            _max_tool_calls=max_tool_calls,
        )
        completion = NodeCompletionStage(repository, plans, progress, progress_stage)
        tool_stage = self._tool_stage(
            repository,
            progress,
            progress_stage,
            collaborators,
        )
        return self._compose_root(
            values,
            collaborators,
            plans,
            scheduler,
            control,
            completion,
            tool_stage,
            infrastructure,
        )

    def _tool_stage(
        self,
        repository: RunUnitOfWork,
        progress: ExecutionProgress,
        progress_stage: ProgressEvaluationStage,
        collaborators: RuntimeCollaborators,
    ) -> InvocationPipeline:
        return InvocationPipeline(
            _repository=repository,
            _tool_registry=self._tool_registry,
            _authorization=collaborators.authorization,
            _approval=collaborators.approval,
            _invocation=collaborators.invocation,
            _observation=collaborators.observation,
            _failure=collaborators.failure,
            _progress=progress,
            _progress_stage=progress_stage,
            _memory_writer=collaborators.memory_writer,
            _evaluator=self._evaluator,
            _tool_outputs=collaborators.tool_outputs,
        )

    def _compose_root(
        self,
        values: RuntimeBuildValues,
        collaborators: RuntimeCollaborators,
        plans: PlanRepository,
        scheduler: PlanScheduler,
        control: ControlDecisionStage,
        completion: NodeCompletionStage,
        tool_stage: InvocationPipeline,
        infrastructure: RuntimeInfrastructure,
    ) -> TrustedRuntime:
        repository: RunUnitOfWork = values.repository
        run = values.run
        progress: ExecutionProgress = values.progress
        terminal_status, terminal_summary = values.terminal
        max_turns, max_tool_calls, max_reflections, max_replans = values.limits
        state = _TrustedRuntimeState(
            run=run,
            profile=values.profile,
            approved_tool_call=values.approved_call,
            approved_turn=values.approved_turn,
            approved_request_snapshot=values.approved_snapshot,
            terminal_status=terminal_status,
            terminal_summary=terminal_summary,
        )
        preparation = self._preparation(values, plans, scheduler, progress, state)
        decision = RootDecisionStage(
            _repository=repository,
            _model_client=self._model_client,
            _progress=progress,
            _progress_stage=collaborators.progress_stage,
            _skills=collaborators.skill,
            _ensure_permissions=values.permission_runtime.ensure,
            _answer_mode=values.profile.answer_mode,
            _on_answer_delta=values.on_answer_delta,
        )
        finalization = self._finalization(values, plans, collaborators, infrastructure)
        return TrustedRuntime(
            run=run,
            initial_turn_count=values.initial_turn_count,
            profile=values.profile,
            policy=values.policy,
            max_turns=max_turns,
            max_tool_calls=max_tool_calls,
            max_reflections=max_reflections,
            max_replans=max_replans,
            progress=progress,
            state=state,
            preparation_stage=preparation,
            decision_stage=decision,
            completion_stage=completion,
            control_stage=control,
            tool_stage=tool_stage,
            subagent_supervisor=values.supervisor,
            execution_mode=values.policy.execution_mode,
            finalization_stage=finalization,
            tool_outputs=collaborators.tool_outputs,
        )

    def _preparation(
        self,
        values: RuntimeBuildValues,
        plans: PlanRepository,
        scheduler: PlanScheduler,
        progress: ExecutionProgress,
        state: _TrustedRuntimeState,
    ) -> RootTurnPreparationStage:
        repository: RunUnitOfWork = values.repository
        return RootTurnPreparationStage(
            _repository=repository,
            _plans=plans,
            _scheduler=scheduler,
            _assembler=partial(
                assemble_agent_context,
                repository,
                skills_enabled=self._settings.skills_enabled,
                settings=self._settings,
            ),
            _settings=self._settings,
            _model_client=self._model_client,
            _tool_registry=self._tool_registry,
            _tool_router=self._tool_router,
            _progress=progress,
            _subagents=values.supervisor,
        )

    def _finalization(
        self,
        values: RuntimeBuildValues,
        plans: PlanRepository,
        collaborators: RuntimeCollaborators,
        infrastructure: RuntimeInfrastructure,
    ) -> AgentFinalizationStage:
        return AgentFinalizationStage(
            _repository=values.repository,
            _plan_repository=plans,
            _model_client=self._model_client,
            _plugin_runtime=self._plugin_runtime,
            _memory_writer=collaborators.memory_writer,
            _verifier=verify_completion,
            _completion_gate=self._completion_gate,
            _progress_stage=collaborators.progress_stage,
            _workspace_service=infrastructure.workspace_service,
            _on_answer_delta=values.on_answer_delta,
        )
