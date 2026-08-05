from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.infrastructure.tools.base import AstraToolSpec
from app.infrastructure.tools.router import ToolRouter

_SUCCESS_STATUSES = frozenset({"completed", "succeeded", "success"})


def task_capability_catalog(specs: Mapping[str, AstraToolSpec]) -> set[str]:
    """Return the provider-neutral abilities that planning is allowed to reference."""
    return {
        capability for spec in specs.values() for capability in spec.task_capabilities if capability
    }


def forbidden_plan_bindings(specs: Mapping[str, AstraToolSpec]) -> set[str]:
    """Return concrete/runtime identities that must not leak into new Plans."""
    semantic = task_capability_catalog(specs)
    bindings = set(specs)
    for spec in specs.values():
        bindings.update(spec.capabilities)
        bindings.update(spec.permissions)
        bindings.update(
            {
                spec.permission,
                spec.provider_id,
                spec.execution_backend,
            }
        )
    return {binding for binding in bindings if binding and binding not in semantic}


def _normalized_values(values: Iterable[str] | str | None) -> tuple[str, ...]:
    if values is None:
        return ()
    source = (values,) if isinstance(values, str) else values
    return tuple(sorted({str(value).strip() for value in source if str(value).strip()}))


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _candidate_rejection(
    tool_name, spec, required, matched, excluded, require_read_only, require_idempotent
):
    if required and not matched:
        return "not_matched"
    if tool_name in excluded:
        return "excluded"
    if require_read_only and spec.side_effect_level != "read_only":
        return "side_effect_not_read_only"
    if require_idempotent and not spec.idempotent:
        return "non_idempotent"
    return None


@dataclass(frozen=True)
class CapabilityToolCandidate:
    tool_name: str
    matched_capabilities: tuple[str, ...]
    task_capabilities: tuple[str, ...]
    tool_version: str
    provider_id: str
    provider_digest: str
    spec: AstraToolSpec = field(repr=False, compare=False)


@dataclass(frozen=True)
class CapabilityToolRejection:
    tool_name: str
    reason: str
    matched_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityToolResolution:
    candidates: tuple[CapabilityToolCandidate, ...]
    required_capabilities: tuple[str, ...]
    satisfied_capabilities: tuple[str, ...]
    unresolved_capabilities: tuple[str, ...]
    capability_gaps: tuple[str, ...]
    plan_node_id: str | None
    require_read_only: bool
    require_idempotent: bool
    excluded_tools: tuple[str, ...]
    rejections: tuple[CapabilityToolRejection, ...] = ()

    @property
    def candidate_names(self) -> tuple[str, ...]:
        return tuple(candidate.tool_name for candidate in self.candidates)

    def audit_payload(self) -> dict[str, Any]:
        """Return a bounded payload containing identities and safe reason codes only."""
        return {
            "plan_node_id": self.plan_node_id,
            "required_capabilities": list(self.required_capabilities),
            "satisfied_capabilities": list(self.satisfied_capabilities),
            "unresolved_capabilities": list(self.unresolved_capabilities),
            "capability_gaps": list(self.capability_gaps),
            "candidate_names": list(self.candidate_names),
            "candidates": [
                {
                    "tool_name": candidate.tool_name,
                    "tool_version": candidate.tool_version,
                    "provider_id": candidate.provider_id,
                    "provider_digest": candidate.provider_digest,
                    "matched_capabilities": list(candidate.matched_capabilities),
                }
                for candidate in self.candidates
            ],
            "constraints": {
                "require_read_only": self.require_read_only,
                "require_idempotent": self.require_idempotent,
                "excluded_tools": list(self.excluded_tools),
            },
            "rejections": [
                {
                    "tool_name": rejection.tool_name,
                    "reason": rejection.reason,
                    "matched_capabilities": list(rejection.matched_capabilities),
                }
                for rejection in self.rejections
            ],
        }


