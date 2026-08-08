"""Readable staged loop for one delegated Agent execution."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from app.application.context_compaction.child import compact_child_context
from app.application.subagents.executor_contracts import AgentExecutorRuntime
from app.application.subagents.run_session import (
    ChildRunSessionBuilder,
    ChildRunState,
    PreparedChildTurn,
)
from app.common.schemas.agent.types import NodeExecutionPhase, NodeExecutionStatus, PlanNodeStatus
from app.common.schemas.subagents import (
    DelegationContract,
    SubagentContextManifest,
    SubagentEvidenceReference,
    SubagentExecutionStatus,
    SubagentQuestion,
    SubagentResult,
)
from app.infrastructure.repositories.executions import NodeExecutionRepository

if TYPE_CHECKING:
    from app.application.subagents.executor import LocalAstraAgentExecutor


class ChildAgentRun:
    """Execute child turns while delegating persistence primitives to executor services."""

    def __init__(
        self,
        *,
        services: LocalAstraAgentExecutor,
        contract: DelegationContract,
        context_manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        self._services = services
        self._contract = contract
        self._manifest = context_manifest
        self._runtime = runtime
        self._checkpoint = checkpoint
        self._session_builder = ChildRunSessionBuilder(
            services=services,
            contract=contract,
            context_manifest=context_manifest,
            runtime=runtime,
            checkpoint=checkpoint,
        )

    async def execute(self) -> SubagentResult:
        state = await self._session_builder.initialize()
        max_calls = self._contract.request.budget.max_model_calls
        while state.usage["model_calls"] < max_calls:
            prepared = await self._session_builder.prepare_turn(state)
            result = await self._route_decision(state, prepared)
            if result is not None:
                return result
        return await self._budget_exhausted(state)

    async def _route_decision(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> SubagentResult | None:
        decision_type = prepared.decision.decision_type
        if decision_type == "waiting_parent":
            return await self._wait_for_parent(state, prepared)
        if decision_type == "waiting_resource":
            return await self._wait_for_resource(state, prepared)
        if decision_type in {"blocked", "fail"}:
            return await self._stop_failed(state, prepared)
        if decision_type == "call_tool" and prepared.decision.tool_name:
            return await self._call_tool(state, prepared)
        if decision_type == "complete_node" and prepared.active_node is not None:
            await self._complete_node(state, prepared)
            return None
        if decision_type in {"finalize", "complete"}:
            return await self._finalize(state, prepared)
        await self._reject_decision(state, prepared)
        return None

    async def _wait_for_parent(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> SubagentResult:
        raw = prepared.decision.node_result.get("question") or prepared.decision.tool_input
        continuation = self._runtime.continuation_service
        question = (
            continuation.question(
                checkpoint=state.context_checkpoint,
                prompt=str(raw.get("prompt", "Parent input is required.")),
                required_fields=list(raw.get("required_fields", [])),
            )
            if continuation is not None
            else SubagentQuestion.model_validate(raw)
        )
        result = SubagentResult(
            status=SubagentExecutionStatus.waiting_parent,
            summary=prepared.decision.reasoning_summary,
            question=question,
            usage=state.usage,
            provenance=self._services._provenance(state.execution, self._contract),
        )
        await state.repository.update_agent_turn(
            prepared.turn.id,
            status="waiting",
            phase="waiting_parent",
        )
        await self._checkpoint_waiting_parent(state, prepared, question)
        await self._services._transition_waiting(
            state.executions,
            state.execution,
            result,
            "waiting_parent",
            "parent_input",
        )
        await self._runtime.session.commit()
        return result

    async def _checkpoint_waiting_parent(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
        question: SubagentQuestion,
    ) -> None:
        current = await state.executions.require(state.execution.id)
        current.checkpoint = {
            **(current.checkpoint or {}),
            "observations": deepcopy(state.observations),
            "context_checkpoint": state.context_checkpoint.model_dump(mode="json"),
        }
        current.budget_usage = deepcopy(state.usage)
        await self._runtime.session.flush()
        if prepared.node_execution is None:
            return
        nodes = NodeExecutionRepository(self._runtime.session)
        node = await nodes.require(prepared.node_execution.id)
        await nodes.transition(
            node.id,
            expected_version=node.state_version,
            phase=NodeExecutionPhase.result_unknown,
            status=NodeExecutionStatus.waiting,
            wait_reason="parent_input",
            checkpoint={"question": question.model_dump(mode="json")},
        )

    async def _wait_for_resource(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> SubagentResult:
        reason = str(prepared.decision.node_result.get("reason") or "resource_conflict")
        result = SubagentResult(
            status=SubagentExecutionStatus.waiting_resource,
            summary=prepared.decision.reasoning_summary,
            open_issues=[reason],
            usage=state.usage,
            provenance=self._services._provenance(state.execution, self._contract),
        )
        await state.repository.update_agent_turn(
            prepared.turn.id,
            status="waiting",
            phase="waiting_resource",
        )
        if prepared.node_execution is not None:
            nodes = NodeExecutionRepository(self._runtime.session)
            node = await nodes.require(prepared.node_execution.id)
            await nodes.transition(
                node.id,
                expected_version=node.state_version,
                phase=NodeExecutionPhase.waiting_resource,
                status=NodeExecutionStatus.waiting,
                wait_reason=reason,
            )
        await self._services._transition_waiting(
            state.executions,
            state.execution,
            result,
            "waiting_resource",
            reason,
        )
        await self._runtime.session.commit()
        return result

    async def _stop_failed(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> SubagentResult:
        status = (
            SubagentExecutionStatus.blocked
            if prepared.decision.decision_type == "blocked"
            else SubagentExecutionStatus.failed
        )
        result = SubagentResult(
            status=status,
            summary=prepared.decision.reasoning_summary,
            open_issues=list(prepared.decision.node_result.get("open_issues", [])),
            usage=state.usage,
            provenance=self._services._provenance(state.execution, self._contract),
        )
        await self._finish(state, prepared, result)
        return result

    async def _call_tool(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> SubagentResult | None:
        outcome = await self._services._call_tool(
            repo=state.repository,
            executions=state.executions,
            execution=state.execution,
            runtime=self._runtime,
            contract=self._contract,
            turn_id=prepared.turn.id,
            plan_node_id=prepared.active_node.id if prepared.active_node else None,
            node_execution_id=(prepared.node_execution.id if prepared.node_execution else None),
            tool_name=prepared.decision.tool_name,
            tool_input=prepared.decision.tool_input,
            usage=state.usage,
        )
        if isinstance(outcome, SubagentResult):
            await self._runtime.session.commit()
            return outcome
        await self._record_tool_outcome(state, prepared, outcome)
        return None

    async def _record_tool_outcome(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
        outcome: dict[str, Any],
    ) -> None:
        state.observations.append(outcome)
        for item in outcome.get("artifacts", []):
            try:
                state.artifact_refs.append(self._services._artifact_reference(item))
            except ValueError:
                continue
        state.evidence_refs.extend(
            SubagentEvidenceReference(id=str(ref), summary="Child tool evidence")
            for ref in outcome.get("evidence_refs", [])
        )
        succeeded = outcome["status"] == "succeeded"
        await state.repository.update_agent_turn(
            prepared.turn.id,
            status="completed" if succeeded else "failed",
            phase="committed" if succeeded else "failed",
            observation=outcome,
            tool_call_id=outcome.get("tool_call_id"),
        )
        if not succeeded:
            reflection = await self._services.model_client.reflect(
                self._contract.request.objective,
                {**prepared.model_context, "latest_observation": outcome},
            )
            state.usage["model_calls"] += 1
            await state.repository.update_agent_turn(
                prepared.turn.id,
                reflection=reflection.model_dump(mode="json"),
                phase="reflected",
            )
        state.execution, state.context_checkpoint, state.observations = await compact_child_context(
            session=self._runtime.session,
            settings=self._services.settings,
            model_client=self._services.model_client,
            execution=state.execution,
            contract=self._contract,
            manifest=self._manifest,
            plan=self._services._plan_context(state.plan),
            usage=state.usage,
            observations=state.observations,
            checkpoint=state.context_checkpoint,
        )
        state.execution = await self._services._checkpoint(
            state.executions,
            state.execution,
            self._runtime,
            state.usage,
            state.observations,
            state.plan.id,
            state.context_checkpoint,
        )
        await self._runtime.session.commit()

    async def _complete_node(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> None:
        node = prepared.active_node
        assert node is not None
        if node.status == PlanNodeStatus.pending.value:
            await state.plans.transition_node(node.id, PlanNodeStatus.running)
        await state.plans.transition_node(
            node.id,
            PlanNodeStatus.completed,
            evidence_refs=[item.id for item in state.evidence_refs],
        )
        if prepared.node_execution is not None:
            nodes = NodeExecutionRepository(self._runtime.session)
            current = await nodes.require(prepared.node_execution.id)
            await nodes.transition(
                current.id,
                expected_version=current.state_version,
                phase=NodeExecutionPhase.completed,
                status=NodeExecutionStatus.completed,
                result={"summary": prepared.decision.reasoning_summary},
            )
        await state.repository.update_agent_turn(
            prepared.turn.id,
            status="completed",
            phase="committed",
        )
        await self._runtime.session.commit()

    async def _finalize(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> SubagentResult:
        result = self._services._result_from_decision(
            prepared.decision.node_result,
            summary=prepared.decision.reasoning_summary,
            execution=state.execution,
            contract=self._contract,
            usage=state.usage,
            artifacts=state.artifact_refs,
            evidence=state.evidence_refs,
        )
        await self._complete_active_node_if_successful(state, prepared, result)
        await self._finish(state, prepared, result)
        return result

    async def _complete_active_node_if_successful(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
        result: SubagentResult,
    ) -> None:
        if prepared.active_node is None or result.status not in {
            SubagentExecutionStatus.completed,
            SubagentExecutionStatus.completed_with_warnings,
        }:
            return
        node = await state.plans.require_node(prepared.active_node.id)
        if node.status == PlanNodeStatus.pending.value:
            node = await state.plans.transition_node(node.id, PlanNodeStatus.running)
        if node.status == PlanNodeStatus.running.value:
            await state.plans.transition_node(
                node.id,
                PlanNodeStatus.completed,
                evidence_refs=[item.id for item in state.evidence_refs],
            )

    async def _finish(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
        result: SubagentResult,
    ) -> None:
        await state.repository.update_agent_turn(
            prepared.turn.id,
            status="completed",
            phase="terminal",
        )
        await self._services._finish_node_execution(
            self._runtime.session,
            prepared.node_execution,
            result,
        )
        await self._services._terminal(state.executions, state.execution, result)
        await self._services._settle_budget(self._runtime, state.execution.id, result)
        await self._runtime.session.commit()

    async def _reject_decision(
        self,
        state: ChildRunState,
        prepared: PreparedChildTurn,
    ) -> None:
        observation = {
            "kind": "decision_rejected",
            "status": "failed",
            "summary": "Child decision must call an allowed tool, complete a node, wait, or finalize.",
        }
        state.observations.append(observation)
        await state.repository.update_agent_turn(
            prepared.turn.id,
            status="failed",
            phase="failed",
            observation=observation,
        )

    async def _budget_exhausted(self, state: ChildRunState) -> SubagentResult:
        result = SubagentResult(
            status=SubagentExecutionStatus.failed,
            summary="Child model-call budget was exhausted before a valid result.",
            open_issues=["model_call_budget_exhausted"],
            usage=state.usage,
            provenance=self._services._provenance(state.execution, self._contract),
        )
        await self._services._finish_node_execution(
            self._runtime.session,
            state.node_execution,
            result,
        )
        await self._services._terminal(state.executions, state.execution, result)
        await self._services._settle_budget(self._runtime, state.execution.id, result)
        await self._runtime.session.commit()
        return result
