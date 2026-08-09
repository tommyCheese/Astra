from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.subagents.executor_contracts import AgentExecutor, AgentExecutorRuntime
from app.application.subagents.governance import ChildInvocationAuthorizer, stable_digest
from app.application.subagents.invocation import ChildToolInvocationInput, ChildToolInvocationStage
from app.application.subagents.run_loop import ChildAgentRun
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import CompletionDecision
from app.common.schemas.agent.planning import SuccessCriterion, TaskContract
from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
from app.common.schemas.agent.types import (
    CriterionStatus,
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanNodeStatus,
    TerminalState,
)
from app.common.schemas.context_compaction import ChildCheckpoint
from app.common.schemas.subagents import (
    DelegationContract,
    SubagentArtifactReference,
    SubagentContextManifest,
    SubagentEvidenceReference,
    SubagentExecutionStatus,
    SubagentResult,
)
from app.domain.execution.contracts import InvocationIntent
from app.domain.execution.model_port import DelegatedModelPort
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.executions import NodeExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry, validate_json_schema


def _evaluate_basic_completion(
    validation_outcomes: list[AgentValidationOutcome],
) -> CompletionDecision:
    blocking = [outcome.validator for outcome in validation_outcomes if not outcome.passed and outcome.blocking]
    warnings = list(
        dict.fromkeys(
            [warning for outcome in validation_outcomes for warning in outcome.warnings]
            + [issue.message for outcome in validation_outcomes for issue in outcome.issues if issue.severity == "warning"]
        )
    )
    if blocking:
        return CompletionDecision(
            state=TerminalState.blocked,
            reason="基础保障存在阻塞问题。",
            unmet_criteria=[f"validator:{validator}" for validator in blocking],
            warnings=warnings,
        )
    return CompletionDecision(
        state=TerminalState.completed_with_warnings if warnings else TerminalState.completed,
        reason="快速回答已完成基础保障检查。",
        warnings=warnings,
    )