class CapabilityToolResolver:
    """Resolve provider-neutral task capabilities to currently eligible tools.

    ``ToolRouter`` remains the security, permission, risk, and backend gate. This
    layer only narrows its eligible manifest set using semantic task abilities
    and execution safety constraints.
    """

    def __init__(self, router: ToolRouter):
        self.router = router

    def resolve(
        self,
        required_capabilities: Iterable[str] | str,
        *,
        observations: Iterable[Any] = (),
        plan_node_id: str | None = None,
        require_read_only: bool = False,
        require_idempotent: bool = False,
        excluded_tools: Iterable[str] | str | None = None,
    ) -> CapabilityToolResolution:
        required = _normalized_values(required_capabilities)
        excluded = frozenset(_normalized_values(excluded_tools))
        all_specs = self.router.registry.specs()
        eligible_specs, unavailable = self.router.eligible_specs()
        satisfied = self._satisfied_capabilities(
            required,
            observations,
            plan_node_id=plan_node_id,
            specs=all_specs,
        )
        unresolved = tuple(sorted(set(required) - set(satisfied)))
        unresolved_set = frozenset(unresolved)

        candidates, rejections = self._eligible_candidates(
            eligible_specs,
            required,
            unresolved_set,
            excluded,
            require_read_only,
            require_idempotent,
        )
        rejections.extend(
            self._unavailable_rejections(unavailable, all_specs, required, unresolved_set)
        )
        self._sort_resolution(candidates, rejections, bool(required))
        covered = {
            capability for candidate in candidates for capability in candidate.matched_capabilities
        }
        gaps = tuple(sorted(unresolved_set - covered))
        return CapabilityToolResolution(
            candidates=tuple(candidates),
            required_capabilities=required,
            satisfied_capabilities=satisfied,
            unresolved_capabilities=unresolved,
            capability_gaps=gaps,
            plan_node_id=plan_node_id,
            require_read_only=require_read_only,
            require_idempotent=require_idempotent,
            excluded_tools=tuple(sorted(excluded)),
            rejections=tuple(rejections),
        )

    def _eligible_candidates(
        self, specs, required, unresolved, excluded, require_read_only, require_idempotent
    ):
        candidates, rejections = [], []
        for tool_name, spec in specs.items():
            matched = self._matched_capabilities(
                tool_name, spec, required=required, unresolved=unresolved
            )
            reason = _candidate_rejection(
                tool_name,
                spec,
                required,
                matched,
                excluded,
                require_read_only,
                require_idempotent,
            )
            if reason == "not_matched":
                continue
            if reason:
                rejections.append(CapabilityToolRejection(tool_name, reason, matched))
                continue
            candidates.append(
                CapabilityToolCandidate(
                    tool_name=tool_name,
                    matched_capabilities=matched,
                    task_capabilities=_normalized_values(spec.task_capabilities),
                    tool_version=spec.version,
                    provider_id=spec.provider_id,
                    provider_digest=spec.provider_digest,
                    spec=spec,
                )
            )
        return candidates, rejections

    def _unavailable_rejections(self, unavailable, specs, required, unresolved):
        rejections = []
        for tool_name, availability in unavailable.items():
            spec = specs.get(tool_name)
            if spec is None:
                continue
            matched = self._matched_capabilities(
                tool_name, spec, required=required, unresolved=unresolved
            )
            if required and not matched:
                continue
            reason = str(availability.get("reason") or "unavailable")
            rejections.append(CapabilityToolRejection(tool_name, reason, matched))
        return rejections

    @staticmethod
    def _sort_resolution(candidates, rejections, required) -> None:
        side_effect_rank = {
            "read_only": 0,
            "temporary": 1,
            "workspace_write": 2,
            "external_write": 3,
            "destructive": 4,
        }
        risk_rank = {"low": 0, "sandboxed": 1, "high": 2}
        candidates.sort(
            key=lambda item: (
                -len(item.matched_capabilities) if required else 0,
                side_effect_rank.get(item.spec.side_effect_level, 99),
                risk_rank.get(item.spec.risk, 99),
                item.provider_id,
                item.tool_name,
                item.tool_version,
                item.provider_digest,
            )
        )
        rejections.sort(
            key=lambda item: (
                item.tool_name,
                item.reason,
                item.matched_capabilities,
            )
        )

    @staticmethod
    def _matched_capabilities(
        tool_name: str,
        spec: AstraToolSpec,
        *,
        required: tuple[str, ...],
        unresolved: frozenset[str],
    ) -> tuple[str, ...]:
        if not required:
            return _normalized_values(spec.task_capabilities)
        matched = set(spec.task_capabilities) & unresolved
        return tuple(sorted(matched))

    @staticmethod
    def _satisfied_capabilities(
        required: tuple[str, ...],
        observations: Iterable[Any],
        *,
        plan_node_id: str | None,
        specs: Mapping[str, AstraToolSpec],
    ) -> tuple[str, ...]:
        if not required:
            return ()
        satisfied: set[str] = set()
        required_set = set(required)
        for raw_observation in observations:
            observation = _as_mapping(raw_observation)
            if observation is None:
                continue
            if str(observation.get("status") or "").casefold() not in _SUCCESS_STATUSES:
                continue
            data = _as_mapping(observation.get("data")) or {}
            observation_node_id = observation.get("plan_node_id") or data.get("plan_node_id")
            if plan_node_id is not None and str(observation_node_id or "") != plan_node_id:
                continue
            tool_name = str(
                observation.get("tool_name")
                or observation.get("selected_tool")
                or data.get("tool_name")
                or ""
            ).strip()
            spec = specs.get(tool_name)
            if spec is None:
                continue
            satisfied.update(required_set & set(spec.task_capabilities))
        return tuple(sorted(satisfied))
