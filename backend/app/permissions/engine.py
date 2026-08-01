from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

from app.db.models import ApprovalGrantRecord, utc_now
from app.permissions.effects import is_side_effecting
from app.permissions.governance import PermissionBundleEvaluator
from app.schemas.agent import ExecutionMode
from app.schemas.permissions import (
    ActionEffectPlan,
    PermissionBundle,
    PermissionDecision,
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionRequest,
    PolicyExplanation,
    PolicyMatch,
    PolicyTier,
)

PROTECTED_RESOURCE_PATTERNS = (
    "astra://permission/**",
    "astra://approval/**",
    "astra://audit/**",
    "astra://credential/**",
    "astra://identity/**",
    "astra://runtime/**",
    "astra://sandbox-policy/**",
    "host://docker.sock",
    "host://system/**",
    "task://*/workspace/.astra",
    "task://*/workspace/.astra/**",
    "task://*/workspace/.git",
    "task://*/workspace/.git/**",
    "task://*/workspace/.codex",
    "task://*/workspace/.codex/**",
    "task://*/workspace/**/.astra",
    "task://*/workspace/**/.astra/**",
    "task://*/workspace/**/.git",
    "task://*/workspace/**/.git/**",
    "task://*/workspace/**/.codex",
    "task://*/workspace/**/.codex/**",
)

TIER_ORDER = {
    PolicyTier.platform: 0,
    PolicyTier.managed: 1,
    PolicyTier.deployment: 2,
    PolicyTier.user: 3,
    PolicyTier.task: 4,
    PolicyTier.run: 5,
    PolicyTier.once: 6,
}

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


