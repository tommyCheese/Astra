from __future__ import annotations

from typing import Any

from app.application.permissions.effects import DefaultEffectAnalyzer, workspace_mount_mode
from app.application.planning.concurrency import (
    acquire_resource_claims,
    resource_claims_from_effect_plan,
)
from app.application.planning.coordinator import NodeContextSnapshot, NodeExecutionResult
from app.application.planning.node_runtime import (
    NodeRuntimeRunner,
    PreparedNodeTool,
    evidence_pack,
    observation_from_output,
)
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.types import NodeExecutionPhase, NodeExecutionStatus
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.executions import NodeExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import (
    AstraToolRegistry,
    ToolExecutionContext,
    ToolExecutionError,
)
from app.infrastructure.tools.router import ToolRouter
from app.infrastructure.tools.selection import CapabilityToolResolver


class ReadOnlyAgentNodeExecutor:
    def __init__(
        self,
        settings: AstraRuntimeSettings,
        *,
        model_client: ModelClient,
        tool_registry: AstraToolRegistry,
        runtime_runner: NodeRuntimeRunner | None = None,
    ):
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.runtime_runner = runtime_runner
        backends = {"in_process"}
        if settings.sandbox_enabled:
            backends.add("sandbox.remote")
        self.router = ToolRouter(tool_registry, available_backends=backends)
        self.resolver = CapabilityToolResolver(self.router)

    @property
    def safe_capabilities(self) -> set[str]:
        capabilities: set[str] = set()
        for name, spec in self.tool_registry.specs().items():
            if spec.side_effect_level != "read_only" or not spec.idempotent:
                continue
            capabilities.update({name, *spec.task_capabilities})
        return capabilities

    async def __call__(self, repository: RunUnitOfWork, context: NodeContextSnapshot) -> NodeExecutionResult:
        if self.runtime_runner is None:
            raise RuntimeError("Node runtime runner is not configured")
        return await self.runtime_runner(self, repository, context)

    async def invoke_tool(
        self, repository: Any, context: Any, runtime: Any, turn: Any, tool: Any, decision: Any, prepared_tool: Any
    ) -> None:
        call = await repository.start_tool_call(
            context.run_id,
            None,
            tool.spec.name,
            tool.spec.version,
            decision.tool_input,
            tool.spec.permission,
            tool.spec.side_effect_level,
            plan_node_id=context.plan_node_id,
            node_execution_id=context.execution_id,
        )
        executions = prepared_tool.executions
        execution = await executions.require(context.execution_id)
        await executions.transition(
            execution.id,
            expected_version=execution.state_version,
            phase=NodeExecutionPhase.running,
            checkpoint={
                "action_started": True,
                "idempotent": tool.spec.idempotent,
                "tool_call_id": call.id,
                "tool_name": tool.spec.name,
            },
        )
        try:
            output = await self._run_external_tool(repository, context, runtime, call, tool, decision, prepared_tool)
        except ToolExecutionError as error:
            await self._record_tool_failure(repository, executions, context, runtime, turn, call, tool, error)
            return
        await self._record_tool_success(repository, executions, context, runtime, turn, call, tool, output)

    @staticmethod
    async def _run_external_tool(repository, context, runtime, call, tool, decision, prepared_tool):
        effect_payload = prepared_tool.effect_plan.model_dump(mode="json")
        if context.active_skills:
            await repository.add_event(
                context.run_id,
                "skill.attributed_action",
                {
                    "tool_call_id": call.id,
                    "plan_node_id": context.plan_node_id,
                    "node_execution_id": context.execution_id,
                    "skills": list(context.active_skills),
                    "effect_plan": effect_payload,
                },
            )
        await repository.commit()
        execution_context = ToolExecutionContext(
            run_id=context.run_id,
            tool_call_id=call.id,
            step_id=context.plan_node_id,
            trace_id=f"{context.run_id}:{context.execution_id}:{call.id}",
            artifact_service=runtime.artifact_service,
            sandbox_service=runtime.sandbox_service,
            task_id=runtime.run.task_id,
            workspace_path=prepared_tool.workspace_path,
            workspace_mode=prepared_tool.mount_mode,
            effect_plan=effect_payload,
            skill_bindings=tuple(context.active_skills),
            skill_draft_test=context.skill_draft_test,
        )
        return await tool.run(decision.tool_input, context=execution_context)

    @staticmethod
    async def _record_tool_failure(repository, executions, context, runtime, turn, call, tool, error) -> None:
        await repository.finish_tool_call(call.id, error=error.to_payload())
        await executions.release_leases(context.execution_id, reason="tool_failed")
        observation = {
            "plan_node_id": context.plan_node_id,
            "node_execution_id": context.execution_id,
            "kind": "tool_result",
            "status": "failed",
            "summary": error.message,
            "data": {"tool_name": tool.spec.name},
            "error": error.to_payload(),
        }
        runtime.observations.append(observation)
        runtime.excluded_tools.add(tool.spec.name)
        await repository.update_agent_turn(
            turn.id,
            status="failed",
            observation=observation,
            phase="failed",
            tool_call_id=call.id,
        )

    @staticmethod
    async def _record_tool_success(repository, executions, context, runtime, turn, call, tool, output) -> None:
        await repository.finish_tool_call(call.id, output=output)
        runtime.tool_calls += 1
        runtime.evidence_refs.append(call.id)
        observation = observation_from_output(context.plan_node_id, context.execution_id, tool.spec.name, call.id, output)
        runtime.observations.append(observation)
        execution = await executions.require(context.execution_id)
        await executions.transition(
            execution.id,
            expected_version=execution.state_version,
            phase=NodeExecutionPhase.running,
            checkpoint={
                "action_started": True,
                "action_result": output,
                "idempotent": tool.spec.idempotent,
                "tool_call_id": call.id,
                "tool_name": tool.spec.name,
            },
        )
        await executions.release_leases(context.execution_id, reason="tool_completed")
        await repository.update_agent_turn(
            turn.id,
            status="completed",
            observation=observation,
            phase="committed",
            tool_call_id=call.id,
        )

    async def prepare_tool_execution(
        self, repository: Any, context: Any, runtime: Any, local_turn: Any, turn: Any, tool: Any, decision: Any
    ) -> PreparedNodeTool:
        effect_plan = DefaultEffectAnalyzer().analyze(tool.spec, decision.tool_input, task_id=runtime.run.task_id)
        mount_mode = workspace_mount_mode(effect_plan)
        workspace_path = await runtime.workspace_service.prepare(runtime.run.task_id) if mount_mode != "none" else None
        claims = resource_claims_from_effect_plan(effect_plan)
        executions = NodeExecutionRepository(repository.session)
        if any(claim.mode != "read" for claim in claims):
            result = await self._unsafe_tool_result(
                repository,
                context,
                turn.id,
                decision.tool_name,
                local_turn,
                runtime.tool_calls,
            )
            return PreparedNodeTool(effect_plan, mount_mode, workspace_path, executions, result)
        acquired = await acquire_resource_claims(
            executions,
            run_id=context.run_id,
            execution_id=context.execution_id,
            claims=claims,
        )
        if acquired:
            return PreparedNodeTool(effect_plan, mount_mode, workspace_path, executions)
        result = await self._waiting_resource_result(repository, executions, context, runtime, local_turn, claims)
        return PreparedNodeTool(effect_plan, mount_mode, workspace_path, executions, result)

    @staticmethod
    async def _waiting_resource_result(repository, executions, context, runtime, local_turn, claims) -> NodeExecutionResult:
        execution = await executions.require(context.execution_id)
        execution.worker_id = None
        await executions.transition(
            execution.id,
            expected_version=execution.state_version,
            phase=NodeExecutionPhase.waiting_resource,
            status=NodeExecutionStatus.waiting,
            wait_reason="resource_conflict",
        )
        resources = [claim.resource_summary for claim in claims]
        await repository.add_event(
            context.run_id,
            "plan.node.waiting_resource",
            {
                "node_execution_id": context.execution_id,
                "plan_id": context.plan_id,
                "plan_version": context.plan_version,
                "plan_node_id": context.plan_node_id,
                "attempt": context.attempt,
                "dispatch_batch_id": execution.dispatch_batch_id,
                "phase": NodeExecutionPhase.waiting_resource.value,
                "status": NodeExecutionStatus.waiting.value,
                "wait_reason": "resource_conflict",
                "resource_summaries": resources,
            },
        )
        return NodeExecutionResult(
            execution_id=context.execution_id,
            plan_node_id=context.plan_node_id,
            plan_version=context.plan_version,
            attempt=context.attempt,
            status=NodeExecutionStatus.waiting,
            evidence_refs=runtime.evidence_refs,
            observations=runtime.observations,
            budget_consumed={
                "turns": local_turn,
                "tool_calls": runtime.tool_calls,
                "model_calls": local_turn,
            },
            checkpoint={"wait_reason": "resource_conflict", "resource_summaries": resources},
        )

    async def select_tool(
        self,
        repository: Any,
        context: Any,
        runtime: Any,
        local_turn: Any,
        turn: Any,
        resolution: Any,
        allowed_tools: Any,
        decision: Any,
    ) -> Any:
        candidate = allowed_tools.get(decision.tool_name)
        if candidate is None:
            await self._reject_tool_selection(
                repository,
                context,
                runtime,
                turn,
                resolution,
                decision,
                "AstraTool is not in the current parallel-safe candidate set.",
            )
            return None
        try:
            tool = self.router.resolve(decision.tool_name, decision.tool_input)
        except ToolExecutionError as error:
            await self._reject_tool_selection(
                repository,
                context,
                runtime,
                turn,
                resolution,
                decision,
                error.message,
                error=error,
            )
            return None
        await repository.add_event(
            context.run_id,
            "tool.selection.accepted",
            {
                "turn_index": context.node["index"] * 1000 + local_turn,
                "node_execution_id": context.execution_id,
                "plan_node_id": context.plan_node_id,
                "tool_name": tool.spec.name,
                "candidate_names": list(resolution.candidate_names),
            },
        )
        return tool

    @staticmethod
    async def _reject_tool_selection(repository, context, runtime, turn, resolution, decision, summary, error=None) -> None:
        observation = {
            "kind": "tool_selection_rejected",
            "status": "failed",
            "summary": summary,
            "data": {
                "tool_name": decision.tool_name,
                "candidate_names": list(resolution.candidate_names),
            },
        }
        if error is not None:
            observation["error"] = error.to_payload()
        runtime.observations.append(observation)
        await repository.update_agent_turn(turn.id, status="failed", phase="failed", observation=observation)
        await repository.add_event(context.run_id, "tool.selection.rejected", observation)

    async def complete_node(
        self,
        repository: Any,
        context: Any,
        runtime: Any,
        local_turn: Any,
        turn: Any,
        resolution: Any,
        decision: Any,
        candidate_answer: Any,
    ) -> NodeExecutionResult | None:
        if resolution.unresolved_capabilities:
            observation = {
                "plan_node_id": context.plan_node_id,
                "node_execution_id": context.execution_id,
                "kind": "capability_requirements_unresolved",
                "status": "failed",
                "summary": "Parallel node still has unresolved task capabilities.",
                "data": {
                    "unresolved_capabilities": list(resolution.unresolved_capabilities),
                    "capability_gaps": list(resolution.capability_gaps),
                    "candidate_names": list(resolution.candidate_names),
                },
            }
            runtime.observations.append(observation)
            await repository.update_agent_turn(turn.id, status="failed", observation=observation, phase="failed")
            await repository.add_event(context.run_id, "reasoning.decision_rejected", observation)
            return None
        observation = {
            "plan_node_id": context.plan_node_id,
            "node_execution_id": context.execution_id,
            "kind": "node_result",
            "status": "succeeded",
            "summary": decision.reasoning_summary,
            "data": {"candidate_summary": candidate_answer.summary if candidate_answer else None},
        }
        await repository.update_agent_turn(turn.id, status="completed", observation=observation, phase="committed")
        return NodeExecutionResult(
            execution_id=context.execution_id,
            plan_node_id=context.plan_node_id,
            plan_version=context.plan_version,
            attempt=context.attempt,
            evidence_refs=list(dict.fromkeys(runtime.evidence_refs)),
            observations=[*runtime.observations, observation],
            budget_consumed={
                "turns": local_turn,
                "tool_calls": runtime.tool_calls,
                "model_calls": local_turn,
            },
            checkpoint={"node_result": observation, "idempotent": True},
        )

    async def prepare_turn(self, repository: Any, context: Any, runtime: Any, local_turn: Any) -> Any:
        resolution = self.resolver.resolve(
            context.node.get("required_capabilities") or [],
            observations=runtime.observations,
            plan_node_id=context.plan_node_id,
            require_read_only=True,
            require_idempotent=True,
            excluded_tools=runtime.excluded_tools,
        )
        allowed_tools = {
            candidate.tool_name: self.tool_registry.get(candidate.tool_name) for candidate in resolution.candidates
        }
        turn_index = context.node["index"] * 1000 + local_turn
        await repository.add_event(
            context.run_id,
            "reasoning.phase.started",
            {
                "phase": "selecting_action",
                "label": "正在并行执行计划节点",
                "turn_index": turn_index,
                "node_execution_id": context.execution_id,
                "plan_node_id": context.plan_node_id,
                "attempt": context.attempt,
            },
        )
        await repository.add_event(
            context.run_id,
            "tool.resolution.candidates",
            {
                "turn_index": turn_index,
                "node_execution_id": context.execution_id,
                **resolution.audit_payload(),
            },
        )
        await repository.commit()
        model_context = self._model_context(context, runtime, resolution, allowed_tools)
        decision, candidate_answer = await self.model_client.decide_with_answer(runtime.goal, model_context)
        await repository.add_event(
            context.run_id,
            "reasoning.summary.completed",
            {
                "turn_index": turn_index,
                "summary": decision.reasoning_summary[:4000],
                "node_execution_id": context.execution_id,
                "plan_node_id": context.plan_node_id,
                "attempt": context.attempt,
            },
        )
        await repository.commit()
        turn = await self._record_turn(repository, context, turn_index, decision)
        return resolution, allowed_tools, decision, candidate_answer, turn

    @staticmethod
    async def _record_turn(repository, context, turn_index, decision):
        return await repository.create_agent_turn(
            context.run_id,
            turn_index,
            decision.decision_type,
            decision.reasoning_summary,
            selected_tool=decision.tool_name,
            decision=decision.model_dump(mode="json"),
            state_version_before=context.state_version,
            plan_version=context.plan_version,
            phase="prepared",
            plan_node_id=context.plan_node_id,
            node_execution_id=context.execution_id,
        )

    @staticmethod
    def _model_context(context, runtime, resolution, allowed_tools):
        return {
            "run_id": context.run_id,
            "node_execution_id": context.execution_id,
            "goal": runtime.goal,
            "task_contract": context.task_contract,
            "active_plan_node": context.node,
            "active_node": context.node,
            "plan_version": context.plan_version,
            "attempt": context.attempt,
            "dependency_evidence": list(context.dependency_evidence),
            "accepted_facts": list(context.accepted_facts),
            "observations": runtime.observations,
            "tool_manifests": {name: tool.spec.model_dump(mode="json") for name, tool in allowed_tools.items()},
            "tool_selection": resolution.audit_payload(),
            "memory_reads": [],
            "evidence_pack": evidence_pack(runtime.observations),
            "skill_catalog": list(context.skill_catalog),
            "active_skills": list(context.active_skills),
        }

    @staticmethod
    async def _unsafe_tool_result(
        repository: RunUnitOfWork,
        context: NodeContextSnapshot,
        turn_id: str,
        tool_name: str,
        turns: int,
        tool_calls: int,
    ) -> NodeExecutionResult:
        await repository.update_agent_turn(
            turn_id,
            status="blocked",
            phase="blocked",
            observation={
                "kind": "parallel_safety_fallback",
                "status": "blocked",
                "summary": "AstraTool requires deterministic serial execution.",
                "data": {"tool_name": tool_name},
            },
        )
        return NodeExecutionResult(
            execution_id=context.execution_id,
            plan_node_id=context.plan_node_id,
            plan_version=context.plan_version,
            attempt=context.attempt,
            status=NodeExecutionStatus.blocked,
            budget_consumed={
                "turns": turns,
                "tool_calls": tool_calls,
                "model_calls": turns,
            },
            failure={
                "category": "requires_serial_execution",
                "tool_name": tool_name,
            },
        )
