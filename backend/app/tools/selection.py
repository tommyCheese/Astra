from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.tools.base import ToolSpec
from app.tools.router import ToolRouter

_SUCCESS_STATUSES = frozenset({"completed", "succeeded", "success"})


def task_capability_catalog(specs: Mapping[str, ToolSpec]) -> set[str]:
    """Return the provider-neutral abilities that planning is allowed to reference."""
    return {
        capability for spec in specs.values() for capability in spec.task_capabilities if capability
    }


def forbidden_plan_bindings(specs: Mapping[str, ToolSpec]) -> set[str]:
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


@dataclass(frozen=True)
class CapabilityToolCandidate:
    tool_name: str
    matched_capabilities: tuple[str, ...]
    task_capabilities: tuple[str, ...]
    tool_version: str
    provider_id: str
    provider_digest: str
    spec: ToolSpec = field(repr=False, compare=False)


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
    legacy_tool_binding: bool
    legacy_tool_names: tuple[str, ...]
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
            "legacy_tool_binding": self.legacy_tool_binding,
            "legacy_tool_names": list(self.legacy_tool_names),
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
        all_tool_names = frozenset(all_specs)
        legacy_names = tuple(sorted(set(required) & all_tool_names))
        legacy_name_set = frozenset(legacy_names)

        satisfied = self._satisfied_capabilities(
            required,
            observations,
            plan_node_id=plan_node_id,
            specs=all_specs,
            legacy_names=legacy_name_set,
        )
        unresolved = tuple(sorted(set(required) - set(satisfied)))
        unresolved_set = frozenset(unresolved)

        candidates: list[CapabilityToolCandidate] = []
        rejections: list[CapabilityToolRejection] = []
        for tool_name, spec in eligible_specs.items():
            matched = self._matched_capabilities(
                tool_name,
                spec,
                required=required,
                unresolved=unresolved_set,
                legacy_names=legacy_name_set,
            )
            if required and not matched:
                continue
            if tool_name in excluded:
                rejections.append(CapabilityToolRejection(tool_name, "excluded", matched))
                continue
            if require_read_only and spec.side_effect_level != "read_only":
                rejections.append(
                    CapabilityToolRejection(tool_name, "side_effect_not_read_only", matched)
                )
                continue
            if require_idempotent and not spec.idempotent:
                rejections.append(CapabilityToolRejection(tool_name, "non_idempotent", matched))
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

        for tool_name, availability in unavailable.items():
            spec = all_specs.get(tool_name)
            if spec is None:
                continue
            matched = self._matched_capabilities(
                tool_name,
                spec,
                required=required,
                unresolved=unresolved_set,
                legacy_names=legacy_name_set,
            )
            if required and not matched:
                continue
            rejections.append(
                CapabilityToolRejection(
                    tool_name,
                    str(availability.get("reason") or "unavailable"),
                    matched,
                )
            )

        side_effect_rank = {
            "read_only": 0,
            "temporary": 1,
            "workspace_write": 2,
            "external_write": 3,
            "destructive": 4,
        }
        risk_rank = {"low": 0, "sandboxed": 1, "high": 2}
        candidates.sort(
            key=lambda candidate: (
                -len(candidate.matched_capabilities) if required else 0,
                side_effect_rank.get(candidate.spec.side_effect_level, 99),
                risk_rank.get(candidate.spec.risk, 99),
                candidate.provider_id,
                candidate.tool_name,
                candidate.tool_version,
                candidate.provider_digest,
            )
        )
        rejections.sort(
            key=lambda rejection: (
                rejection.tool_name,
                rejection.reason,
                rejection.matched_capabilities,
            )
        )
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
            legacy_tool_binding=bool(legacy_names),
            legacy_tool_names=legacy_names,
            plan_node_id=plan_node_id,
            require_read_only=require_read_only,
            require_idempotent=require_idempotent,
            excluded_tools=tuple(sorted(excluded)),
            rejections=tuple(rejections),
        )

    @staticmethod
    def _matched_capabilities(
        tool_name: str,
        spec: ToolSpec,
        *,
        required: tuple[str, ...],
        unresolved: frozenset[str],
        legacy_names: frozenset[str],
    ) -> tuple[str, ...]:
        if not required:
            return _normalized_values(spec.task_capabilities)
        matched = set(spec.task_capabilities) & (unresolved - legacy_names)
        if tool_name in unresolved and tool_name in legacy_names:
            matched.add(tool_name)
        return tuple(sorted(matched))

    @staticmethod
    def _satisfied_capabilities(
        required: tuple[str, ...],
        observations: Iterable[Any],
        *,
        plan_node_id: str | None,
        specs: Mapping[str, ToolSpec],
        legacy_names: frozenset[str],
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
            if tool_name in legacy_names:
                satisfied.add(tool_name)
        return tuple(sorted(satisfied))
