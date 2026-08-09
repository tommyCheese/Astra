from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.permissions.engine import PermissionEngine
from app.application.permissions.invocation import InvocationAuthorizationResult
from app.application.subagents.scope import DelegationAuthorizationError, DelegationScopeAttenuator
from app.common.schemas.agent.run_policy import EffectiveSubagentPolicy
from app.common.schemas.agent.types import ExecutionMode
from app.common.schemas.permissions import (
    ActionEffectPlan,
    PermissionBundle,
    PermissionPolicySet,
    PermissionSubject,
)
from app.common.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationContract,
    DelegationRejectionCode,
    DelegationRequest,
    EffectiveDelegationScope,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.db.models.permissions import AgentIdentityRecord, ToolCatalogSnapshotRecord
from app.infrastructure.db.models.skills import RunSkillSnapshotRecord
from app.infrastructure.repositories.agent_executions import (
    TERMINAL_AGENT_STATUSES,
    AgentExecutionRepository,
)
from app.infrastructure.repositories.permissions import PermissionRepository


def stable_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True)
class FrozenChildCatalog:
    tools: tuple[dict[str, Any], ...]
    tool_digest: str
    skills: tuple[dict[str, Any], ...]
    skill_digest: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "tools": [deepcopy(item) for item in self.tools],
            "tool_digest": self.tool_digest,
            "skills": [deepcopy(item) for item in self.skills],
            "skill_digest": self.skill_digest,
        }

    def validate_resume(self, snapshot: dict[str, Any]) -> None:
        if snapshot != self.model_dump():
            raise DelegationAuthorizationError(
                DelegationRejectionCode.catalog_drift,
                "The child AstraTool/Skill Catalog changed after it was frozen.",
            )


