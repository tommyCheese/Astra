"""Effect analysis and permission authorization for frozen tool invocations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.permissions.effects import effect_plan_hash
from app.application.permissions.engine import PermissionEngine
from app.application.permissions.invocation import InvocationAuthorizationResult
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.permissions import (
    ActionEffectPlan,
    PermissionBundle,
    PermissionPolicySet,
    PermissionSubject,
)
from app.domain.execution.contracts import SubagentSupervisorPort
from app.infrastructure.db.models.permissions import AgentIdentityRecord
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraTool, ToolExecutionError
from app.infrastructure.tools.router import ToolRouter

if TYPE_CHECKING:
    from app.common.schemas.agent.execution_state import AgentDecision
    from app.infrastructure.db.models.permissions import ToolCallRecord
    from app.infrastructure.db.models.plans import PlanNodeRecord
    from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord


@dataclass(frozen=True)
class ToolActionInput:
    run: RunRecord
    run_id: str
    goal: str
    turn_index: int
    turn: AgentTurnRecord
    decision: AgentDecision
    main_identity: AgentIdentityRecord
    active_node: PlanNodeRecord | None
    active_node_execution_id: str | None
    model_context: dict[str, Any]
    execution_mode: str
    is_approved_resume: bool
    approved_request_snapshot: dict[str, Any] | None
    approved_tool_call: ToolCallRecord | None
    workspace_path: str | None
    subagent_supervisor: SubagentSupervisorPort | None

AuthorizedInvocation = tuple[
    AstraTool,
    AgentIdentityRecord,
    AgentIdentityRecord,
    ActionEffectPlan,
    str,
    InvocationAuthorizationResult,
]


@dataclass
class PermissionAuthorizationStage:
    """Make identity attenuation, effect analysis, and policy decision explicit."""

    _settings: AstraRuntimeSettings
    _run_repository: RunUnitOfWork
    _permission_repository: PermissionRepository
    _tool_router: ToolRouter
    _plugin_runtime: PluginRuntimeState
    _provider_identities: dict[str, AgentIdentityRecord] = field(default_factory=dict)

    async def execute(self, action: ToolActionInput, *, tool_call_count: int) -> AuthorizedInvocation:
        tool = self._tool_router.resolve(
            action.decision.tool_name,
            action.decision.tool_input,
        )
        provider_identity = await self._provider_identity(action, tool)
        schema_digest = self._schema_digest(tool)
        runtime_identity = await self._runtime_identity(
            action,
            tool,
            provider_identity,
            schema_digest,
        )
        effect_plan = self._plugin_runtime.effect_analyzer(tool.spec).analyze(
            tool.spec,
            action.decision.tool_input,
            task_id=action.run.task_id,
        )
        effect_hash = effect_plan_hash(effect_plan)
        try:
            self._validate_approved_effect(action, effect_plan, effect_hash)
        except ToolExecutionError:
            if action.approved_tool_call:
                await self._run_repository.finish_tool_call(
                    action.approved_tool_call.id,
                    error={
                        "category": "approval_integrity_error",
                        "message": "Approved effect plan no longer matches the invocation",
                    },
                )
            raise
        authorization = await self._authorize(
            action,
            tool,
            runtime_identity,
            provider_identity,
            effect_plan,
            effect_hash,
            schema_digest,
            tool_call_count,
        )
        await self._record_decision(action.run.id, tool, effect_hash, authorization)
        return (
            tool,
            provider_identity,
            runtime_identity,
            effect_plan,
            effect_hash,
            authorization,
        )

    async def _provider_identity(
        self,
        action: ToolActionInput,
        tool: AstraTool,
    ) -> AgentIdentityRecord:
        existing = self._provider_identities.get(tool.spec.provider_id)
        if existing is not None:
            return existing
        identity = await self._permission_repository.get_or_create_identity(
            identity_type=("external_provider" if tool.spec.provider_id != "astra.builtin" else "tool_provider"),
            principal=tool.spec.provider_id,
            task_id=action.run.task_id,
            run_id=action.run.id,
            parent_identity_id=action.main_identity.id,
            trust_level=tool.spec.trust_level,
            attributes={"provider_digest": tool.spec.provider_digest},
        )
        self._provider_identities[tool.spec.provider_id] = identity
        return identity

    async def _runtime_identity(
        self,
        action: ToolActionInput,
        tool: AstraTool,
        provider_identity: AgentIdentityRecord,
        schema_digest: str,
    ) -> AgentIdentityRecord:
        return await self._permission_repository.get_or_create_identity(
            identity_type="tool_runtime",
            principal=f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}",
            task_id=action.run.task_id,
            run_id=action.run.id,
            parent_identity_id=provider_identity.id,
            trust_level=tool.spec.trust_level,
            attributes={
                "provider_digest": tool.spec.provider_digest,
                "schema_digest": schema_digest,
                "permission_scope": {
                    "actions": tool.spec.permissions,
                    "resources": ["*"],
                },
            },
        )

    async def _authorize(
        self,
        action: ToolActionInput,
        tool: AstraTool,
        runtime_identity: AgentIdentityRecord,
        provider_identity: AgentIdentityRecord,
        effect_plan: ActionEffectPlan,
        effect_hash: str,
        schema_digest: str,
        tool_call_count: int,
    ) -> InvocationAuthorizationResult:
        raw_profile = action.run.execution_profile or {}
        raw_bundle = raw_profile.get("permission_bundle")
        raw_policies = raw_profile.get("permission_policy_set")
        grants = await self._run_repository.list_approval_grants(
            action.run.id,
            tool.spec.name,
            tool.spec.version,
        )
        return PermissionEngine().authorize_invocation(
            subject=PermissionSubject(
                agent_id=runtime_identity.id,
                identity_type="tool_runtime",
                task_id=action.run.task_id,
                run_id=action.run.id,
                parent_agent_id=provider_identity.id,
                delegation_chain=[
                    action.main_identity.id,
                    provider_identity.id,
                    runtime_identity.id,
                ],
            ),
            effect_plan=effect_plan,
            effect_plan_hash=effect_hash,
            tool_input=action.decision.tool_input,
            declared_permissions=tool.spec.permissions,
            execution_mode=action.execution_mode,
            policies=PermissionPolicySet.model_validate(raw_policies) if raw_policies else None,
            grants=grants,
            provider_id=tool.spec.provider_id,
            schema_digest=schema_digest,
            once_approved=action.is_approved_resume,
            data_flow=await self._permission_repository.get_data_flow_state(action.run.id),
            permission_bundle=PermissionBundle.model_validate(raw_bundle) if raw_bundle else None,
            permission_bundle_signing_secret=self._settings.permission_bundle_signing_secret,
            unattended=not bool(raw_profile.get("interactive", True)),
            tool_identity=(f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}:{tool.spec.provider_digest}"),
            tool_call_count=tool_call_count,
            run_started_at=action.run.started_at or action.run.created_at,
        )

    def _validate_approved_effect(
        self,
        action: ToolActionInput,
        effect_plan: ActionEffectPlan,
        effect_hash: str,
    ) -> None:
        if not action.is_approved_resume:
            return
        snapshot = action.approved_request_snapshot
        valid = snapshot is not None and (
            snapshot["effect_plan_hash"] is None
            or (
                snapshot["effect_plan_hash"] == effect_hash
                and snapshot["frozen_effect_plan"] == effect_plan.model_dump(mode="json")
                and snapshot["analyzer_version"] == effect_plan.analyzer_version
                and snapshot["analyzer_digest"] == effect_plan.analyzer_digest
            )
        )
        if valid and snapshot.get("catalog_digest") and self._plugin_runtime.catalog is not None:
            current_digest = self._plugin_runtime.behavioral_digest(self._plugin_runtime.catalog.tool_registry())
            valid = snapshot["catalog_digest"] == current_digest
        if not valid:
            raise ToolExecutionError(
                "approval_integrity_error",
                "Approved effect plan failed integrity validation",
            )

    async def _record_decision(
        self,
        run_id: str,
        tool: AstraTool,
        effect_hash: str,
        authorization: InvocationAuthorizationResult,
    ) -> None:
        await self._run_repository.add_event(
            run_id,
            "permission.decided",
            {
                "tool_name": tool.spec.name,
                "effect_plan_hash": effect_hash,
                "decision": authorization.decision.decision.value,
                "reason_code": authorization.decision.explanation.reason_code,
                "requests": [
                    {
                        "action": request.action,
                        "resource": request.resource,
                        "subject_id": request.subject.agent_id,
                    }
                    for request in authorization.requests
                ],
            },
        )

    @staticmethod
    def _schema_digest(tool: AstraTool) -> str:
        return hashlib.sha256(
            json.dumps(
                tool.spec.input_schema,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
