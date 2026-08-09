from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

from app.application.permissions.invocation import InvocationAuthorizationMixin
from app.common.schemas.permissions import (
    PermissionDecision,
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionRequest,
    PolicyExplanation,
    PolicyMatch,
    PolicyTier,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.permissions import ApprovalGrantRecord

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


def _lease_matches(
    grant: ApprovalGrantRecord,
    request: PermissionRequest,
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    now = now or utc_now()
    invalid_reason = _grant_state_reason(grant, now) or _grant_scope_reason(grant, request)
    if invalid_reason:
        return False, invalid_reason
    grant_subject = dict(grant.subject or {})
    if grant.scope == "task":
        grant_subject.pop("run_id", None)
    if not _subject_matches(grant_subject, request):
        return False, "grant_subject_mismatch"
    if not _grant_effects_match(grant, request):
        return False, "grant_effect_mismatch"
    if not _resource_matcher_matches(grant.resource_matcher or {}, request.resource):
        return False, "grant_resource_mismatch"
    if not _invocation_matches(grant.invocation_constraints or {}, request):
        return False, "grant_invocation_mismatch"
    return True, "grant_match"


def _grant_state_reason(grant, now) -> str | None:
    if grant.status != "active" or grant.revoked_at is not None:
        return "grant_inactive"
    if grant.expires_at is not None and _as_utc(grant.expires_at) <= _as_utc(now):
        return "grant_expired"
    if grant.max_uses is not None and grant.use_count >= grant.max_uses:
        return "grant_usage_exhausted"
    return None


def _grant_scope_reason(grant, request) -> str | None:
    if grant.scope not in {"run", "task"}:
        return "grant_scope_invalid"
    if grant.scope == "run" and grant.run_id != request.subject.run_id:
        return "grant_run_mismatch"
    if grant.scope == "task" and grant.task_id != request.subject.task_id:
        return "grant_task_mismatch"
    return None


def _grant_effects_match(grant, request) -> bool:
    if not grant.effect_kinds:
        return True
    requested = set(request.context.get("effect_kinds", []))
    return bool(requested) and requested <= set(grant.effect_kinds)


class PermissionEngine(InvocationAuthorizationMixin):
    def __init__(
        self,
        *,
        protected_resource_patterns: Iterable[str] = PROTECTED_RESOURCE_PATTERNS,
    ):
        self.protected_resource_patterns = tuple(protected_resource_patterns)

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

    def evaluate(
        self,
        request: PermissionRequest,
        policies: PermissionPolicySet,
        grants: Iterable[ApprovalGrantRecord] = (),
        *,
        now: datetime | None = None,
    ) -> PermissionDecision:
        now = now or utc_now()
        protected = self._protected_resource_decision(request, now)
        if protected:
            return protected
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
        policy_decision = self._matching_policy_decision(strongest, policy_matches, now)
        return policy_decision or self._lease_decision(request, grants, policy_matches, now)

    def _protected_resource_decision(self, request, now):
        pattern = next(
            (candidate for candidate in self.protected_resource_patterns if fnmatchcase(request.resource, candidate)),
            None,
        )
        if pattern is None or not _is_mutating_action(request.action):
            return None
        explanation = PolicyExplanation(
            reason_code="protected_resource",
            summary="The requested action targets a protected control-plane resource.",
            enforced_scope={"resource_pattern": pattern},
            trace=["platform protected-resource guard denied the request"],
        )
        return PermissionDecision(decision=PermissionDecisionKind.deny, explanation=explanation, decided_at=now)

    def _matching_policy_decision(self, strongest, matches, now):
        messages = {
            PermissionDecisionKind.deny: ("policy_denied", "A matching policy denied the action."),
            PermissionDecisionKind.ask: ("policy_requires_approval", "A matching policy requires explicit approval."),
            PermissionDecisionKind.allow: ("policy_allowed", "The action is allowed by the effective policy."),
        }
        if strongest is None:
            return None
        reason_code, summary = messages[strongest]
        return self._decision(strongest, reason_code, summary, matches, now)

    def _lease_decision(self, request, grants, policy_matches, now):
        reasons = []
        for grant in grants:
            matched, reason = _lease_matches(grant, request, now=now)
            reasons.append(f"{grant.id}:{reason}")
            if matched:
                explanation = PolicyExplanation(
                    reason_code="permission_lease",
                    summary="A scoped permission lease allows the action.",
                    matched_policies=policy_matches,
                    enforced_scope={
                        "grant_id": grant.id,
                        "scope": grant.scope,
                        "resource_matcher": grant.resource_matcher,
                        "effect_kinds": grant.effect_kinds,
                    },
                    trace=reasons,
                )
                return PermissionDecision(
                    decision=PermissionDecisionKind.allow,
                    explanation=explanation,
                    decided_at=now,
                )
        explanation = PolicyExplanation(
            reason_code="default_ask",
            summary="No policy or active permission lease allows the action.",
            matched_policies=policy_matches,
            trace=reasons or ["no matching policy or permission lease"],
        )
        return PermissionDecision(decision=PermissionDecisionKind.ask, explanation=explanation, decided_at=now)

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
                trace=[f"{match.tier}:{match.policy_id}:{match.decision.value}" for match in matches],
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
    if expected_analyzer_digest is not None and request.context.get("analyzer_digest") != expected_analyzer_digest:
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
    return any(marker in action for marker in ("write", "delete", "modify", "create", "grant", "revoke", "execute"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
