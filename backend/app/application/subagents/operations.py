from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.subagents.budget import (
    DelegationGateInput,
    HierarchicalBudgetError,
    HierarchicalBudgetManager,
    evaluate_delegation,
)
from app.application.subagents.context import (
    SubagentContextComposer,
    SubagentContinuationService,
)
from app.application.subagents.executor_contracts import AgentExecutorRuntime
from app.application.subagents.fan_in import SubagentJoinService
from app.application.subagents.governance import (
    DelegationAuthorizationError,
    DelegationContractService,
    FrozenChildCatalog,
)
from app.application.subagents.lifecycle import CancellationReport, SubagentCancellationService
from app.common.schemas.agent.run_policy import EffectiveSubagentPolicy
from app.common.schemas.context_compaction import parse_child_checkpoint
from app.common.schemas.permissions import PermissionPolicySet
from app.common.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationContract,
    DelegationRejectionCode,
    DelegationRequest,
    EffectiveDelegationScope,
    SubagentContinuationAnswer,
    SubagentExecutionStatus,
    SubagentFanoutRequest,
    SubagentFanoutResult,
    SubagentQuestion,
)
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.db.models.permissions import AgentIdentityRecord
from app.infrastructure.repositories.agent_executions import (
    TERMINAL_AGENT_STATUSES,
    AgentExecutionRepository,
)
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


