from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any

from app.application.permissions.effects import is_side_effecting
from app.application.permissions.governance import PermissionBundleEvaluator
from app.common.schemas.agent.types import ExecutionMode
from app.common.schemas.permissions import (
    ActionEffectPlan,
    PermissionBundle,
    PermissionConditions,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionRequest,
    PermissionRule,
    PolicyTier,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.permissions import ApprovalGrantRecord

DECISION_ORDER = {
    PermissionDecisionKind.allow: 0,
    PermissionDecisionKind.ask: 1,
    PermissionDecisionKind.deny: 2,
}

SENSITIVE_DATA_LABELS = {
    "sensitive",
    "confidential",
    "secret",
    "credential",
    "personal",
    "financial",
    "private",
    "source_code",
}


@dataclass(frozen=True)
class InvocationAuthorizationResult:
    """The single runtime authorization outcome for one frozen invocation."""

    decision: PermissionDecision
    requests: tuple[PermissionRequest, ...]
    decisions: tuple[PermissionDecision, ...]
    grant_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvocationAuthorizationCommand:
    subject: Any
    effect_plan: ActionEffectPlan
    effect_plan_hash: str
    tool_input: dict[str, Any]
    declared_permissions: tuple[str, ...]
    execution_mode: ExecutionMode
    policies: PermissionPolicySet | None
    grants: tuple[ApprovalGrantRecord, ...]
    provider_id: str | None
    schema_digest: str | None
    once_approved: bool
    data_flow: Any | None
    permission_bundle: PermissionBundle | None
    permission_bundle_signing_secret: str
    unattended: bool
    tool_identity: str
    tool_call_count: int
    run_started_at: datetime | None
    now: datetime


class InvocationAuthorizationMixin:
    def authorize_invocation(
        self,
        *,
        subject: Any,
        effect_plan: ActionEffectPlan,
        effect_plan_hash: str,
        tool_input: dict[str, Any],
        declared_permissions: Iterable[str],
        execution_mode: ExecutionMode,
        policies: PermissionPolicySet | None = None,
        grants: Iterable[ApprovalGrantRecord] = (),
        provider_id: str | None = None,
        schema_digest: str | None = None,
        once_approved: bool = False,
        data_flow: Any | None = None,
        permission_bundle: PermissionBundle | None = None,
        permission_bundle_signing_secret: str = "",
        unattended: bool = False,
        tool_identity: str = "",
        tool_call_count: int = 0,
        run_started_at: datetime | None = None,
        now: datetime | None = None,
    ) -> InvocationAuthorizationResult:
        return self._authorize_command(
            InvocationAuthorizationCommand(
                subject=subject,
                effect_plan=effect_plan,
                effect_plan_hash=effect_plan_hash,
                tool_input=tool_input,
                declared_permissions=tuple(declared_permissions),
                execution_mode=execution_mode,
                policies=policies,
                grants=tuple(grants),
                provider_id=provider_id,
                schema_digest=schema_digest,
                once_approved=once_approved,
                data_flow=data_flow,
                permission_bundle=permission_bundle,
                permission_bundle_signing_secret=permission_bundle_signing_secret,
                unattended=unattended,
                tool_identity=tool_identity,
                tool_call_count=tool_call_count,
                run_started_at=run_started_at,
                now=now or utc_now(),
            )
        )

    def _authorize_command(
        self, command: InvocationAuthorizationCommand
    ) -> InvocationAuthorizationResult:
        requests = self._requests_for_plan(
            command.subject,
            command.effect_plan,
            command.effect_plan_hash,
            command.tool_input,
            command.provider_id,
            command.schema_digest,
            command.data_flow,
        )
        denied = self._preflight_decision(
            requests,
            command.effect_plan,
            command.declared_permissions,
            permission_bundle=command.permission_bundle,
            permission_bundle_signing_secret=command.permission_bundle_signing_secret,
            unattended=command.unattended,
            tool_identity=command.tool_identity,
            tool_call_count=command.tool_call_count,
            run_started_at=command.run_started_at,
            now=command.now,
        )
        if denied is not None:
            return self._invocation_result(denied, requests)
        effective_policies = self._invocation_policies(
            requests=requests,
            effect_plan=command.effect_plan,
            execution_mode=command.execution_mode,
            once_approved=command.once_approved,
            data_flow=command.data_flow,
            base_policies=command.policies,
            now=command.now,
        )
        decisions = tuple(
            self.evaluate(request, effective_policies, command.grants, now=command.now)
            for request in requests
        )
        aggregate = self._aggregate_decision(
            decisions, unattended=command.unattended, now=command.now
        )
        return InvocationAuthorizationResult(
            decision=aggregate,
            requests=requests,
            decisions=decisions,
            grant_ids=self._allowed_grant_ids(aggregate, decisions),
        )

    def _requests_for_plan(
        self, subject, plan, plan_hash, tool_input, provider_id, schema_digest, data_flow
    ) -> tuple[PermissionRequest, ...]:
        return tuple(
            self._request_for_effect(
                subject=subject,
                effect_plan=plan,
                effect_plan_hash=plan_hash,
                tool_input=tool_input,
                effect=effect,
                provider_id=provider_id,
                schema_digest=schema_digest,
                data_flow=data_flow,
            )
            for effect in plan.effects
        )

    def _preflight_decision(
        self,
        requests,
        effect_plan,
        declared_permissions,
        *,
        permission_bundle,
        permission_bundle_signing_secret,
        unattended,
        tool_identity,
        tool_call_count,
        run_started_at,
        now,
    ) -> PermissionDecision | None:
        if not requests:
            return self._decision(
                PermissionDecisionKind.deny,
                "empty_effect_plan",
                "The invocation has no enforceable effect classification.",
                [],
                now,
            )
        undeclared = set(effect_plan.required_permissions) - set(declared_permissions)
        if undeclared:
            return self._decision(
                PermissionDecisionKind.deny,
                "tool_permission_violation",
                "The invocation exceeds the ToolSpec permission ceiling: "
                + ", ".join(sorted(undeclared)),
                [],
                now,
            )
        allowed, reason = PermissionBundleEvaluator(permission_bundle_signing_secret).validate(
            permission_bundle,
            effect_plan,
            tool_identity=tool_identity,
            unattended=unattended,
            tool_call_count=tool_call_count,
            run_started_at=run_started_at,
            now=now,
        )
        if allowed:
            return None
        return self._decision(
            PermissionDecisionKind.deny,
            reason,
            "The unattended Permission Bundle does not authorize this invocation.",
            [],
            now,
        )

    @staticmethod
    def _request_for_effect(
        *,
        subject,
        effect_plan,
        effect_plan_hash,
        tool_input,
        effect,
        provider_id,
        schema_digest,
        data_flow,
    ) -> PermissionRequest:
        return PermissionRequest(
            subject=subject,
            action=effect.kind.value,
            resource=effect.resource,
            conditions=PermissionConditions(
                tool_name=effect_plan.tool_name,
                tool_version=effect_plan.tool_version,
                provider_id=provider_id,
                schema_digest=schema_digest,
                analyzer_version=effect_plan.analyzer_version,
                working_directory=effect_plan.cwd,
                network_destination=(
                    effect.resource
                    if effect.kind.value in {"network_write", "external_write"}
                    else None
                ),
                data_labels=sorted(
                    set(effect.data_labels) | set(getattr(data_flow, "data_labels", []) or [])
                ),
            ),
            effect_plan_hash=effect_plan_hash,
            context={
                "effect_kinds": sorted(item.kind.value for item in effect_plan.effects),
                "tool_input": tool_input,
                "analyzer_digest": effect_plan.analyzer_digest,
                "persistent": effect.persistent,
                "risk": effect.risk,
                "trust_sources": list(getattr(data_flow, "trust_sources", []) or []),
            },
        )

    def _invocation_policies(
        self,
        *,
        requests,
        effect_plan,
        execution_mode,
        once_approved,
        data_flow,
        base_policies,
        now,
    ) -> PermissionPolicySet:
        rules = list(base_policies.rules if base_policies else [])
        if effect_plan.network_scope.get("mode") == "blocked":
            rules.append(self._network_block_rule())
        side_effecting = is_side_effecting(effect_plan)
        mode_rule = self._execution_mode_rule(
            execution_mode, once_approved=once_approved, side_effecting=side_effecting
        )
        if mode_rule is not None:
            rules.append(mode_rule)
        if data_flow is not None:
            rules.extend(self._data_flow_rules(requests, data_flow))
        return PermissionPolicySet(
            version=(
                f"{base_policies.version}+runtime:{int(now.timestamp())}"
                if base_policies
                else f"runtime:{int(now.timestamp())}"
            ),
            rules=rules,
            source_digests=dict(base_policies.source_digests if base_policies else {}),
        )

    @staticmethod
    def _network_block_rule() -> PermissionRule:
        return PermissionRule(
            id="platform.network.blocked",
            source="astra.platform",
            tier=PolicyTier.platform,
            decision=PermissionDecisionKind.deny,
            actions=["network_write"],
            resources=["*"],
            reason_code="platform_network_denied",
        )

    @staticmethod
    def _execution_mode_rule(execution_mode, *, once_approved, side_effecting):
        if once_approved:
            return PermissionRule(
                id="once.user-approved",
                source="user.approval",
                tier=PolicyTier.once,
                decision=PermissionDecisionKind.allow,
                actions=["*"],
                resources=["*"],
                reason_code="once_approved",
            )
        if execution_mode != ExecutionMode.auto_approval and side_effecting:
            return None
        return PermissionRule(
            id=(
                "run.mode.auto-approval"
                if execution_mode == ExecutionMode.auto_approval
                else "platform.safe-action"
            ),
            source="run.execution_mode",
            tier=PolicyTier.run,
            decision=PermissionDecisionKind.allow,
            actions=["*"],
            resources=["*"],
            reason_code="auto_approval" if side_effecting else "safe_action",
        )

    @staticmethod
    def _data_flow_rules(requests, data_flow) -> list[PermissionRule]:
        external = [
            request for request in requests if request.action in {"network_write", "external_write"}
        ]
        rules = []
        for index, request in enumerate(external):
            rule = _data_flow_rule(index, request, data_flow)
            if rule is not None:
                rules.append(rule)
        return rules

    def _aggregate_decision(self, decisions, *, unattended, now):
        aggregate = max(decisions, key=lambda item: DECISION_ORDER[item.decision])
        decisive_match = next(
            (
                match
                for match in aggregate.explanation.matched_policies
                if match.decision == aggregate.decision
            ),
            None,
        )
        if decisive_match is not None:
            aggregate = aggregate.model_copy(
                update={
                    "explanation": aggregate.explanation.model_copy(
                        update={"reason_code": decisive_match.reason_code}
                    )
                }
            )
        if unattended and aggregate.decision == PermissionDecisionKind.ask:
            return self._decision(
                PermissionDecisionKind.deny,
                "unattended_approval_unavailable",
                "The unattended Run cannot request interactive approval.",
                aggregate.explanation.matched_policies,
                now,
            )
        return aggregate

    @staticmethod
    def _allowed_grant_ids(aggregate, decisions) -> tuple[str, ...]:
        if aggregate.decision != PermissionDecisionKind.allow:
            return ()
        grant_ids = {
            decision.explanation.enforced_scope.get("grant_id")
            for decision in decisions
            if decision.explanation.enforced_scope.get("grant_id")
        }
        return tuple(sorted(grant_ids))

    @staticmethod
    def _invocation_result(decision, requests) -> InvocationAuthorizationResult:
        return InvocationAuthorizationResult(
            decision=decision, requests=requests, decisions=(decision,)
        )


def _data_flow_rule(index, request, data_flow) -> PermissionRule | None:
    labels = set(getattr(data_flow, "data_labels", []) or []) | set(request.conditions.data_labels)
    destination = request.conditions.network_destination or request.resource
    prohibited = getattr(data_flow, "prohibited_destinations", []) or []
    allowed = getattr(data_flow, "allowed_destinations", []) or []
    sources = getattr(data_flow, "trust_sources", []) or []
    outcome = _data_flow_outcome(
        destination,
        labels=labels,
        sources=sources,
        allowed=allowed,
        prohibited=prohibited,
    )
    if outcome is None:
        return None
    decision, reason = outcome
    return PermissionRule(
        id=f"data-flow.{reason}.{index}",
        source="run.data_flow",
        tier=PolicyTier.run,
        decision=decision,
        actions=[request.action],
        resources=[request.resource],
        reason_code=reason,
    )


def _data_flow_outcome(destination, *, labels, sources, allowed, prohibited):
    destination_allowed = any(fnmatchcase(destination, pattern) for pattern in allowed)
    if any(fnmatchcase(destination, pattern) for pattern in prohibited):
        return PermissionDecisionKind.deny, "data_egress_prohibited"
    if labels & SENSITIVE_DATA_LABELS and not destination_allowed:
        return PermissionDecisionKind.deny, "sensitive_data_egress_denied"
    untrusted = any(source.startswith(("workspace:", "web:", "external:")) for source in sources)
    if untrusted and not destination_allowed:
        return PermissionDecisionKind.ask, "untrusted_data_external_write"
    return None