class DelegationContractService:
    """Normalizes and freezes a governed delegation into durable child state."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: EffectiveSubagentPolicy,
        permission_policies: PermissionPolicySet | None = None,
        task_policy_scope: dict[str, Any] | None = None,
    ):
        self.session = session
        self.policy = policy
        self.permission_policies = permission_policies
        self.task_policy_scope = deepcopy(task_policy_scope or {})
        self.permissions = PermissionRepository(session)
        self.executions = AgentExecutionRepository(session)

    async def authorize_and_create(
        self,
        *,
        parent_execution_id: str,
        parent_identity_id: str,
        request: DelegationRequest,
        parent_node_execution_id: str | None = None,
        on_child_created: Callable[[AgentExecutionRecord], Awaitable[None]] | None = None,
        commit: bool = True,
    ) -> AgentExecutionRecord:
        self._validate_policy(request)
        parent_execution = await self.executions.require(parent_execution_id)
        parent_identity = await self.session.get(AgentIdentityRecord, parent_identity_id)
        if parent_identity is None or parent_identity.revoked_at is not None:
            raise ValueError("Parent Agent identity is unavailable")
        if parent_execution.identity_id not in {None, parent_identity.id}:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.identity_overlap,
                "Parent execution and delegation identity do not match.",
            )
        if parent_identity.run_id != parent_execution.run_id:
            raise ValueError("Parent identity is outside the AgentExecution Run")
        depth = parent_execution.depth + 1
        if depth > self.policy.budgets.max_depth:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.excessive_depth,
                "Delegation exceeds the configured maximum depth.",
                field="depth",
                details={"requested": depth, "maximum": self.policy.budgets.max_depth},
            )
        normalized = self.normalize_request(request)
        await self._reject_duplicate_or_overlap(parent_execution, normalized)
        contract = self.freeze_contract(parent_execution, normalized)
        effective_scope = DelegationScopeAttenuator.attenuate(
            requested=self._requested_scope(normalized),
            parent=parent_identity.attributes.get("permission_scope", {}),
            task_policy=self.task_policy_scope,
            server_policy=self.policy,
            execution_id=contract.contract_id,
        )
        catalog = await self.catalog_for_run(
            run_id=parent_execution.run_id,
            request=normalized,
            scope=effective_scope,
        )
        scope_payload = effective_scope.model_dump(mode="json")
        try:
            child_identity = await self.permissions.create_identity(
                identity_type="subagent",
                principal=f"astra.subagent:{contract.contract_id}",
                task_id=parent_execution.task_id,
                run_id=parent_execution.run_id,
                parent_identity_id=parent_identity.id,
                trust_level="delegated",
                attributes={
                    "contract_hash": contract.contract_hash,
                    "permission_scope": scope_payload,
                    "parent_secrets_inherited": False,
                },
                commit=False,
            )
            delegation = await self.permissions.create_delegation(
                parent_identity_id=parent_identity.id,
                child_identity_id=child_identity.id,
                delegated_scope=scope_payload,
                expires_at=normalized.deadline_at,
                policies=self.permission_policies,
                commit=False,
            )
            child = await self.executions.create_child(
                contract=contract,
                identity_id=child_identity.id,
                delegation_id=delegation.id,
                parent_node_execution_id=parent_node_execution_id,
                catalog_snapshot=catalog.model_dump(),
            )
            if on_child_created is not None:
                await on_child_created(child)
            if commit:
                await self.session.commit()
            else:
                await self.session.flush()
            return child
        except Exception:
            await self.session.rollback()
            raise

    @staticmethod
    def normalize_request(request: DelegationRequest) -> DelegationRequest:
        payload = request.model_dump(mode="python")
        for key in (
            "success_criteria",
            "required_capabilities",
            "requested_tools",
            "requested_skills",
        ):
            payload[key] = sorted(_unique_strings(payload.get(key, [])))
        payload["scope"]["included"] = sorted(_unique_strings(payload["scope"]["included"]))
        payload["scope"]["excluded"] = sorted(_unique_strings(payload["scope"].get("excluded", [])))
        payload["objective"] = request.objective.strip()
        payload["resource_scope"] = _canonicalize(payload.get("resource_scope", {}))
        payload["output_schema"] = _canonicalize(payload["output_schema"])
        return DelegationRequest.model_validate(payload)

    @staticmethod
    def freeze_contract(
        parent: AgentExecutionRecord,
        request: DelegationRequest,
    ) -> DelegationContract:
        canonical = {
            "schema_version": 1,
            "task_id": parent.task_id,
            "run_id": parent.run_id,
            "parent_execution_id": parent.id,
            "depth": parent.depth + 1,
            "request": request.model_dump(mode="json"),
        }
        digest = stable_digest(canonical)
        return DelegationContract(
            contract_id=f"dc_{digest.removeprefix('sha256:')[:32]}",
            contract_hash=digest,
            task_id=parent.task_id,
            run_id=parent.run_id,
            parent_execution_id=parent.id,
            depth=parent.depth + 1,
            request=DelegationRequest.model_validate(request.model_dump(mode="python")),
            created_at=utc_now(),
        )

    async def _catalog_from_snapshots(
        self,
        request: DelegationRequest,
        scope: EffectiveDelegationScope,
        tool_snapshot: ToolCatalogSnapshotRecord | None,
        *,
        run_id: str | None = None,
    ) -> FrozenChildCatalog:
        allowed_tools = set(scope.tools)
        tools = self._attenuated_tools(request, allowed_tools, tool_snapshot)
        resolved_run_id = run_id or (tool_snapshot.run_id if tool_snapshot is not None else None)
        skill_snapshot = None
        if resolved_run_id is not None:
            skill_snapshot = await self.session.scalar(
                select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == resolved_run_id)
            )
        skills = self._attenuated_skills(request, scope, allowed_tools, skill_snapshot)
        tools.sort(key=lambda item: (str(item.get("name")), str(item.get("version"))))
        skills.sort(key=lambda item: str(item.get("qualified_identity")))
        return FrozenChildCatalog(
            tools=tuple(tools),
            tool_digest=stable_digest(tools),
            skills=tuple(skills),
            skill_digest=stable_digest(skills),
        )

    def _attenuated_tools(self, request, allowed_tools, snapshot):
        requested = set(request.requested_tools)
        tools = [
            deepcopy(item)
            for item in (snapshot.catalog if snapshot else [])
            if str(item.get("name", "")) in requested & allowed_tools
            and item.get("execution_backend") != "astra.runtime"
            and item.get("name") != "swarm"
            and not (self.policy.read_only and self._tool_is_side_effecting(item))
        ]
        _require_catalog_entries(
            requested - {str(item.get("name")) for item in tools},
            field="requested_tools",
            catalog_name="AstraTool",
        )
        return tools

    def _attenuated_skills(self, request, scope, allowed_tools, snapshot):
        requested = set(request.requested_skills)
        skills = [
            deepcopy(item)
            for item in (snapshot.catalog if snapshot else [])
            if item.get("qualified_identity") in requested & set(scope.skills)
            and self._skill_tools_are_attenuated(item, allowed_tools)
        ]
        _require_catalog_entries(
            requested - {str(item.get("qualified_identity")) for item in skills},
            field="requested_skills",
            catalog_name="Skill",
        )
        return skills

    async def catalog_for_run(
        self,
        *,
        run_id: str,
        request: DelegationRequest,
        scope: EffectiveDelegationScope,
    ) -> FrozenChildCatalog:
        tool_snapshot = await self.session.scalar(
            select(ToolCatalogSnapshotRecord).where(ToolCatalogSnapshotRecord.run_id == run_id)
        )
        return await self._catalog_from_snapshots(request, scope, tool_snapshot, run_id=run_id)

    @staticmethod
    def require_skill_activation(catalog: FrozenChildCatalog, identity: str, revision_id: str) -> dict[str, Any]:
        entry = next(
            (
                item
                for item in catalog.skills
                if item.get("qualified_identity") == identity and item.get("revision_id") == revision_id
            ),
            None,
        )
        if entry is None:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.capability_not_delegated,
                "Skill activation is outside the frozen child Catalog.",
            )
        return deepcopy(entry)

    def _validate_policy(self, request: DelegationRequest) -> None:
        if self.policy.kill_switch:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.kill_switch_active,
                "The subagent kill switch is active.",
            )
        if not self.policy.enabled:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.feature_disabled,
                "Subagent execution is disabled for this Run.",
            )
        if request.join_policy.value not in self.policy.allowed_join_policies:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.disallowed_join_policy,
                "The requested join policy is not allowed.",
                field="join_policy",
            )
        limits = self.policy.budgets
        requested = request.budget
        comparisons = {
            "max_tokens": (requested.max_tokens, limits.max_tokens),
            "max_model_calls": (requested.max_model_calls, limits.max_model_calls),
            "max_tool_calls": (requested.max_tool_calls, limits.max_tool_calls),
            "max_wall_time_ms": (requested.max_wall_time_ms, limits.max_wall_time_seconds * 1000),
            "max_cost_usd": (requested.max_cost_usd, limits.max_cost_usd),
            "max_children": (requested.max_children, limits.max_children_per_parent),
        }
        exceeded = {
            key: {"requested": value, "maximum": maximum} for key, (value, maximum) in comparisons.items() if value > maximum
        }
        if exceeded:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.budget_rejected,
                "Delegation budget exceeds the effective Run policy.",
                field="budget",
                details=exceeded,
            )

    async def _reject_duplicate_or_overlap(
        self,
        parent: AgentExecutionRecord,
        request: DelegationRequest,
    ) -> None:
        siblings = list(
            (
                await self.session.scalars(
                    select(AgentExecutionRecord).where(
                        AgentExecutionRecord.parent_execution_id == parent.id,
                        AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
                    )
                )
            ).all()
        )
        requested_scope = set(request.scope.included)
        for sibling in siblings:
            sibling_request = (sibling.contract or {}).get("request", {})
            if sibling_request.get("dedupe_key") == request.dedupe_key:
                raise DelegationAuthorizationError(
                    DelegationRejectionCode.duplicate_request,
                    "An active sibling already owns this deduplication key.",
                    field="dedupe_key",
                )
            sibling_scope = set((sibling_request.get("scope") or {}).get("included", []))
            if (
                request.relationship != "independent_review"
                and sibling_request.get("relationship", "work") != "independent_review"
                and requested_scope & sibling_scope
            ):
                raise DelegationAuthorizationError(
                    DelegationRejectionCode.scope_overlap,
                    "Delegation overlaps an active sibling without an independent-review relationship.",
                    field="scope.included",
                    details={"sibling_execution_id": sibling.id},
                )

    @staticmethod
    def _requested_scope(request: DelegationRequest) -> dict[str, Any]:
        scope = deepcopy(request.resource_scope)
        scope.setdefault("tools", list(request.requested_tools))
        scope.setdefault("skills", list(request.requested_skills))
        return scope

    @staticmethod
    def _tool_is_side_effecting(item: dict[str, Any]) -> bool:
        if str(item.get("side_effect_level", "none")).lower() not in {"none", "read", "read_only"}:
            return True
        return any(DelegationScopeAttenuator._is_write(str(permission)) for permission in item.get("permissions", []))

    @staticmethod
    def _skill_tools_are_attenuated(item: dict[str, Any], tools: set[str]) -> bool:
        return all(
            any(fnmatchcase(tool, pattern) for tool in tools) for pattern in item.get("requested_tool_patterns", []) if pattern
        )


class ChildInvocationAuthorizer:
    """Binds child runtime identity and frozen governance state to one tool call."""

    def __init__(self, engine: PermissionEngine | None = None):
        self.engine = engine or PermissionEngine()

    def authorize(
        self,
        *,
        context: DelegatedExecutionContext,
        frozen_catalog: FrozenChildCatalog,
        tool_name: str,
        tool_version: str,
        effect_plan: ActionEffectPlan,
        effect_plan_hash: str,
        tool_input: dict[str, Any],
        declared_permissions: Iterable[str],
        execution_mode: ExecutionMode,
        policies: PermissionPolicySet | None = None,
        grants: Iterable[Any] = (),
        permission_bundle: PermissionBundle | None = None,
        permission_bundle_signing_secret: str = "",
        **kwargs: Any,
    ) -> InvocationAuthorizationResult:
        _validate_catalog_binding(context, frozen_catalog, tool_name, tool_version)
        _validate_effect_scope(context, effect_plan, declared_permissions)
        return self.engine.authorize_invocation(
            subject=PermissionSubject(
                agent_id=context.identity_id,
                identity_type="subagent_tool_runtime",
                task_id=context.task_id,
                run_id=context.run_id,
                parent_agent_id=context.parent_identity_id,
                agent_execution_id=context.agent_execution_id,
                delegation_id=context.delegation_id,
                delegation_chain=list(context.delegation_chain),
            ),
            effect_plan=effect_plan,
            effect_plan_hash=effect_plan_hash,
            tool_input=tool_input,
            declared_permissions=declared_permissions,
            execution_mode=execution_mode,
            policies=policies,
            grants=grants,
            data_flow=SimpleNamespace(**context.data_flow_state),
            permission_bundle=permission_bundle,
            permission_bundle_signing_secret=permission_bundle_signing_secret,
            provider_id=kwargs.get("provider_id"),
            schema_digest=kwargs.get("schema_digest"),
            once_approved=bool(kwargs.get("once_approved", False)),
            unattended=bool(kwargs.get("unattended", False)),
            tool_identity=str(kwargs.get("tool_identity", "")),
            tool_call_count=int(kwargs.get("tool_call_count", 0)),
            run_started_at=kwargs.get("run_started_at"),
            now=kwargs.get("now"),
        )

    @staticmethod
    def validate_reviewer(*, reviewer_identity_id: str, executor_context: DelegatedExecutionContext) -> None:
        if reviewer_identity_id in executor_context.delegation_chain:
            raise DelegationAuthorizationError(
                DelegationRejectionCode.identity_overlap,
                "An executor or its delegation ancestors cannot approve their own action.",
            )


def _require_catalog_entries(missing, *, field: str, catalog_name: str) -> None:
    if missing:
        raise DelegationAuthorizationError(
            DelegationRejectionCode.capability_not_delegated,
            f"Requested {field.removeprefix('requested_')} are absent from the attenuated {catalog_name} Catalog.",
            field=field,
            details={"missing": sorted(missing)},
        )


def _validate_catalog_binding(context, catalog, tool_name, tool_version) -> None:
    digests_differ = context.tool_catalog_digest != catalog.tool_digest or (
        context.skill_catalog_digest is not None and context.skill_catalog_digest != catalog.skill_digest
    )
    if digests_differ:
        raise DelegationAuthorizationError(
            DelegationRejectionCode.catalog_drift,
            "Invocation Catalog digests do not match the frozen child context.",
        )
    if not any(item.get("name") == tool_name and item.get("version") == tool_version for item in catalog.tools):
        raise DelegationAuthorizationError(
            DelegationRejectionCode.capability_not_delegated,
            "AstraTool invocation is outside the frozen child Catalog.",
        )


def _validate_effect_scope(context, effect_plan, declared_permissions) -> None:
    scope = context.effective_scope
    _require_effect_values(effect_plan, "kind", scope.effect_kinds, "effect kind")
    _require_effect_values(effect_plan, "resource", scope.resources, "effect resource")
    if declared_permissions and not _values_are_subset(declared_permissions, scope.actions):
        raise DelegationAuthorizationError(
            DelegationRejectionCode.capability_not_delegated,
            "AstraTool permissions exceed the delegated child action scope.",
        )
    for effect in effect_plan.effects:
        _validate_effect_data_scope(effect, scope)


def _require_effect_values(effect_plan, attribute, allowed, label) -> None:
    values = [
        getattr(effect, attribute).value if hasattr(getattr(effect, attribute), "value") else getattr(effect, attribute)
        for effect in effect_plan.effects
    ]
    if values and not _values_are_subset(values, allowed):
        raise DelegationAuthorizationError(
            DelegationRejectionCode.resource_not_delegated,
            f"AstraTool {label} exceeds the delegated child scope.",
        )


def _validate_effect_data_scope(effect, scope) -> None:
    if effect.data_labels and not _values_are_subset(effect.data_labels, scope.data_labels):
        raise DelegationAuthorizationError(
            DelegationRejectionCode.resource_not_delegated,
            "AstraTool data labels exceed the delegated child scope.",
        )
    if effect.kind.value in {
        "network_write",
        "external_write",
        "network_read",
    } and not _values_are_subset([effect.resource], scope.network_destinations):
        raise DelegationAuthorizationError(
            DelegationRejectionCode.resource_not_delegated,
            "AstraTool network destination exceeds the delegated child scope.",
        )
    workspace_roots = _workspace_roots(effect.kind.value, scope)
    if effect.kind.value.startswith("workspace_") and not _values_are_subset([effect.resource], workspace_roots):
        raise DelegationAuthorizationError(
            DelegationRejectionCode.resource_not_delegated,
            "AstraTool Workspace path exceeds the delegated child roots.",
        )


def _workspace_roots(effect_kind, scope):
    if effect_kind in {"workspace_write", "workspace_delete"}:
        return scope.workspace_write_roots
    if effect_kind == "workspace_read":
        return scope.workspace_read_roots
    return ()


def _unique_strings(values: Iterable[Any]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _values_are_subset(children: Iterable[str], parents: Iterable[str]) -> bool:
    parent_patterns = tuple(str(item) for item in parents)
    return bool(parent_patterns) and all(
        any(pattern == "*" or child == pattern or fnmatchcase(child, pattern) for pattern in parent_patterns)
        for child in children
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value