class SubagentRuntimeOperations:
    """Parent-facing operations over durable child execution handles."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: EffectiveSubagentPolicy,
        permission_policies: PermissionPolicySet | None = None,
        task_policy_scope: dict[str, Any] | None = None,
        continuation_secret: str = "astra-subagent-local",
    ):
        self.session = session
        self.policy = policy
        self.contracts = DelegationContractService(
            session,
            policy=policy,
            permission_policies=permission_policies,
            task_policy_scope=task_policy_scope,
        )
        self.executions = AgentExecutionRepository(session)
        self.permissions = PermissionRepository(session)
        self.continuations = SubagentContinuationService(
            continuation_secret,
            max_round_trips=policy.budgets.max_parent_round_trips,
        )
        self.budgets = HierarchicalBudgetManager(
            session,
            parent_reserve={
                "tokens": policy.budgets.parent_token_reserve,
                "model_calls": policy.budgets.parent_model_call_reserve,
                "tool_calls": policy.budgets.parent_tool_call_reserve,
                "cost_usd": policy.budgets.parent_cost_reserve_usd,
            },
        )

    async def delegate_task(
        self,
        *,
        parent_execution_id: str,
        parent_identity_id: str,
        request: DelegationRequest,
        profile_layers: list[dict[str, Any]] | None = None,
        selected_facts: dict[str, Any] | None = None,
        permission_check=None,
        commit: bool = True,
    ) -> AgentExecutionRecord:
        await self._enforce_delegation_gate(parent_execution_id, request, commit)
        await self._enforce_parallel_limit(parent_execution_id)
        child = await self._create_child(parent_execution_id, parent_identity_id, request, commit)
        child.context_manifest = await self._compose_child_context(
            child, parent_identity_id, profile_layers, selected_facts, permission_check
        )
        await RunUnitOfWork(self.session).add_event(
            child.run_id,
            "subagent.delegation_accepted",
            {
                "request_id": request.request_id,
                "child_execution_id": child.id,
                "depth": child.depth,
                "relationship": request.relationship,
            },
            agent_execution_id=parent_execution_id,
        )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return child

    async def _enforce_delegation_gate(self, parent_id, request, commit) -> None:
        if not self.policy.enabled or self.policy.kill_switch:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.feature_disabled,
                "Subagent execution is disabled by the frozen Run policy.",
            )
        gate = evaluate_delegation(self._gate_input(request))
        if not gate.allowed:
            await self._record_gate_decision(parent_id, request, gate, "subagent.delegation_rejected", commit)
            raise DelegationAuthorizationError(
                DelegationRejectionCode.not_beneficial,
                "Delegation did not pass the deterministic benefit gate.",
                details={
                    "reason_code": gate.reason_code,
                    "score": gate.score,
                    "diagnostics": gate.diagnostics,
                },
            )
        if self.policy.rollout_cohort == "shadow":
            await self._record_gate_decision(parent_id, request, gate, "subagent.shadow_decision", commit)
            raise DelegationAuthorizationError(
                DelegationRejectionCode.feature_disabled,
                "Shadow cohort records delegation decisions without execution.",
                details={"shadow": True},
            )

    async def _record_gate_decision(self, parent_id, request, gate, event_type, commit):
        parent = await self.executions.require(parent_id)
        payload = {
            "request_id": request.request_id,
            "reason_code": gate.reason_code,
            "score": gate.score,
        }
        if event_type == "subagent.shadow_decision":
            payload["would_delegate"] = True
        await RunUnitOfWork(self.session).add_event(parent.run_id, event_type, payload, agent_execution_id=parent.id)
        await self._persist(commit)

    async def _enforce_parallel_limit(self, parent_id) -> None:
        active = [item for item in await self.executions.active_descendants(parent_id) if item.parent_execution_id == parent_id]
        maximum = self.policy.budgets.max_parallel_children
        if len(active) >= maximum:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.budget_rejected,
                "The parent reached its active child limit.",
                details={"active_children": len(active), "maximum": maximum},
            )

    async def _create_child(self, parent_id, parent_identity_id, request, commit):
        async def reserve(child: AgentExecutionRecord) -> None:
            limits = self.policy.budgets
            await self.budgets.reserve(
                parent_execution_id=parent_id,
                child_execution_id=child.id,
                envelope=request.budget,
                max_children_total=limits.max_children_total,
                max_children_per_parent=limits.max_children_per_parent,
                max_parallel_children=limits.max_parallel_children,
                commit=False,
            )

        try:
            return await self.contracts.authorize_and_create(
                parent_execution_id=parent_id,
                parent_identity_id=parent_identity_id,
                request=request,
                on_child_created=reserve,
                commit=commit,
            )
        except HierarchicalBudgetError as exc:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.budget_rejected,
                "The parent cannot reserve the requested child budget.",
                details={"reason": str(exc)},
            ) from exc
        except DelegationAuthorizationError as exc:
            await self._record_authorization_rejection(parent_id, request, exc, commit)
            raise

    async def _record_authorization_rejection(self, parent_id, request, error, commit):
        parent = await self.executions.require(parent_id)
        await RunUnitOfWork(self.session).add_event(
            parent.run_id,
            "subagent.delegation_rejected",
            {"request_id": request.request_id, "reason_code": error.issue.code.value},
            agent_execution_id=parent.id,
        )
        await self._persist(commit)

    async def _compose_child_context(self, child, parent_identity_id, profile_layers, selected_facts, permission_check):
        contract = DelegationContract.model_validate(child.contract)
        identity = await self.session.get(AgentIdentityRecord, child.identity_id)
        if identity is None:
            raise ValueError("Child identity was not created")
        scope = EffectiveDelegationScope.model_validate(identity.attributes["permission_scope"])
        catalog = self._catalog(child.catalog_snapshot)
        composed = SubagentContextComposer().compose(
            agent_execution_id=child.id,
            contract=contract,
            effective_scope=scope,
            catalog=catalog,
            profile_layers=profile_layers,
            selected_facts=selected_facts,
            permission_check=permission_check,
        )
        execution_context = await self._execution_context(
            child, identity, parent_identity_id, contract, scope, catalog, composed
        )
        return {
            "manifest": composed.manifest.model_dump(mode="json"),
            "manifest_hash": composed.manifest_hash,
            "gaps": [item.model_dump(mode="json") for item in composed.gaps],
            "execution_context": execution_context.model_dump(mode="json"),
            "delegation_gate": {"allowed": True, "reason_code": "delegation_beneficial"},
        }

    async def _execution_context(self, child, identity, parent_identity_id, contract, scope, catalog, composed):
        data_flow = await self.permissions.get_data_flow_state(child.run_id)
        data_flow_state = {}
        if data_flow is not None:
            data_flow_state = {
                "trust_sources": list(data_flow.trust_sources),
                "data_labels": list(data_flow.data_labels),
                "allowed_destinations": list(data_flow.allowed_destinations),
                "prohibited_destinations": list(data_flow.prohibited_destinations),
                "retention": deepcopy(data_flow.retention),
                "state_version": data_flow.state_version,
            }
        return DelegatedExecutionContext(
            task_id=child.task_id,
            run_id=child.run_id,
            agent_execution_id=child.id,
            identity_id=identity.id,
            parent_identity_id=parent_identity_id,
            delegation_id=str(child.delegation_id),
            delegation_chain=(parent_identity_id, identity.id),
            purpose=composed.manifest.purpose,
            effective_scope=scope,
            budget_envelope=contract.request.budget,
            data_flow_state=data_flow_state,
            workspace_scope=composed.manifest.workspace_scope,
            tool_catalog_digest=catalog.tool_digest,
            skill_catalog_digest=catalog.skill_digest,
        )

    async def _persist(self, commit: bool) -> None:
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

    async def delegate_tasks(
        self,
        *,
        parent_execution_id: str,
        parent_identity_id: str,
        fanout: SubagentFanoutRequest,
        profile_layers: list[dict[str, Any]] | None = None,
        selected_facts: dict[str, Any] | None = None,
        permission_check=None,
    ) -> SubagentFanoutResult:
        """Atomically create one bounded Swarm group and its durable Join."""
        if len(fanout.tasks) > self.policy.budgets.max_parallel_children:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.fanout_too_large,
                "Swarm fan-out exceeds the frozen parallel child limit.",
                details={
                    "requested": len(fanout.tasks),
                    "maximum": self.policy.budgets.max_parallel_children,
                },
            )
        joins = SubagentJoinService(self.session)
        existing = await joins.for_group(parent_execution_id, fanout.group_id)
        if existing is not None:
            children = list(
                (
                    await self.session.scalars(
                        select(AgentExecutionRecord).where(AgentExecutionRecord.id.in_(existing.child_execution_ids))
                    )
                ).all()
            )
            expected = [item.request_id for item in fanout.tasks]
            actual = [item.request_id for item in sorted(children, key=lambda row: row.ordinal)]
            if existing.join_key != fanout.join.key or actual != expected:
                raise DelegationAuthorizationError(
                    DelegationRejectionCode.fanout_conflict,
                    "Swarm group id already exists with different frozen requests.",
                )
            return SubagentFanoutResult(
                group_id=fanout.group_id,
                join_id=existing.id,
                child_execution_ids=tuple(existing.child_execution_ids),
                idempotent_replay=True,
            )
        children: list[AgentExecutionRecord] = []
        try:
            for request in fanout.tasks:
                children.append(
                    await self.delegate_task(
                        parent_execution_id=parent_execution_id,
                        parent_identity_id=parent_identity_id,
                        request=request,
                        profile_layers=profile_layers,
                        selected_facts=selected_facts,
                        permission_check=permission_check,
                        commit=False,
                    )
                )
            join = await joins.create(
                parent_execution_id=parent_execution_id,
                join_key=fanout.join.key,
                group_id=fanout.group_id,
                child_execution_ids=[item.id for item in children],
                policy=fanout.join.policy,
                consumer_plan_node_id=fanout.join.consumer_plan_node_id,
                commit=False,
            )
            await RunUnitOfWork(self.session).add_event(
                children[0].run_id,
                "subagent.fanout.accepted",
                {
                    "group_id": fanout.group_id,
                    "join_id": join.id,
                    "child_execution_ids": [item.id for item in children],
                    "width": len(children),
                },
                agent_execution_id=parent_execution_id,
            )
            join_id = join.id
            child_execution_ids = tuple(item.id for item in children)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return SubagentFanoutResult(
            group_id=fanout.group_id,
            join_id=join_id,
            child_execution_ids=child_execution_ids,
        )

    async def inspect_delegation(self, execution_id: str) -> dict[str, Any]:
        execution = await self.executions.require(execution_id)
        return {
            "id": execution.id,
            "run_id": execution.run_id,
            "parent_execution_id": execution.parent_execution_id,
            "request_id": execution.request_id,
            "depth": execution.depth,
            "status": execution.status,
            "phase": execution.phase,
            "wait_reason": execution.wait_reason,
            "budget_envelope": deepcopy(execution.budget_envelope),
            "budget_usage": deepcopy(execution.budget_usage),
            "catalog": {
                "tool_digest": execution.catalog_snapshot.get("tool_digest"),
                "skill_digest": execution.catalog_snapshot.get("skill_digest"),
            },
            "result": deepcopy(execution.result),
            "error": deepcopy(execution.error),
        }

    async def executor_runtime(
        self,
        execution_id: str,
        *,
        worker_id: str,
        artifact_service: Any = None,
        sandbox_service: Any = None,
    ) -> AgentExecutorRuntime:
        execution = await self.executions.require(execution_id)
        stored = execution.context_manifest or {}
        return AgentExecutorRuntime(
            session=self.session,
            execution_context=DelegatedExecutionContext.model_validate(stored["execution_context"]),
            frozen_catalog=self._catalog(execution.catalog_snapshot),
            permission_policies=self.contracts.permission_policies,
            worker_id=worker_id,
            artifact_service=artifact_service,
            sandbox_service=sandbox_service,
            continuation_service=self.continuations,
            budget_manager=self.budgets,
        )

    async def collect_delegation_results(
        self,
        *,
        parent_execution_id: str,
        execution_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        conditions = [AgentExecutionRecord.parent_execution_id == parent_execution_id]
        if execution_ids is not None:
            conditions.append(AgentExecutionRecord.id.in_(execution_ids))
        rows = list(
            (
                await self.session.scalars(
                    select(AgentExecutionRecord).where(*conditions).order_by(AgentExecutionRecord.ordinal)
                )
            ).all()
        )
        if execution_ids is not None and {item.id for item in rows} != set(execution_ids):
            raise ValueError("Result collection cannot cross the direct parent boundary")
        return [
            {
                "agent_execution_id": item.id,
                "status": item.status,
                "terminal": item.status in TERMINAL_AGENT_STATUSES,
                "result": deepcopy(item.result),
            }
            for item in rows
        ]

    async def respond_to_parent_question(
        self,
        *,
        answer: SubagentContinuationAnswer,
    ) -> AgentExecutionRecord:
        execution = await self.executions.require(answer.agent_execution_id)
        if execution.status != SubagentExecutionStatus.waiting_parent.value:
            raise ValueError("Child is not waiting for a parent answer")
        raw_result = execution.result or {}
        question = SubagentQuestion.model_validate(raw_result.get("question") or {})
        if answer.continuation_token != question.continuation_token or answer.round_trip != question.round_trip:
            raise ValueError("Parent answer does not match the pending continuation")
        raw_checkpoint = (execution.checkpoint or {}).get("context_checkpoint")
        if raw_checkpoint is None:
            raise ValueError("Child continuation checkpoint is unavailable")
        checkpoint = parse_child_checkpoint(raw_checkpoint)
        resumed = self.continuations.answer(
            checkpoint=checkpoint,
            question=question,
            values=answer.values,
        )
        execution.checkpoint = {
            **(execution.checkpoint or {}),
            "context_checkpoint": resumed.model_dump(mode="json"),
        }
        execution = await self.executions.transition(
            execution.id,
            expected_state_version=execution.state_version,
            status=SubagentExecutionStatus.queued,
            phase="checkpointing",
        )
        await self.session.commit()
        return execution

    async def cancel_delegation(
        self,
        execution_id: str,
        *,
        reason: str = "parent_cancelled",
    ) -> list[str]:
        report = await self.cancel_delegation_with_report(
            execution_id,
            reason=reason,
        )
        return list(report.cancelled_execution_ids)

    async def cancel_delegation_with_report(
        self,
        execution_id: str,
        *,
        reason: str = "parent_cancelled",
    ) -> CancellationReport:
        return await SubagentCancellationService(self.session).cancel_tree(
            execution_id,
            reason=reason,
        )

    @staticmethod
    def _catalog(snapshot: dict[str, Any]) -> FrozenChildCatalog:
        return FrozenChildCatalog(
            tools=tuple(snapshot.get("tools", [])),
            tool_digest=str(snapshot.get("tool_digest", "")),
            skills=tuple(snapshot.get("skills", [])),
            skill_digest=str(snapshot.get("skill_digest", "")),
        )

    @staticmethod
    def _gate_input(request: DelegationRequest) -> DelegationGateInput:
        configured = request.resource_scope.get("delegation_gate") or {}
        write_scope = " ".join(
            [
                *request.resource_scope.get("actions", []),
                *request.resource_scope.get("effect_kinds", []),
            ]
        ).lower()
        size = len(request.success_criteria) + len(request.scope.included) + len(request.inputs) + len(request.requested_tools)
        simple_atomic = bool(
            configured.get(
                "simple_atomic",
                size <= 2 and not request.requested_tools and not request.inputs,
            )
        )
        return DelegationGateInput(
            complexity=float(configured.get("complexity", min(1.0, size / 6))),
            independence=float(
                configured.get(
                    "independence",
                    0.95 if request.relationship == "independent_review" else 0.8,
                )
            ),
            context_pressure=float(configured.get("context_pressure", min(1.0, len(request.inputs) / 8))),
            estimated_benefit=float(configured.get("estimated_benefit", 0.65)),
            write_conflict_risk=float(
                configured.get(
                    "write_conflict_risk",
                    0.8 if any(item in write_scope for item in ("write", "delete")) else 0.05,
                )
            ),
            execution_risk=float(configured.get("execution_risk", 0.1)),
            budget_fraction_remaining=float(configured.get("budget_fraction_remaining", 1.0)),
            simple_atomic=simple_atomic,
            strongly_sequential=bool(
                configured.get(
                    "strongly_sequential",
                    request.resource_scope.get("strongly_sequential", False),
                )
            ),
        )
