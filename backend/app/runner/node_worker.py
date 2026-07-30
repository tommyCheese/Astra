from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.permissions.effects import DefaultEffectAnalyzer
from app.repositories.executions import NodeExecutionRepository
from app.repositories.runs import RunRepository
from app.runner.concurrency import (
    acquire_resource_claims,
    resource_claims_from_effect_plan,
)
from app.runner.coordinator import NodeContextSnapshot, NodeExecutionResult
from app.runner.model_client import ModelClient
from app.schemas.agent import AgentObservation, NodeExecutionPhase, NodeExecutionStatus
from app.tools.base import ToolExecutionContext, ToolExecutionError, ToolRegistry
from app.tools.router import ToolRouter
from app.tools.selection import CapabilityToolResolver


class ReadOnlyAgentNodeExecutor:
    """Executes one claimed node without scheduling or finalizing the Run."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
    ):
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
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

    async def __call__(
        self,
        repository: RunRepository,
        context: NodeContextSnapshot,
    ) -> NodeExecutionResult:
        run = await repository.require_run(context.run_id)
        goal = str(
            context.task_contract.get("original_goal")
            or run.model_policy.get("conversation_goal")
            or context.node["intent"]
        )
        dependency_refs = set(context.dependency_evidence)
        observations = [
            _observation_from_call(call)
            for call in run.tool_calls
            if call.id in dependency_refs and call.status == "succeeded"
        ]
        evidence_refs = list(context.dependency_evidence)
        excluded_tools: set[str] = set()
        maximum_turns = max(1, int(context.reserved_budgets.get("turns", 1)))
        maximum_tool_calls = max(
            0,
            int(context.reserved_budgets.get("tool_calls", 0)),
        )
        tool_calls = 0
        for local_turn in range(1, maximum_turns + 1):
            resolution = self.resolver.resolve(
                context.node.get("required_capabilities") or [],
                observations=observations,
                plan_node_id=context.plan_node_id,
                require_read_only=True,
                require_idempotent=True,
                excluded_tools=excluded_tools,
            )
            allowed_tools = {
                candidate.tool_name: self.tool_registry.get(candidate.tool_name)
                for candidate in resolution.candidates
            }
            await repository.add_event(
                context.run_id,
                "reasoning.phase.started",
                {
                    "phase": "selecting_action",
                    "label": "正在并行执行计划节点",
                    "turn_index": context.node["index"] * 1000 + local_turn,
                    "node_execution_id": context.execution_id,
                    "plan_node_id": context.plan_node_id,
                    "attempt": context.attempt,
                },
            )
            await repository.add_event(
                context.run_id,
                "tool.resolution.candidates",
                {
                    "turn_index": context.node["index"] * 1000 + local_turn,
                    "node_execution_id": context.execution_id,
                    **resolution.audit_payload(),
                },
            )
            await repository.session.commit()
            model_context = {
                "run_id": context.run_id,
                "node_execution_id": context.execution_id,
                "goal": goal,
                "task_contract": context.task_contract,
                "active_plan_node": context.node,
                "active_node": context.node,
                "plan_version": context.plan_version,
                "attempt": context.attempt,
                "dependency_evidence": list(context.dependency_evidence),
                "accepted_facts": list(context.accepted_facts),
                "observations": observations,
                "tool_manifests": {
                    name: tool.spec.model_dump(mode="json") for name, tool in allowed_tools.items()
                },
                "tool_selection": resolution.audit_payload(),
                "memory_reads": [],
                "evidence_pack": _evidence_pack(observations),
                "skill_catalog": list(context.skill_catalog),
                "active_skills": list(context.active_skills),
            }
            decision, candidate_answer = await self.model_client.decide_with_answer(
                goal,
                model_context,
            )
            await repository.add_event(
                context.run_id,
                "reasoning.summary.completed",
                {
                    "turn_index": context.node["index"] * 1000 + local_turn,
                    "summary": decision.reasoning_summary[:4000],
                    "node_execution_id": context.execution_id,
                    "plan_node_id": context.plan_node_id,
                    "attempt": context.attempt,
                },
            )
            await repository.session.commit()
            turn = await repository.create_agent_turn(
                context.run_id,
                context.node["index"] * 1000 + local_turn,
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
            if decision.decision_type in {"complete_node", "finalize"}:
                if resolution.unresolved_capabilities:
                    observation = {
                        "plan_node_id": context.plan_node_id,
                        "node_execution_id": context.execution_id,
                        "kind": "capability_requirements_unresolved",
                        "status": "failed",
                        "summary": ("Parallel node still has unresolved task capabilities."),
                        "data": {
                            "unresolved_capabilities": list(resolution.unresolved_capabilities),
                            "capability_gaps": list(resolution.capability_gaps),
                            "candidate_names": list(resolution.candidate_names),
                        },
                    }
                    observations.append(observation)
                    await repository.update_agent_turn(
                        turn.id,
                        status="failed",
                        observation=observation,
                        phase="failed",
                    )
                    await repository.add_event(
                        context.run_id,
                        "reasoning.decision_rejected",
                        observation,
                    )
                    continue
                observation = {
                    "plan_node_id": context.plan_node_id,
                    "node_execution_id": context.execution_id,
                    "kind": "node_result",
                    "status": "succeeded",
                    "summary": decision.reasoning_summary,
                    "data": {
                        "candidate_summary": candidate_answer.summary if candidate_answer else None
                    },
                }
                await repository.update_agent_turn(
                    turn.id,
                    status="completed",
                    observation=observation,
                    phase="committed",
                )
                return NodeExecutionResult(
                    execution_id=context.execution_id,
                    plan_node_id=context.plan_node_id,
                    plan_version=context.plan_version,
                    attempt=context.attempt,
                    evidence_refs=list(dict.fromkeys(evidence_refs)),
                    observations=[*observations, observation],
                    budget_consumed={
                        "turns": local_turn,
                        "tool_calls": tool_calls,
                        "model_calls": local_turn,
                    },
                    checkpoint={
                        "node_result": observation,
                        "idempotent": True,
                    },
                )
            if decision.decision_type != "call_tool" or not decision.tool_name:
                await repository.update_agent_turn(
                    turn.id,
                    status="failed",
                    phase="failed",
                    observation={
                        "kind": "decision_error",
                        "status": "failed",
                        "summary": "Parallel node Worker requires a tool call or node completion.",
                    },
                )
                continue
            if tool_calls >= maximum_tool_calls:
                return NodeExecutionResult(
                    execution_id=context.execution_id,
                    plan_node_id=context.plan_node_id,
                    plan_version=context.plan_version,
                    attempt=context.attempt,
                    status=NodeExecutionStatus.failed,
                    evidence_refs=evidence_refs,
                    observations=observations,
                    budget_consumed={
                        "turns": local_turn,
                        "tool_calls": tool_calls,
                        "model_calls": local_turn,
                    },
                    failure={"category": "node_tool_budget_exhausted"},
                )
            tool = allowed_tools.get(decision.tool_name)
            if tool is None:
                observation = {
                    "kind": "tool_selection_rejected",
                    "status": "failed",
                    "summary": "Tool is not in the current parallel-safe candidate set.",
                    "data": {
                        "tool_name": decision.tool_name,
                        "candidate_names": list(resolution.candidate_names),
                    },
                }
                observations.append(observation)
                await repository.update_agent_turn(
                    turn.id,
                    status="failed",
                    phase="failed",
                    observation=observation,
                )
                await repository.add_event(
                    context.run_id,
                    "tool.selection.rejected",
                    observation,
                )
                continue
            try:
                tool = self.router.resolve(decision.tool_name, decision.tool_input)
            except ToolExecutionError as exc:
                observation = {
                    "kind": "tool_selection_rejected",
                    "status": "failed",
                    "summary": exc.message,
                    "data": {
                        "tool_name": decision.tool_name,
                        "candidate_names": list(resolution.candidate_names),
                    },
                    "error": exc.to_payload(),
                }
                observations.append(observation)
                await repository.update_agent_turn(
                    turn.id,
                    status="failed",
                    phase="failed",
                    observation=observation,
                )
                await repository.add_event(
                    context.run_id,
                    "tool.selection.rejected",
                    observation,
                )
                continue
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
            effect_plan = DefaultEffectAnalyzer().analyze(
                tool.spec,
                decision.tool_input,
                task_id=run.task_id,
            )
            claims = resource_claims_from_effect_plan(effect_plan)
            if any(claim.mode != "read" for claim in claims):
                return await self._unsafe_tool_result(
                    repository,
                    context,
                    turn.id,
                    decision.tool_name,
                    local_turn,
                    tool_calls,
                )
            executions = NodeExecutionRepository(repository.session)
            acquired = await acquire_resource_claims(
                executions,
                run_id=context.run_id,
                execution_id=context.execution_id,
                claims=claims,
            )
            if not acquired:
                execution = await executions.require(context.execution_id)
                execution.worker_id = None
                await executions.transition(
                    execution.id,
                    expected_version=execution.state_version,
                    phase=NodeExecutionPhase.waiting_resource,
                    status=NodeExecutionStatus.waiting,
                    wait_reason="resource_conflict",
                )
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
                        "resource_summaries": [claim.resource_summary for claim in claims],
                    },
                )
                return NodeExecutionResult(
                    execution_id=context.execution_id,
                    plan_node_id=context.plan_node_id,
                    plan_version=context.plan_version,
                    attempt=context.attempt,
                    status=NodeExecutionStatus.waiting,
                    evidence_refs=evidence_refs,
                    observations=observations,
                    budget_consumed={
                        "turns": local_turn,
                        "tool_calls": tool_calls,
                        "model_calls": local_turn,
                    },
                    checkpoint={
                        "wait_reason": "resource_conflict",
                        "resource_summaries": [claim.resource_summary for claim in claims],
                    },
                )
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
            execution = await executions.require(context.execution_id)
            execution = await executions.transition(
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
                if context.active_skills:
                    await repository.add_event(
                        context.run_id,
                        "skill.attributed_action",
                        {
                            "tool_call_id": call.id,
                            "plan_node_id": context.plan_node_id,
                            "node_execution_id": context.execution_id,
                            "skills": list(context.active_skills),
                            "effect_plan": effect_plan.model_dump(mode="json"),
                        },
                    )
                output = await tool.run(
                    decision.tool_input,
                    context=ToolExecutionContext(
                        run_id=context.run_id,
                        tool_call_id=call.id,
                        step_id=context.plan_node_id,
                        trace_id=f"{context.run_id}:{context.execution_id}:{call.id}",
                        artifact_service=None,
                        sandbox_service=None,
                        task_id=run.task_id,
                        effect_plan=effect_plan.model_dump(mode="json"),
                        skill_bindings=tuple(context.active_skills),
                        skill_draft_test=context.skill_draft_test,
                    ),
                )
            except ToolExecutionError as exc:
                await repository.finish_tool_call(call.id, error=exc.to_payload())
                await executions.release_leases(
                    context.execution_id,
                    reason="tool_failed",
                )
                observation = {
                    "plan_node_id": context.plan_node_id,
                    "node_execution_id": context.execution_id,
                    "kind": "tool_result",
                    "status": "failed",
                    "summary": exc.message,
                    "data": {"tool_name": tool.spec.name},
                    "error": exc.to_payload(),
                }
                observations.append(observation)
                excluded_tools.add(tool.spec.name)
                await repository.update_agent_turn(
                    turn.id,
                    status="failed",
                    observation=observation,
                    phase="failed",
                    tool_call_id=call.id,
                )
                continue
            await repository.finish_tool_call(call.id, output=output)
            tool_calls += 1
            evidence_refs.append(call.id)
            observation = _observation_from_output(
                context.plan_node_id,
                context.execution_id,
                tool.spec.name,
                call.id,
                output,
            )
            observations.append(observation)
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
            await executions.release_leases(
                context.execution_id,
                reason="tool_completed",
            )
            await repository.update_agent_turn(
                turn.id,
                status="completed",
                observation=observation,
                phase="committed",
                tool_call_id=call.id,
            )
        return NodeExecutionResult(
            execution_id=context.execution_id,
            plan_node_id=context.plan_node_id,
            plan_version=context.plan_version,
            attempt=context.attempt,
            status=NodeExecutionStatus.failed,
            evidence_refs=evidence_refs,
            observations=observations,
            budget_consumed={
                "turns": maximum_turns,
                "tool_calls": tool_calls,
                "model_calls": maximum_turns,
            },
            failure={"category": "node_turn_budget_exhausted"},
        )

    @staticmethod
    async def _unsafe_tool_result(
        repository: RunRepository,
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
                "summary": "Tool requires deterministic serial execution.",
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


def _observation_from_call(call) -> dict[str, Any]:
    return _observation_from_output(
        call.plan_node_id,
        call.node_execution_id,
        call.tool_name,
        call.id,
        dict(call.output or {}),
    )


def _observation_from_output(
    plan_node_id: str | None,
    execution_id: str | None,
    tool_name: str,
    tool_call_id: str,
    output: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(output.get("data") or output)
    return AgentObservation(
        plan_node_id=plan_node_id,
        kind="tool_result",
        status="succeeded",
        summary=f"{tool_name} completed",
        data={
            "tool_name": tool_name,
            **payload,
            "tool_call_id": tool_call_id,
            "node_execution_id": execution_id,
        },
    ).model_dump(mode="json")


def _evidence_pack(observations: list[dict[str, Any]]) -> dict[str, Any]:
    fetched_sources = [
        observation.get("data", {})
        for observation in observations
        if observation.get("kind") == "tool_result"
        and observation.get("data", {}).get("tool_name") == "web_fetch"
    ]
    return {"fetched_sources": fetched_sources}
