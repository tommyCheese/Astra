from copy import deepcopy
from typing import Any

from sqlalchemy import update

from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import AgentExecutionRecord, NodeExecutionRecord
from app.infrastructure.db.models.permissions import (
    ApprovalGrantRecord,
    ApprovalRequestRecord,
    ToolCallRecord,
)
from app.infrastructure.repositories.approval_contracts import ApprovalRequestCreate
from app.infrastructure.repositories.approval_grant_store import ApprovalGrantStore


def _catalog_digest(execution: AgentExecutionRecord) -> str:
    snapshot = execution.catalog_snapshot or {}
    return str(snapshot.get("catalog_digest") or snapshot.get("tool_digest") or "")


class ApprovalStore(ApprovalGrantStore):
    async def create_approval_request(
        self,
        command: ApprovalRequestCreate,
    ) -> ApprovalRequestRecord:
        call = await self._require_tool_call(command.tool_call_id)
        if call.run_id != command.run_id:
            raise ValueError("Approval ToolCall does not belong to the Run")
        lineage = await self._approval_lineage(command, call)
        request = ApprovalRequestRecord(
            **self._approval_request_fields(command, lineage),
            grant_scope=self._exact_grant_scope(command, lineage),
            status="pending",
        )
        self.session.add(request)
        await self.session.flush()
        await self.add_event(
            command.run_id,
            "approval.requested",
            self._approval_requested_event(request, command),
        )
        await self.session.flush()
        return request

    async def _approval_lineage(
        self,
        command: ApprovalRequestCreate,
        call: ToolCallRecord,
    ) -> dict[str, str | None]:
        execution_id = command.agent_execution_id or call.agent_execution_id
        if command.agent_execution_id is not None and call.agent_execution_id != command.agent_execution_id:
            raise ValueError("Approval AgentExecution does not match the ToolCall lineage")
        execution = await self._bound_execution(command.run_id, execution_id)
        requester_identity_id = command.requester_identity_id or (execution.identity_id if execution else None)
        delegation_id = command.delegation_id or (execution.delegation_id if execution else None)
        return {
            "agent_execution_id": execution_id,
            "requester_identity_id": requester_identity_id,
            "delegation_id": delegation_id,
            "catalog_digest": self._bound_catalog_digest(command.catalog_digest, execution),
        }

    async def _bound_execution(
        self,
        run_id: str,
        execution_id: str | None,
    ) -> AgentExecutionRecord | None:
        if execution_id is None:
            return None
        execution = await self.session.get(AgentExecutionRecord, execution_id)
        if execution is None or execution.run_id != run_id:
            raise ValueError("Approval AgentExecution does not belong to the Run")
        return execution

    def _bound_catalog_digest(
        self,
        requested_digest: str | None,
        execution: AgentExecutionRecord | None,
    ) -> str | None:
        stored_digest = _catalog_digest(execution) if execution else ""
        if requested_digest is None:
            return stored_digest or None
        if stored_digest and requested_digest != stored_digest:
            raise ValueError("Approval catalog digest is stale")
        return requested_digest

    def _exact_grant_scope(
        self,
        command: ApprovalRequestCreate,
        lineage: dict[str, str | None],
    ) -> dict[str, Any]:
        exact_scope = deepcopy(command.grant_scope or {})
        exact_scope.update(
            {
                **lineage,
                "tool_name": command.tool_name,
                "tool_version": command.tool_version,
                "input_hash": command.input_hash,
                "effect_plan_hash": command.effect_plan_hash,
            }
        )
        return exact_scope

    def _approval_request_fields(
        self,
        command: ApprovalRequestCreate,
        lineage: dict[str, str | None],
    ) -> dict[str, Any]:
        return {
            "run_id": command.run_id,
            **lineage,
            "turn_id": command.turn_id,
            "tool_call_id": command.tool_call_id,
            "node_execution_id": command.node_execution_id,
            "execution_attempt": command.execution_attempt,
            "expected_execution_state_version": command.expected_execution_state_version,
            "tool_name": command.tool_name,
            "tool_version": command.tool_version,
            "frozen_input": deepcopy(command.frozen_input),
            "input_hash": command.input_hash,
            "frozen_effect_plan": deepcopy(command.frozen_effect_plan or {}),
            "effect_plan_hash": command.effect_plan_hash,
            "analyzer_version": command.analyzer_version,
            "analyzer_digest": command.analyzer_digest,
            "continuation_token": command.continuation_token,
            "reviewer_identity": deepcopy(command.reviewer_identity),
            "preview": command.preview,
            "permission": command.permission,
            "impact": command.impact,
            "similar_matcher": deepcopy(command.similar_matcher),
        }

    def _approval_requested_event(
        self,
        request: ApprovalRequestRecord,
        command: ApprovalRequestCreate,
    ) -> dict[str, Any]:
        effect_plan = command.frozen_effect_plan or {}
        effects = [effect for effect in effect_plan.get("effects", []) if isinstance(effect, dict)]
        return {
            "approval_id": request.id,
            "tool_call_id": command.tool_call_id,
            "node_execution_id": command.node_execution_id,
            "execution_attempt": command.execution_attempt,
            "expected_execution_state_version": command.expected_execution_state_version,
            "tool_name": command.tool_name,
            "preview": command.preview,
            "permission": command.permission,
            "impact": command.impact,
            "allow_similar": command.similar_matcher is not None,
            "effect_plan_hash": command.effect_plan_hash,
            "action_summary": effect_plan.get("summary"),
            "effect_kinds": [effect.get("kind") for effect in effects],
            "resources": [effect.get("resource") for effect in effects],
        }

    async def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        decision: str,
        *,
        continuation_token: str,
        reviewer_identity: dict[str, Any] | None = None,
        rejection_guidance: str | None = None,
    ) -> tuple[ApprovalRequestRecord, ToolCallRecord]:
        run, request, delegated_approval = await self._validated_decision(
            run_id,
            approval_id,
            decision,
            continuation_token,
            reviewer_identity,
        )
        await self._claim_decision(request, decision, reviewer_identity)
        call = await self._require_tool_call(request.tool_call_id)
        call.status = "approved" if decision != "reject" else "rejected"
        if decision == "reject":
            await self._record_rejection(run, request, call, rejection_guidance)
        if decision in {"allow_similar", "allow_task"}:
            self._create_approval_grant(run, request, decision)
        await self._resume_after_decision(run, request, delegated_approval)
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "approval.decided",
            {
                "approval_id": request.id,
                "tool_call_id": call.id,
                "tool_name": request.tool_name,
                "decision": decision,
                "guidance": rejection_guidance if decision == "reject" else None,
            },
        )
        await self.session.flush()
        return request, call

    async def _claim_decision(
        self,
        request: ApprovalRequestRecord,
        decision: str,
        reviewer_identity: dict[str, Any] | None,
    ) -> None:
        claimed = await self.session.execute(
            update(ApprovalRequestRecord)
            .where(
                ApprovalRequestRecord.id == request.id,
                ApprovalRequestRecord.run_id == request.run_id,
                ApprovalRequestRecord.status == "pending",
            )
            .values(
                status="approved" if decision != "reject" else "rejected",
                decision=decision,
                decided_at=utc_now(),
                reviewer_identity=deepcopy(reviewer_identity),
            )
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("Approval has already been decided")
        await self.session.refresh(request)

    async def _record_rejection(
        self,
        run,
        request: ApprovalRequestRecord,
        call: ToolCallRecord,
        guidance: str | None,
    ) -> None:
        call.completed_at = utc_now()
        turn = await self._require_agent_turn(request.turn_id)
        observation = {
            "kind": "approval_result",
            "status": "rejected",
            "summary": f"User rejected {request.tool_name}",
            "data": {"approved": False, "tool_call_id": call.id},
        }
        if guidance:
            observation["data"]["guidance"] = guidance
        turn.status = "completed"
        turn.phase = "committed"
        turn.observation = observation
        state = dict(run.agent_state or {})
        state["observations"] = [*state.get("observations", []), observation]
        state["version"] = int(state.get("version", run.state_version)) + 1
        run.agent_state = state
        run.state_version = state["version"]

    def _create_approval_grant(
        self,
        run,
        request: ApprovalRequestRecord,
        decision: str,
    ) -> None:
        proposal = request.similar_matcher or {}
        default_effect_kinds = [
            effect["kind"]
            for effect in request.frozen_effect_plan.get("effects", [])
            if isinstance(effect, dict) and isinstance(effect.get("kind"), str)
        ]
        constraints = proposal.get("invocation_constraints", {}) if "effect_kinds" in proposal else proposal
        self.session.add(
            ApprovalGrantRecord(
                run_id=run.id,
                task_id=run.task_id,
                scope="task" if decision == "allow_task" else "run",
                subject=self._grant_subject(run, request, decision),
                tool_name=request.tool_name,
                tool_version=request.tool_version,
                matcher=deepcopy(constraints),
                effect_kinds=deepcopy(proposal.get("effect_kinds", default_effect_kinds)),
                resource_matcher=deepcopy(proposal.get("resource_matcher", {})),
                invocation_constraints=deepcopy(constraints),
                source_approval_id=request.id,
            )
        )

    def _grant_subject(self, run, request: ApprovalRequestRecord, decision: str) -> dict[str, Any]:
        subject = {"task_id": run.task_id, **deepcopy(request.grant_scope or {})}
        if decision != "allow_task":
            subject["run_id"] = run.id
        return subject

    async def _resume_after_decision(
        self,
        run,
        request: ApprovalRequestRecord,
        delegated: bool,
    ) -> None:
        if not delegated:
            run.waiting_state = None
            run.status = "executing"
            run.completed_at = None
            return
        execution = await self.session.get(AgentExecutionRecord, request.agent_execution_id)
        if execution is None or execution.status != "waiting_approval":
            raise ValueError("Delegated approval no longer has a waiting execution")
        execution.status = "queued"
        execution.phase = "approval_decided"
        execution.wait_reason = None
        execution.worker_id = None
        execution.state_version += 1
        execution.updated_at = utc_now()

    async def _validated_decision(
        self,
        run_id: str,
        approval_id: str,
        decision: str,
        continuation_token: str,
        reviewer_identity: dict[str, Any] | None,
    ):
        run = await self.require_run(run_id)
        request = await self.session.get(ApprovalRequestRecord, approval_id)
        if request is None or request.run_id != run_id or request.status != "pending":
            raise ValueError("Approval has already been decided")
        execution = await self._approval_execution(request)
        delegated = bool(execution is not None and execution.parent_execution_id is not None)
        self._validate_continuation(run, request, delegated, approval_id, continuation_token)
        self._validate_approval_identity(request, execution, run_id)
        await self._validate_node_execution(request)
        self._validate_decision_and_reviewer(request, decision, reviewer_identity)
        return run, request, delegated

    async def _approval_execution(
        self,
        request: ApprovalRequestRecord,
    ) -> AgentExecutionRecord | None:
        if not request.agent_execution_id:
            return None
        return await self.session.get(AgentExecutionRecord, request.agent_execution_id)

    def _validate_continuation(
        self,
        run,
        request: ApprovalRequestRecord,
        delegated: bool,
        approval_id: str,
        continuation_token: str,
    ) -> None:
        if not delegated:
            if run.status != "waiting_user" or not run.waiting_state:
                raise ValueError("Run is not waiting for approval")
            if run.waiting_state.get("approval_id") != approval_id:
                raise ValueError("Approval is not pending for this run")
            if run.waiting_state.get("continuation_token") != continuation_token:
                raise ValueError("Invalid continuation token")
        if request.continuation_token and request.continuation_token != continuation_token:
            raise ValueError("Approval continuation token is stale")

    def _validate_approval_identity(
        self,
        request: ApprovalRequestRecord,
        execution: AgentExecutionRecord | None,
        run_id: str,
    ) -> None:
        if not request.agent_execution_id:
            return
        if (
            execution is None
            or execution.run_id != run_id
            or execution.identity_id != request.requester_identity_id
            or execution.delegation_id != request.delegation_id
        ):
            raise ValueError("Approval is bound to stale delegated identity context")
        if request.catalog_digest and _catalog_digest(execution) != request.catalog_digest:
            raise ValueError("Approval is bound to a stale catalog snapshot")

    async def _validate_node_execution(self, request: ApprovalRequestRecord) -> None:
        if not request.node_execution_id:
            return
        execution = await self.session.get(NodeExecutionRecord, request.node_execution_id)
        if (
            execution is None
            or execution.attempt != request.execution_attempt
            or execution.state_version != request.expected_execution_state_version
            or execution.current_slot != "current"
        ):
            raise ValueError("Approval is bound to a stale NodeExecution attempt")

    def _validate_decision_and_reviewer(
        self,
        request: ApprovalRequestRecord,
        decision: str,
        reviewer_identity: dict[str, Any] | None,
    ) -> None:
        if decision in {"allow_similar", "allow_task"} and request.similar_matcher is None:
            raise ValueError("Similar approval is not available")
        if reviewer_identity and reviewer_identity.get("identity_type") in {
            "main_agent",
            "subagent",
            "tool_runtime",
            "external_provider",
        }:
            raise ValueError("Agent identities cannot approve their own actions")
        reviewer_id = reviewer_identity.get("id") if reviewer_identity else None
        forbidden_reviewers = {
            request.requester_identity_id,
            (request.grant_scope or {}).get("parent_identity_id"),
        }
        if reviewer_id is not None and reviewer_id in forbidden_reviewers:
            raise ValueError("Delegation-chain identities cannot approve child actions")
