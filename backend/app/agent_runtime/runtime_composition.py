"""Compose execution stages after infrastructure and policy are prepared."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.agent_runtime.approval import ApprovalRoutingStage
from app.agent_runtime.authorization import PermissionAuthorizationStage
from app.agent_runtime.completion import CompletionVerificationStage
from app.agent_runtime.completion_policy import CompletionGate
from app.agent_runtime.context import ContextAssembler
from app.agent_runtime.control_decisions import ControlDecisionStage
from app.agent_runtime.failure import ToolFailureStage
from app.agent_runtime.finalization import AgentFinalizationStage
from app.agent_runtime.invocation import ToolInvocationStage
from app.agent_runtime.loop_control import LoopOrchestrator, NoProgressDetector
from app.agent_runtime.memory_candidates import MemoryCandidateWriter
from app.agent_runtime.node_completion import NodeCompletionStage
from app.agent_runtime.observation import ObservationNormalizationStage
from app.agent_runtime.progress import ExecutionProgress, ProgressEvaluationStage
from app.agent_runtime.reasoning import ObservationEvaluator, ReflectionGate
from app.agent_runtime.result_adapters import ChartTaskAdapter, ProcessorRegistry, WebTaskAdapter
from app.agent_runtime.root_decision import RootDecisionStage
from app.agent_runtime.root_iteration import RootAgentIterationStage, RootRuntimeState
from app.agent_runtime.runtime_assembly import RootRuntimeAssembly, RuntimeLimits
from app.agent_runtime.skill_actions import SkillActionStage
from app.agent_runtime.tool_action import RootToolActionStage
from app.agent_runtime.turn_preparation import RootTurnPreparationStage
from app.core.config import Settings
from app.model_clients.contracts import ModelClient
from app.planning.scheduler import PlanScheduler
from app.repositories.plans import PlanRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.run_policy import EffectiveReasoningPolicy
from app.schemas.agent.types import AnswerMode
from app.skills.activation import SkillActivationService
from app.tools.base import ToolRegistry
from app.tools.router import ToolRouter


class RootRuntimeComposer:
    def __init__(
        self,
        *,
        settings: Settings,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        tool_router: ToolRouter,
        processors: ProcessorRegistry,
        evaluator: ObservationEvaluator,
        reflection_gate: ReflectionGate,
        completion_gate: CompletionGate,
        web_adapter: WebTaskAdapter,
        chart_adapter: ChartTaskAdapter,
        normalize_tool_output: Callable[[str, dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._settings = settings
        self._model_client = model_client
        self._tool_registry = tool_registry
        self._tool_router = tool_router
        self._processors = processors
        self._evaluator = evaluator
        self._reflection_gate = reflection_gate
        self._completion_gate = completion_gate
        self._web_adapter = web_adapter
        self._chart_adapter = chart_adapter
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
        limits: RuntimeLimits = values["limits"]
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
        )
        invocation = ToolInvocationStage(
            repository,
            permissions,
            infrastructure["workspace_repository"],
            infrastructure["workspace_service"],
            infrastructure["artifact_service"],
            infrastructure["sandbox_service"],
            activation,
        )
        return {
            "authorization": authorization,
            "approval": ApprovalRoutingStage(self._settings, repository),
            "invocation": invocation,
            "observation": ObservationNormalizationStage(
                self._settings,
                self._processors,
                self._normalize_tool_output,
            ),
            "failure": ToolFailureStage(
                repository,
                self._processors,
                progress,
                progress_stage,
            ),
            "no_progress": NoProgressDetector(),
            "transition_rules": LoopOrchestrator(),
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
        limits: RuntimeLimits = values["limits"]
        progress_stage = collaborators["progress_stage"]
        control = ControlDecisionStage(
            repository,
            plans,
            scheduler,
            progress,
            progress_stage,
            collaborators["no_progress"],
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
    ) -> RootToolActionStage:
        return RootToolActionStage(
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
            no_progress=collaborators["no_progress"],
            transition_validator=collaborators["transition_rules"],
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
        tool_stage: RootToolActionStage,
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
            assembler=ContextAssembler(
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
            web_adapter=self._web_adapter,
            chart_adapter=self._chart_adapter,
            memory_writer=collaborators["memory_writer"],
            verifier=CompletionVerificationStage(),
            completion_gate=self._completion_gate,
            progress_stage=collaborators["progress_stage"],
            workspace_service=infrastructure["workspace_service"],
            on_answer_delta=values["on_answer_delta"],
        )
