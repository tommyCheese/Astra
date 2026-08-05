"""Effect analysis and permission authorization for frozen tool invocations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.application.permissions.effects import DefaultEffectAnalyzer, effect_plan_hash
from app.application.permissions.engine import PermissionEngine
from app.application.permissions.invocation import InvocationAuthorizationResult
from app.common.core.config import Settings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.types import ExecutionMode
from app.common.schemas.permissions import (
    ActionEffectPlan,
    PermissionBundle,
    PermissionPolicySet,
    PermissionSubject,
)
from app.infrastructure.db.models.permissions import AgentIdentityRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import Tool, ToolExecutionError
from app.infrastructure.tools.router import ToolRouter


@dataclass(frozen=True)
class AuthorizationStageInput:
    run: RunRecord
    decision: AgentDecision
    main_identity: AgentIdentityRecord
    execution_mode: ExecutionMode
    tool_call_count: int
    is_approved_resume: bool
    approved_request_snapshot: dict[str, Any] | None
    approved_tool_call_id: str | None = None


@dataclass(frozen=True)
class AuthorizedInvocation:
    tool: Tool
    provider_identity: AgentIdentityRecord
    runtime_identity: AgentIdentityRecord
    effect_plan: ActionEffectPlan
    effect_plan_hash: str
    authorization: InvocationAuthorizationResult


class PermissionAuthorizationStage:
    """Make identity attenuation, effect analysis, and policy decision explicit."""

    def __init__(
        self,
        settings: Settings,
        run_repository: RunUnitOfWork,
        permission_repository: PermissionRepository,
        tool_router: ToolRouter,
    ) -> None:
        self._settings = settings
        self._run_repository = run_repository
        self._permission_repository = permission_repository
        self._tool_router = tool_router
        self._provider_identities: dict[str, AgentIdentityRecord] = {}

    async def execute(self, stage_input: AuthorizationStageInput) -> AuthorizedInvocation:
        tool = self._tool_router.resolve(
            stage_input.decision.tool_name,
            stage_input.decision.tool_input,
        )
        provider_identity = await self._provider_identity(stage_input, tool)
        schema_digest = self._schema_digest(tool)
        runtime_identity = await self._runtime_identity(
            stage_input,
            tool,
            provider_identity,
            schema_digest,
        )
        effect_plan = DefaultEffectAnalyzer().analyze(
            tool.spec,
            stage_input.decision.tool_input,
            task_id=stage_input.run.task_id,
        )
        effect_hash = effect_plan_hash(effect_plan)
        try:
            self._validate_approved_effect(stage_input, effect_plan, effect_hash)
        except ToolExecutionError:
            if stage_input.approved_tool_call_id:
                await self._run_repository.finish_tool_call(
                    stage_input.approved_tool_call_id,
                    error={
                        "category": "approval_integrity_error",
                        "message": "Approved effect plan no longer matches the invocation",
                    },
                )
            raise
        authorization = await self._authorize(
            stage_input,
            tool,
            runtime_identity,
            provider_identity,
            effect_plan,
            effect_hash,
            schema_digest,
        )
        await self._record_decision(stage_input.run.id, tool, effect_hash, authorization)
        return AuthorizedInvocation(
            tool,
            provider_identity,
            runtime_identity,
            effect_plan,
            effect_hash,
            authorization,
        )

    async def _provider_identity(
        self,
        stage_input: AuthorizationStageInput,
        tool: Tool,
    ) -> AgentIdentityRecord:
        existing = self._provider_identities.get(tool.spec.provider_id)
        if existing is not None:
            return existing
        identity = await self._permission_repository.get_or_create_identity(
            identity_type=(
                "external_provider" if tool.spec.provider_id != "astra.builtin" else "tool_provider"
            ),
            principal=tool.spec.provider_id,
            task_id=stage_input.run.task_id,
            run_id=stage_input.run.id,
            parent_identity_id=stage_input.main_identity.id,
            trust_level=tool.spec.trust_level,
            attributes={"provider_digest": tool.spec.provider_digest},
        )
        self._provider_identities[tool.spec.provider_id] = identity
        return identity

    async def _runtime_identity(
        self,
        stage_input: AuthorizationStageInput,
        tool: Tool,
        provider_identity: AgentIdentityRecord,
        schema_digest: str,
    ) -> AgentIdentityRecord:
        return await self._permission_repository.get_or_create_identity(
            identity_type="tool_runtime",
            principal=f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}",
            task_id=stage_input.run.task_id,
            run_id=stage_input.run.id,
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
        stage_input: AuthorizationStageInput,
        tool: Tool,
        runtime_identity: AgentIdentityRecord,
        provider_identity: AgentIdentityRecord,
        effect_plan: ActionEffectPlan,
        effect_hash: str,
        schema_digest: str,
    ) -> InvocationAuthorizationResult:
        raw_profile = stage_input.run.execution_profile or {}
        raw_bundle = raw_profile.get("permission_bundle")
        raw_policies = raw_profile.get("permission_policy_set")
        grants = await self._run_repository.list_approval_grants(
            stage_input.run.id,
            tool.spec.name,
            tool.spec.version,
        )
        return PermissionEngine().authorize_invocation(
            subject=PermissionSubject(
                agent_id=runtime_identity.id,
                identity_type="tool_runtime",
                task_id=stage_input.run.task_id,
                run_id=stage_input.run.id,
                parent_agent_id=provider_identity.id,
                delegation_chain=[
                    stage_input.main_identity.id,
                    provider_identity.id,
                    runtime_identity.id,
                ],
            ),
            effect_plan=effect_plan,
            effect_plan_hash=effect_hash,
            tool_input=stage_input.decision.tool_input,
            declared_permissions=tool.spec.permissions,
            execution_mode=stage_input.execution_mode,
            policies=PermissionPolicySet.model_validate(raw_policies) if raw_policies else None,
            grants=grants,
            provider_id=tool.spec.provider_id,
            schema_digest=schema_digest,
            once_approved=stage_input.is_approved_resume,
            data_flow=await self._permission_repository.get_data_flow_state(stage_input.run.id),
            permission_bundle=PermissionBundle.model_validate(raw_bundle) if raw_bundle else None,
            permission_bundle_signing_secret=self._settings.permission_bundle_signing_secret,
            unattended=not bool(raw_profile.get("interactive", True)),
            tool_identity=(
                f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}:"
                f"{tool.spec.provider_digest}"
            ),
            tool_call_count=stage_input.tool_call_count,
            run_started_at=stage_input.run.started_at or stage_input.run.created_at,
        )

    @staticmethod
    def _validate_approved_effect(
        stage_input: AuthorizationStageInput,
        effect_plan: ActionEffectPlan,
        effect_hash: str,
    ) -> None:
        if not stage_input.is_approved_resume:
            return
        snapshot = stage_input.approved_request_snapshot
        valid = snapshot is not None and (
            snapshot["effect_plan_hash"] is None
            or (
                snapshot["effect_plan_hash"] == effect_hash
                and snapshot["frozen_effect_plan"] == effect_plan.model_dump(mode="json")
                and snapshot["analyzer_version"] == effect_plan.analyzer_version
                and snapshot["analyzer_digest"] == effect_plan.analyzer_digest
            )
        )
        if not valid:
            raise ToolExecutionError(
                "approval_integrity_error",
                "Approved effect plan failed integrity validation",
            )

    async def _record_decision(
        self,
        run_id: str,
        tool: Tool,
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
    def _schema_digest(tool: Tool) -> str:
        return hashlib.sha256(
            json.dumps(
                tool.spec.input_schema,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