class LeaseValidator:
    def matches(
        self,
        grant: ApprovalGrantRecord,
        request: PermissionRequest,
        *,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        now = now or utc_now()
        if grant.status != "active" or grant.revoked_at is not None:
            return False, "grant_inactive"
        if grant.expires_at is not None and _as_utc(grant.expires_at) <= _as_utc(now):
            return False, "grant_expired"
        if grant.max_uses is not None and grant.use_count >= grant.max_uses:
            return False, "grant_usage_exhausted"
        if grant.scope == "run" and grant.run_id != request.subject.run_id:
            return False, "grant_run_mismatch"
        if grant.scope == "task" and grant.task_id != request.subject.task_id:
            return False, "grant_task_mismatch"
        if grant.scope not in {"run", "task"}:
            return False, "grant_scope_invalid"
        grant_subject = dict(grant.subject or {})
        if grant.scope == "task":
            grant_subject.pop("run_id", None)
        if not _subject_matches(grant_subject, request):
            return False, "grant_subject_mismatch"
        if grant.effect_kinds:
            requested_effects = set(request.context.get("effect_kinds", []))
            if not requested_effects or not requested_effects <= set(grant.effect_kinds):
                return False, "grant_effect_mismatch"
        if not _resource_matcher_matches(grant.resource_matcher or {}, request.resource):
            return False, "grant_resource_mismatch"
        if not _invocation_matches(grant.invocation_constraints or {}, request):
            return False, "grant_invocation_mismatch"
        return True, "grant_match"


class PermissionEngine:
    def __init__(
        self,
        *,
        protected_resource_patterns: Iterable[str] = PROTECTED_RESOURCE_PATTERNS,
    ):
        self.protected_resource_patterns = tuple(protected_resource_patterns)
        self.lease_validator = LeaseValidator()

    def authorize_request(
        self,
        request: PermissionRequest,
        policies: PermissionPolicySet | None = None,
        grants: Iterable[ApprovalGrantRecord] = (),
        *,
        now: datetime | None = None,
    ) -> PermissionDecision:
        """Authorize a non-tool controlled action through the same policy core."""
        return self.evaluate(
            request,
            policies or PermissionPolicySet(version="runtime", rules=[]),
            grants,
            now=now,
        )

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
        """Authorize a tool invocation through one PermissionRequest pipeline.

        ToolSpec attenuation, platform boundaries, execution mode, leases,
        unattended bundles, and data-flow egress all converge here. Callers
        must only act on the returned aggregate decision.
        """
        now = now or utc_now()
        requests = tuple(
            self._request_for_effect(
                subject=subject,
                effect_plan=effect_plan,
                effect_plan_hash=effect_plan_hash,
                tool_input=tool_input,
                effect=effect,
                provider_id=provider_id,
                schema_digest=schema_digest,
                data_flow=data_flow,
            )
            for effect in effect_plan.effects
        )
        if not requests:
            return self._invocation_result(
                self._decision(
                    PermissionDecisionKind.deny,
                    "empty_effect_plan",
                    "The invocation has no enforceable effect classification.",
                    [],
                    now,
                ),
                requests,
            )

        undeclared = set(effect_plan.required_permissions) - set(declared_permissions)
        if undeclared:
            return self._invocation_result(
                self._decision(
                    PermissionDecisionKind.deny,
                    "tool_permission_violation",
                    "The invocation exceeds the ToolSpec permission ceiling: "
                    + ", ".join(sorted(undeclared)),
                    [],
                    now,
                ),
                requests,
            )

        bundle_allowed, bundle_reason = PermissionBundleEvaluator(
            permission_bundle_signing_secret
        ).validate(
            permission_bundle,
            effect_plan,
            tool_identity=tool_identity,
            unattended=unattended,
            tool_call_count=tool_call_count,
            run_started_at=run_started_at,
            now=now,
        )
        if not bundle_allowed:
            return self._invocation_result(
                self._decision(
                    PermissionDecisionKind.deny,
                    bundle_reason,
                    "The unattended Permission Bundle does not authorize this invocation.",
                    [],
                    now,
                ),
                requests,
            )

        policies = self._invocation_policies(
            requests=requests,
            effect_plan=effect_plan,
            execution_mode=execution_mode,
            once_approved=once_approved,
            data_flow=data_flow,
            base_policies=policies,
            now=now,
        )
        grant_list = tuple(grants)
        decisions = tuple(
            self.evaluate(request, policies, grant_list, now=now) for request in requests
        )
        aggregate = max(
            decisions,
            key=lambda item: DECISION_ORDER[item.decision],
        )
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
                        update={
                            "reason_code": decisive_match.reason_code,
                        }
                    )
                }
            )
        if unattended and aggregate.decision == PermissionDecisionKind.ask:
            aggregate = self._decision(
                PermissionDecisionKind.deny,
                "unattended_approval_unavailable",
                "The unattended Run cannot request interactive approval.",
                aggregate.explanation.matched_policies,
                now,
            )
        grant_ids = {
            decision.explanation.enforced_scope.get("grant_id")
            for decision in decisions
            if decision.explanation.enforced_scope.get("grant_id")
        }
        return InvocationAuthorizationResult(
            decision=aggregate,
            requests=requests,
            decisions=decisions,
            grant_ids=(
                tuple(sorted(grant_ids))
                if aggregate.decision == PermissionDecisionKind.allow
                else ()
            ),
        )

    @staticmethod
    def _request_for_effect(
        *,
        subject: Any,
        effect_plan: ActionEffectPlan,
        effect_plan_hash: str,
        tool_input: dict[str, Any],
        effect: Any,
        provider_id: str | None,
        schema_digest: str | None,
        data_flow: Any | None,
    ) -> PermissionRequest:
        from app.schemas.permissions import PermissionConditions

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
                "effect_kinds": sorted({item.kind.value for item in effect_plan.effects}),
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
        requests: tuple[PermissionRequest, ...],
        effect_plan: ActionEffectPlan,
        execution_mode: ExecutionMode,
        once_approved: bool,
        data_flow: Any | None,
        base_policies: PermissionPolicySet | None,
        now: datetime,
    ) -> PermissionPolicySet:
        from app.schemas.permissions import PermissionRule

        rules: list[PermissionRule] = list(base_policies.rules if base_policies else [])
        if effect_plan.network_scope.get("mode") == "blocked":
            rules.append(
                PermissionRule(
                    id="platform.network.blocked",
                    source="astra.platform",
                    tier=PolicyTier.platform,
                    decision=PermissionDecisionKind.deny,
                    actions=["network_write"],
                    resources=["*"],
                    reason_code="platform_network_denied",
                )
            )

        side_effecting = is_side_effecting(effect_plan)
        if once_approved:
            rules.append(
                PermissionRule(
                    id="once.user-approved",
                    source="user.approval",
                    tier=PolicyTier.once,
                    decision=PermissionDecisionKind.allow,
                    actions=["*"],
                    resources=["*"],
                    reason_code="once_approved",
                )
            )
        elif execution_mode == ExecutionMode.auto_approval or not side_effecting:
            rules.append(
                PermissionRule(
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
                    reason_code=("auto_approval" if side_effecting else "safe_action"),
                )
            )

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
    def _data_flow_rules(
        requests: tuple[PermissionRequest, ...],
        data_flow: Any,
    ) -> list[Any]:
        from app.schemas.permissions import PermissionRule

        external = [
            request for request in requests if request.action in {"network_write", "external_write"}
        ]
        if not external:
            return []
        accumulated_labels = set(getattr(data_flow, "data_labels", []) or [])
        sources = getattr(data_flow, "trust_sources", []) or []
        allowed = getattr(data_flow, "allowed_destinations", []) or []
        prohibited = getattr(data_flow, "prohibited_destinations", []) or []
        rules: list[PermissionRule] = []
        for index, request in enumerate(external):
            labels = accumulated_labels | set(request.conditions.data_labels)
            destination = request.conditions.network_destination or request.resource
            if any(fnmatchcase(destination, pattern) for pattern in prohibited):
                rules.append(
                    PermissionRule(
                        id=f"data-flow.prohibited.{index}",
                        source="run.data_flow",
                        tier=PolicyTier.run,
                        decision=PermissionDecisionKind.deny,
                        actions=[request.action],
                        resources=[request.resource],
                        reason_code="data_egress_prohibited",
                    )
                )
                continue
            destination_allowed = any(fnmatchcase(destination, pattern) for pattern in allowed)
            sensitive = bool(labels & SENSITIVE_DATA_LABELS)
            if sensitive and not destination_allowed:
                rules.append(
                    PermissionRule(
                        id=f"data-flow.sensitive.{index}",
                        source="run.data_flow",
                        tier=PolicyTier.run,
                        decision=PermissionDecisionKind.deny,
                        actions=[request.action],
                        resources=[request.resource],
                        reason_code="sensitive_data_egress_denied",
                    )
                )
            elif (
                any(source.startswith(("workspace:", "web:", "external:")) for source in sources)
                and not destination_allowed
            ):
                rules.append(
                    PermissionRule(
                        id=f"data-flow.untrusted.{index}",
                        source="run.data_flow",
                        tier=PolicyTier.run,
                        decision=PermissionDecisionKind.ask,
                        actions=[request.action],
                        resources=[request.resource],
                        reason_code="untrusted_data_external_write",
                    )
                )
        return rules

    @staticmethod
    def _invocation_result(
        decision: PermissionDecision,
        requests: tuple[PermissionRequest, ...],
    ) -> InvocationAuthorizationResult:
        return InvocationAuthorizationResult(
            decision=decision,
            requests=requests,
            decisions=(decision,),
        )

    def evaluate(
        self,
        request: PermissionRequest,
        policies: PermissionPolicySet,
        grants: Iterable[ApprovalGrantRecord] = (),
        *,
        now: datetime | None = None,
    ) -> PermissionDecision:
        now = now or utc_now()
        protected_pattern = next(
            (
                pattern
                for pattern in self.protected_resource_patterns
                if fnmatchcase(request.resource, pattern)
            ),
            None,
        )
        if protected_pattern is not None and _is_mutating_action(request.action):
            return PermissionDecision(
                decision=PermissionDecisionKind.deny,
                explanation=PolicyExplanation(
                    reason_code="protected_resource",
                    summary="The requested action targets a protected control-plane resource.",
                    enforced_scope={"resource_pattern": protected_pattern},
                    trace=["platform protected-resource guard denied the request"],
                ),
                decided_at=now,
            )

        matches = [rule for rule in policies.rules if _rule_matches(rule, request, now=now)]
        matches.sort(key=lambda rule: (TIER_ORDER[rule.tier], rule.id))
        policy_matches = [
            PolicyMatch(
                policy_id=rule.id,
                source=rule.source,
                tier=rule.tier.value,
                decision=rule.decision,
                reason_code=rule.reason_code,
                constraints=rule.conditions,
            )
            for rule in matches
        ]
        strongest = max(
            (rule.decision for rule in matches),
            key=lambda decision: DECISION_ORDER[decision],
            default=None,
        )
        if strongest == PermissionDecisionKind.deny:
            return self._decision(
                PermissionDecisionKind.deny,
                "policy_denied",
                "A matching policy denied the action.",
                policy_matches,
                now,
            )
        if strongest == PermissionDecisionKind.ask:
            return self._decision(
                PermissionDecisionKind.ask,
                "policy_requires_approval",
                "A matching policy requires explicit approval.",
                policy_matches,
                now,
            )
        if strongest == PermissionDecisionKind.allow:
            return self._decision(
                PermissionDecisionKind.allow,
                "policy_allowed",
                "The action is allowed by the effective policy.",
                policy_matches,
                now,
            )

        lease_reasons: list[str] = []
        for grant in grants:
            matched, reason = self.lease_validator.matches(grant, request, now=now)
            lease_reasons.append(f"{grant.id}:{reason}")
            if matched:
                return PermissionDecision(
                    decision=PermissionDecisionKind.allow,
                    explanation=PolicyExplanation(
                        reason_code="permission_lease",
                        summary="A scoped permission lease allows the action.",
                        matched_policies=policy_matches,
                        enforced_scope={
                            "grant_id": grant.id,
                            "scope": grant.scope,
                            "resource_matcher": grant.resource_matcher,
                            "effect_kinds": grant.effect_kinds,
                        },
                        trace=lease_reasons,
                    ),
                    decided_at=now,
                )
        return PermissionDecision(
            decision=PermissionDecisionKind.ask,
            explanation=PolicyExplanation(
                reason_code="default_ask",
                summary="No policy or active permission lease allows the action.",
                matched_policies=policy_matches,
                trace=lease_reasons or ["no matching policy or permission lease"],
            ),
            decided_at=now,
        )

    @staticmethod
    def _decision(
        decision: PermissionDecisionKind,
        reason_code: str,
        summary: str,
        matches: list[PolicyMatch],
        now: datetime,
    ) -> PermissionDecision:
        return PermissionDecision(
            decision=decision,
            explanation=PolicyExplanation(
                reason_code=reason_code,
                summary=summary,
                matched_policies=matches,
                trace=[
                    f"{match.tier}:{match.policy_id}:{match.decision.value}" for match in matches
                ],
            ),
            decided_at=now,
        )