class LocalAstraAgentExecutor(AgentExecutor):
    """A child-local Astra loop for the conservative depth-one, read-only slice."""

    def __init__(
        self,
        *,
        model_client: DelegatedModelPort,
        tool_registry: AstraToolRegistry,
        settings: AstraRuntimeSettings | None = None,
    ):
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.settings = settings or AstraRuntimeSettings()
        self.authorizer = ChildInvocationAuthorizer()

    async def execute(
        self,
        *,
        contract: DelegationContract,
        context_manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
        checkpoint: dict[str, Any] | None = None,
    ) -> SubagentResult:
        self._validate_runtime(contract, context_manifest, runtime)
        self.model_client.bind_agent_execution(runtime.execution_context.agent_execution_id)
        return await ChildAgentRun(
            services=self,
            contract=contract,
            context_manifest=context_manifest,
            runtime=runtime,
            checkpoint=checkpoint,
        ).execute()

    async def _call_tool(
        self,
        *,
        repo: RunUnitOfWork,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        runtime: AgentExecutorRuntime,
        contract: DelegationContract,
        turn_id: str,
        plan_node_id: str | None,
        node_execution_id: str | None,
        tool_name: str,
        tool_input: dict[str, Any],
        usage: dict[str, Any],
    ) -> dict[str, Any] | SubagentResult:
        intent = InvocationIntent(
            tool_name=tool_name,
            tool_input=tool_input,
            idempotency_key=stable_digest(
                {
                    "execution_id": execution.id,
                    "turn_id": turn_id,
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                }
            ),
            plan_node_id=plan_node_id,
            node_execution_id=node_execution_id,
        )
        return await ChildToolInvocationStage(
            settings=self.settings,
            tool_registry=self.tool_registry,
            authorizer=self.authorizer,
        ).execute(
            ChildToolInvocationInput(
                repository=repo,
                executions=executions,
                execution=execution,
                runtime=runtime,
                contract=contract,
                turn_id=turn_id,
                intent=intent,
                usage=usage,
            )
        )

    def _result_from_decision(
        self,
        payload: dict[str, Any],
        *,
        summary: str,
        execution: AgentExecutionRecord,
        contract: DelegationContract,
        usage: dict[str, Any],
        artifacts: list[SubagentArtifactReference],
        evidence: list[SubagentEvidenceReference],
    ) -> SubagentResult:
        outputs = deepcopy(payload.get("outputs", {}))
        outcomes: list[AgentValidationOutcome] = []
        try:
            validate_json_schema(outputs, contract.request.output_schema, path="outputs")
            outcomes.append(AgentValidationOutcome(validator="subagent_output_schema", passed=True))
        except ValueError as exc:
            outcomes.append(
                AgentValidationOutcome(
                    validator="subagent_output_schema",
                    passed=False,
                    blocking=True,
                    issues=[
                        AgentValidationIssue(
                            code="subagent_output_schema_invalid",
                            message=str(exc),
                            severity="error",
                        )
                    ],
                )
            )
        completion = _evaluate_basic_completion(outcomes)
        if completion.state.value == "blocked":
            return SubagentResult(
                status=SubagentExecutionStatus.failed,
                summary="Child output failed schema validation.",
                outputs=outputs,
                open_issues=completion.unmet_criteria,
                completion=completion.model_dump(mode="json"),
                usage=usage,
                provenance=self._provenance(execution, contract),
            )
        warnings = list(payload.get("warnings", []))
        return SubagentResult(
            status=(SubagentExecutionStatus.completed_with_warnings if warnings else SubagentExecutionStatus.completed),
            summary=str(payload.get("summary") or summary),
            outputs=outputs,
            artifacts=[
                *artifacts,
                *[self._artifact_reference(item) for item in payload.get("artifacts", [])],
            ],
            evidence_refs=[
                *evidence,
                *[SubagentEvidenceReference.model_validate(item) for item in payload.get("evidence_refs", [])],
            ],
            claims=list(payload.get("claims", [])),
            open_issues=list(payload.get("open_issues", [])),
            completion=completion.model_dump(mode="json"),
            usage=usage,
            provenance=self._provenance(execution, contract),
        )

    @staticmethod
    def _artifact_reference(item: dict[str, Any]) -> SubagentArtifactReference:
        if "uri" in item:
            return SubagentArtifactReference.model_validate(item)
        artifact_id = str(item["id"])
        metadata = item.get("metadata") or {}
        return SubagentArtifactReference(
            id=artifact_id,
            uri=str(item.get("content_url") or f"artifact://{artifact_id}"),
            name=metadata.get("filename"),
            mime_type=item.get("mime_type"),
            content_hash=item.get("checksum"),
        )

    @staticmethod
    async def _finish_node_execution(
        session: AsyncSession,
        node_execution,
        result: SubagentResult,
    ) -> None:
        if node_execution is None:
            return
        repository = NodeExecutionRepository(session)
        current = await repository.require(node_execution.id)
        if current.status in {
            NodeExecutionStatus.completed.value,
            NodeExecutionStatus.failed.value,
            NodeExecutionStatus.blocked.value,
            NodeExecutionStatus.cancelled.value,
        }:
            return
        success = result.status in {
            SubagentExecutionStatus.completed,
            SubagentExecutionStatus.completed_with_warnings,
        }
        await repository.transition(
            current.id,
            expected_version=current.state_version,
            phase=(NodeExecutionPhase.completed if success else NodeExecutionPhase.failed),
            status=(NodeExecutionStatus.completed if success else NodeExecutionStatus.failed),
            result=result.model_dump(mode="json") if success else None,
            failure=(None if success else {"category": (result.open_issues[0] if result.open_issues else "child_failed")}),
        )

    @staticmethod
    async def _node_execution(
        session: AsyncSession,
        *,
        execution: AgentExecutionRecord,
        plan,
        node,
        worker_id: str,
    ):
        from sqlalchemy import select

        from app.infrastructure.db.models.executions import NodeExecutionRecord

        repository = NodeExecutionRepository(session)
        current = await session.scalar(
            select(NodeExecutionRecord).where(
                NodeExecutionRecord.agent_execution_id == execution.id,
                NodeExecutionRecord.plan_node_id == node.id,
                NodeExecutionRecord.current_slot == "current",
            )
        )
        if current is not None:
            return current
        current = await repository.create_claim(
            run_id=execution.run_id,
            plan_id=plan.id,
            plan_version=plan.version,
            plan_node_id=node.id,
            worker_id=worker_id,
            agent_execution_id=execution.id,
        )
        return await repository.transition(
            current.id,
            expected_version=current.state_version,
            phase=NodeExecutionPhase.running,
        )

    @staticmethod
    def _task_contract(contract: DelegationContract) -> TaskContract:
        return TaskContract(
            original_goal=contract.request.objective,
            deliverables=list(contract.request.output_schema.get("properties", {})),
            constraints=[f"included:{item}" for item in contract.request.scope.included]
            + [f"excluded:{item}" for item in contract.request.scope.excluded],
            prohibited_actions=["publish_final_answer", "modify_parent_state"],
            success_criteria=[
                SuccessCriterion(
                    id=f"child-criterion-{index}",
                    description=description,
                    verification_method="delegation_output_schema",
                    status=CriterionStatus.pending,
                    provenance={"delegation_contract": contract.contract_id},
                )
                for index, description in enumerate(contract.request.success_criteria, start=1)
            ],
            risk_level="low",
        )

    @staticmethod
    def _next_node(plan):
        completed = {node.node_key for node in plan.nodes if node.status == PlanNodeStatus.completed.value}
        dependencies = {
            node.id: {
                next(item.node_key for item in plan.nodes if item.id == edge.predecessor_id)
                for edge in plan.edges
                if edge.successor_id == node.id
            }
            for node in plan.nodes
        }
        return next(
            (
                node
                for node in sorted(plan.nodes, key=lambda item: item.index)
                if node.status in {PlanNodeStatus.pending.value, PlanNodeStatus.running.value}
                and dependencies[node.id] <= completed
            ),
            None,
        )

    @staticmethod
    def _plan_context(plan) -> dict[str, Any]:
        return {
            "id": plan.id,
            "version": plan.version,
            "nodes": [LocalAstraAgentExecutor._node_context(node) for node in plan.nodes],
        }

    @staticmethod
    def _node_context(node) -> dict[str, Any] | None:
        if node is None:
            return None
        return {
            "id": node.id,
            "node_key": node.node_key,
            "intent": node.intent,
            "status": node.status,
            "required_capabilities": list(node.required_capabilities or []),
        }

    async def _checkpoint(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        runtime: AgentExecutorRuntime,
        usage: dict[str, Any],
        observations: list[dict[str, Any]],
        plan_id: str,
        context_checkpoint: ChildCheckpoint,
    ) -> AgentExecutionRecord:
        existing = execution.checkpoint or {}
        return await executions.save_checkpoint(
            execution.id,
            worker_id=runtime.worker_id,
            fencing_token=execution.fencing_token,
            expected_state_version=execution.state_version,
            checkpoint={
                "schema_version": 1,
                "runtime_version": "astra-subagent-v1",
                "plan_id": plan_id,
                "observations": deepcopy(observations),
                "resume_safe": True,
                "contract_hash": execution.contract.get("contract_hash"),
                "tool_catalog_digest": runtime.frozen_catalog.tool_digest,
                "skill_catalog_digest": runtime.frozen_catalog.skill_digest,
                "context_checkpoint": context_checkpoint.model_dump(mode="json"),
                **{key: deepcopy(existing[key]) for key in ("context_compaction", "context_continuation") if key in existing},
            },
            budget_usage=usage,
            cancellation_epoch=execution.cancellation_epoch,
        )

    async def _transition_waiting(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        result: SubagentResult,
        status: str,
        reason: str,
    ) -> None:
        current = await executions.require(execution.id)
        await executions.transition(
            execution.id,
            expected_state_version=current.state_version,
            expected_fencing_token=current.fencing_token,
            expected_cancellation_epoch=execution.cancellation_epoch,
            status=status,
            phase=status,
            wait_reason=reason,
            result=result.model_dump(mode="json"),
        )

    async def _terminal(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        result: SubagentResult,
    ) -> None:
        current = await executions.require(execution.id)
        if current.status == SubagentExecutionStatus.running.value:
            current = await executions.transition(
                execution.id,
                expected_state_version=current.state_version,
                expected_fencing_token=current.fencing_token,
                expected_cancellation_epoch=execution.cancellation_epoch,
                status=SubagentExecutionStatus.completing,
                phase="completing",
            )
        await executions.transition(
            execution.id,
            expected_state_version=current.state_version,
            expected_fencing_token=current.fencing_token,
            expected_cancellation_epoch=execution.cancellation_epoch,
            status=result.status,
            phase="terminal",
            result=result.model_dump(mode="json"),
        )

    @staticmethod
    async def _settle_budget(
        runtime: AgentExecutorRuntime,
        execution_id: str,
        result: SubagentResult,
    ) -> None:
        if runtime.budget_manager is not None:
            await runtime.budget_manager.settle(
                execution_id,
                actual_usage=result.usage,
                commit=False,
            )

    async def _event(
        self,
        repo: RunUnitOfWork,
        runtime: AgentExecutorRuntime,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        enriched = {
            **payload,
            "agent_execution_id": runtime.execution_context.agent_execution_id,
        }
        await repo.add_event(
            runtime.execution_context.run_id,
            event_type,
            enriched,
            agent_execution_id=runtime.execution_context.agent_execution_id,
        )
        if runtime.on_event is not None:
            await runtime.on_event(event_type, enriched)

    @staticmethod
    def _provenance(
        execution: AgentExecutionRecord,
        contract: DelegationContract,
    ) -> dict[str, Any]:
        return {
            "agent_execution_id": execution.id,
            "identity_id": execution.identity_id,
            "delegation_id": execution.delegation_id,
            "contract_id": contract.contract_id,
            "contract_hash": contract.contract_hash,
        }

    @staticmethod
    def _validate_runtime(
        contract: DelegationContract,
        manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
    ) -> None:
        context = runtime.execution_context
        if (
            context.task_id != contract.task_id
            or context.run_id != contract.run_id
            or manifest.agent_execution_id != context.agent_execution_id
            or manifest.tool_catalog_digest != runtime.frozen_catalog.tool_digest
            or context.tool_catalog_digest != runtime.frozen_catalog.tool_digest
        ):
            raise ValueError("Child executor runtime does not match the frozen delegation")
        if contract.depth != 1:
            raise ValueError("The initial local child executor supports depth one only")
        if context.effective_scope.workspace_write_roots:
            raise ValueError("The initial local child executor is read-only")
