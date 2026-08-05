"""Compose execution stages after infrastructure and policy are prepared."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.application.agent_runtime.models import AgentRuntimeLimits, RootRuntimeAssembly
from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReflectionGate,
)
from app.application.agent_runtime.services.plugin_runtime import PluginRuntimeState
from app.application.agent_runtime.services.approval import ApprovalRoutingStage
from app.application.agent_runtime.services.authorization import PermissionAuthorizationStage
from app.application.agent_runtime.services.completion import CompletionVerificationStage
from app.application.agent_runtime.services.context import AgentContextAssembler
from app.application.agent_runtime.services.control_decisions import ControlDecisionStage
from app.application.agent_runtime.services.failure import ToolFailureStage
from app.application.agent_runtime.services.finalization import AgentFinalizationStage
from app.application.agent_runtime.services.invocation import ToolInvocationStage
from app.application.agent_runtime.services.memory_candidates import MemoryCandidateWriter
from app.application.agent_runtime.services.node_completion import NodeCompletionStage
from app.application.agent_runtime.services.observation import ObservationNormalizationStage
from app.application.agent_runtime.services.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.application.agent_runtime.services.root_decision import RootDecisionStage
from app.application.agent_runtime.services.root_iteration import (
    RootAgentIterationStage,
    RootRuntimeState,
)
from app.application.agent_runtime.services.skill_actions import SkillActionStage
from app.application.agent_runtime.services.tool_action import InvocationPipeline
from app.application.agent_runtime.services.turn_preparation import RootTurnPreparationStage
from app.application.planning.scheduler import PlanScheduler
from app.application.skills.activation import SkillActivationService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import EffectiveReasoningPolicy
from app.common.schemas.agent.types import AnswerMode
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.router import ToolRouter


class RootRuntimeComposer:
    def __init__(
        self,
        *,
        settings: AstraRuntimeSettings,
        model_client: ModelClient,
        tool_registry: AstraToolRegistry,
        tool_router: ToolRouter,
        plugin_runtime: PluginRuntimeState,
        evaluator: AgentObservationEvaluator,
        reflection_gate: AgentReflectionGate,
        completion_gate: AgentCompletionGate,
        normalize_tool_output: Callable[[str, dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._settings = settings
        self._model_client = model_client
        self._tool_registry = tool_registry
        self._tool_router = tool_router
        self._plugin_runtime = plugin_runtime
        self._evaluator = evaluator
        self._reflection_gate = reflection_gate
        self._completion_gate = completion_gate
        self._normalize_tool_output = normalize_tool_output

    def compose(self, values: dict[str, Any]) -> RootRuntimeAssembly:
        repository: RunUnitOfWork = values["repository"]
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
            values["infrastructure"],
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
        values: dict[str, Any],
        plans: PlanRepository,
        activation: SkillActivationService,
    ) -> dict[str, Any]:
        repository: RunUnitOfWork = values["repository"]
        progress: ExecutionProgress = values["progress"]
        policy: EffectiveReasoningPolicy = values["policy"]
        limits: AgentRuntimeLimits = values["limits"]
        progress_stage = ProgressEvaluationStage(
            run_id=values["run_id"],
            goal=values["goal"],
            repository=repository,
            plan_repository=plans,
            model_client=self._model_client,
            tool_registry=self._tool_registry,
            policy=policy,
            reflection_gate=self._reflection_gate,
            evaluator=self._evaluator,
            max_reflections=limits.max_reflections,
            progress=progress,
        )
        memory_writer = MemoryCandidateWriter(
            self._settings,
            repository,
            self._model_client,
        )
        stages = self._leaf_stages(values, activation, progress_stage)
        return {**stages, "progress_stage": progress_stage, "memory_writer": memory_writer}

    def _leaf_stages(
        self,
        values: dict[str, Any],
        activation: SkillActivationService,
        progress_stage: ProgressEvaluationStage,
    ) -> dict[str, Any]:
        repository: RunUnitOfWork = values["repository"]
        progress: ExecutionProgress = values["progress"]
        infrastructure = values["infrastructure"]
        permissions = infrastructure["permissions"]
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
            infrastructure["workspace_repository"],
            infrastructure["workspace_service"],
            infrastructure["artifact_service"],
            infrastructure["sandbox_service"],
            activation,
            self._plugin_runtime,
        )
        return {
            "authorization": authorization,
            "approval": ApprovalRoutingStage(
                self._settings,
                repository,
                self._plugin_runtime,
            ),
            "invocation": invocation,
            "observation": ObservationNormalizationStage(
                self._settings,
                self._plugin_runtime,
                self._normalize_tool_output,
            ),
            "failure": ToolFailureStage(
                repository,
                self._plugin_runtime,
                self._tool_registry,
                progress,
                progress_stage,
            ),
            "tool_outputs": [],
            "skill": SkillActionStage(repository, activation, self._model_client, progress),
        }

    def _assembly_result(
        self,
        values: dict[str, Any],
        collaborators: dict[str, Any],
        plans: PlanRepository,
        scheduler: PlanScheduler,
        infrastructure: dict[str, Any],
    ) -> RootRuntimeAssembly:
        repository: RunUnitOfWork = values["repository"]
        progress: ExecutionProgress = values["progress"]
        limits: AgentRuntimeLimits = values["limits"]
        progress_stage = collaborators["progress_stage"]
        control = ControlDecisionStage(
            repository,
            plans,
            scheduler,
            progress,
            progress_stage,
            max_replans=limits.max_replans,
            max_tool_calls=limits.max_tool_calls,
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
        collaborators: dict[str, Any],
    ) -> InvocationPipeline:
        return InvocationPipeline(
            repository=repository,
            tool_registry=self._tool_registry,
            authorization_stage=collaborators["authorization"],
            approval_stage=collaborators["approval"],
            invocation_stage=collaborators["invocation"],
            observation_stage=collaborators["observation"],
            failure_stage=collaborators["failure"],
            progress=progress,
            progress_stage=progress_stage,
            memory_writer=collaborators["memory_writer"],
            evaluator=self._evaluator,
            tool_outputs=collaborators["tool_outputs"],
        )

    def _compose_root(
        self,
        values: dict[str, Any],
        collaborators: dict[str, Any],
        plans: PlanRepository,
        scheduler: PlanScheduler,
        control: ControlDecisionStage,
        completion: NodeCompletionStage,
        tool_stage: InvocationPipeline,
        infrastructure: dict[str, Any],
    ) -> RootRuntimeAssembly:
        repository: RunUnitOfWork = values["repository"]
        run = values["run"]
        progress: ExecutionProgress = values["progress"]
        recovered = values["recovered"]
        state = RootRuntimeState(
            run=run,
            profile=values["profile"],
            quick_mode=values["profile"].answer_mode == AnswerMode.standard,
            approved_tool_call=recovered.approved_tool_call,
            approved_turn=recovered.approved_turn,
            approved_request_snapshot=recovered.approved_request_snapshot,
            terminal_status=recovered.terminal_status,
            terminal_summary=recovered.terminal_summary,
        )
        preparation = self._preparation(values, plans, scheduler, progress, state)
        decision = RootDecisionStage(
            repository=repository,
            model_client=self._model_client,
            progress=progress,
            progress_stage=collaborators["progress_stage"],
            skill_action_stage=collaborators["skill"],
            ensure_permission_runtime=values["permission_runtime"].ensure,
            answer_mode=values["profile"].answer_mode,
            quick_mode=state.quick_mode,
            on_answer_delta=values["on_answer_delta"],
        )
        iteration = RootAgentIterationStage(
            state=state,
            preparation_stage=preparation,
            decision_stage=decision,
            completion_stage=completion,
            control_stage=control,
            tool_stage=tool_stage,
            subagent_supervisor=values["supervisor"],
            execution_mode=values["policy"].execution_mode,
        )
        finalization = self._finalization(values, plans, collaborators, infrastructure)
        return RootRuntimeAssembly(
            run=run,
            initial_turn_count=len(values["loaded"].turns),
            profile=values["profile"],
            policy=values["policy"],
            limits=values["limits"],
            progress=progress,
            state=state,
            iteration_stage=iteration,
            finalization_stage=finalization,
            tool_outputs=collaborators["tool_outputs"],
        )

    def _preparation(
        self,
        values: dict[str, Any],
        plans: PlanRepository,
        scheduler: PlanScheduler,
        progress: ExecutionProgress,
        state: RootRuntimeState,
    ) -> RootTurnPreparationStage:
        repository: RunUnitOfWork = values["repository"]
        return RootTurnPreparationStage(
            repository=repository,
            plan_repository=plans,
            scheduler=scheduler,
            assembler=AgentContextAssembler(
                repository,
                skills_enabled=self._settings.skills_enabled,
                settings=self._settings,
            ),
            settings=self._settings,
            model_client=self._model_client,
            tool_registry=self._tool_registry,
            tool_router=self._tool_router,
            progress=progress,
            initial_run=values["run"],
            initial_skill_snapshot=values["initial_skill_snapshot"],
            fresh_run=values["fresh_run"],
            quick_mode=state.quick_mode,
            subagent_supervisor=values["supervisor"],
        )

    def _finalization(
        self,
        values: dict[str, Any],
        plans: PlanRepository,
        collaborators: dict[str, Any],
        infrastructure: dict[str, Any],
    ) -> AgentFinalizationStage:
        return AgentFinalizationStage(
            repository=values["repository"],
            plan_repository=plans,
            model_client=self._model_client,
            plugin_runtime=self._plugin_runtime,
            memory_writer=collaborators["memory_writer"],
            verifier=CompletionVerificationStage(),
            completion_gate=self._completion_gate,
            progress_stage=collaborators["progress_stage"],
            workspace_service=infrastructure["workspace_service"],
            on_answer_delta=values["on_answer_delta"],
        )