def _rule_matches(rule: Any, request: PermissionRequest, *, now: datetime) -> bool:
    if not rule.enabled:
        return False
    if rule.expires_at is not None and _as_utc(rule.expires_at) <= _as_utc(now):
        return False
    if not any(fnmatchcase(request.action, pattern) for pattern in rule.actions):
        return False
    if not any(fnmatchcase(request.resource, pattern) for pattern in rule.resources):
        return False
    values = request.conditions.model_dump(mode="json")
    for key, expected in rule.conditions.items():
        actual = values.get(key, request.context.get(key))
        if isinstance(expected, list):
            if isinstance(actual, list):
                if not set(expected) <= set(actual):
                    return False
            elif actual not in expected:
                return False
        elif expected != actual:
            return False
    return True


def _subject_matches(subject: dict[str, Any], request: PermissionRequest) -> bool:
    for key in ("agent_id", "user_id", "task_id", "run_id"):
        expected = subject.get(key)
        if expected is not None and expected != getattr(request.subject, key):
            return False
    return True


def _resource_matcher_matches(matcher: dict[str, Any], resource: str) -> bool:
    if not matcher:
        return False
    if "exact" in matcher:
        return resource == matcher["exact"]
    if "prefix" in matcher:
        return resource.startswith(matcher["prefix"])
    if "glob" in matcher:
        return fnmatchcase(resource, matcher["glob"])
    if "globs" in matcher:
        return any(fnmatchcase(resource, pattern) for pattern in matcher["globs"])
    return False


def _invocation_matches(constraints: dict[str, Any], request: PermissionRequest) -> bool:
    if not constraints:
        return True
    condition_values = request.conditions.model_dump(mode="json")
    for key in (
        "tool_name",
        "tool_version",
        "provider_id",
        "schema_digest",
        "analyzer_version",
        "working_directory",
        "network_destination",
    ):
        expected = constraints.get(key)
        if expected is not None and condition_values.get(key) != expected:
            return False
    expected_analyzer_digest = constraints.get("analyzer_digest")
    if (
        expected_analyzer_digest is not None
        and request.context.get("analyzer_digest") != expected_analyzer_digest
    ):
        return False
    kind = constraints.get("kind")
    tool_input = request.context.get("tool_input", {})
    if kind == "exact_args":
        return tool_input == constraints.get("input")
    if kind == "command_prefix":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return False
        tokens = command.split()
        expected_tokens = constraints.get("tokens", [])
        return tokens[: len(expected_tokens)] == expected_tokens
    return True


def _is_mutating_action(action: str) -> bool:
    return any(
        marker in action
        for marker in ("write", "delete", "modify", "create", "grant", "revoke", "execute")
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
