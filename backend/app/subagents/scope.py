from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any

from app.schemas.agent.run_policy import EffectiveSubagentPolicy
from app.schemas.subagents import (
    DelegationRejectionCode,
    DelegationValidationIssue,
    EffectiveDelegationScope,
)


class DelegationAuthorizationError(RuntimeError):
    def __init__(
        self,
        code: DelegationRejectionCode,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.issue = DelegationValidationIssue(
            code=code,
            message=message,
            field=field,
            details=details or {},
        )
        super().__init__(f"{code.value}: {message}")


class DelegationScopeAttenuator:
    LIST_KEYS = (
        "actions",
        "resources",
        "effect_kinds",
        "tools",
        "skills",
        "credential_scopes",
        "data_labels",
        "allowed_purposes",
        "network_destinations",
        "workspace_read_roots",
        "workspace_write_roots",
    )
    BUDGET_KEYS = ("max_uses", "max_tool_calls", "max_runtime_seconds")
    WRITE_MARKERS = ("write", "delete", "execute", "change", "create", "mutation")

    @classmethod
    def attenuate(
        cls,
        *,
        requested: dict[str, Any],
        parent: dict[str, Any],
        task_policy: dict[str, Any] | None,
        server_policy: EffectiveSubagentPolicy,
        execution_id: str,
    ) -> EffectiveDelegationScope:
        ceilings = [parent, *([task_policy] if task_policy else [])]
        normalized = {
            key: cls._attenuate_list(key, requested, ceilings, server_policy)
            for key in cls.LIST_KEYS
        }
        normalized.update(
            {key: cls._attenuate_budget(key, requested, ceilings) for key in cls.BUDGET_KEYS}
        )
        normalized["private_staging_root"] = f".astra/subagents/{execution_id}/staging"
        return EffectiveDelegationScope(**normalized)

    @classmethod
    def _attenuate_list(cls, key, requested, ceilings, server_policy):
        values = _unique_strings(requested.get(key, []))
        if values and any(
            not _values_are_subset(values, ceiling.get(key, [])) for ceiling in ceilings
        ):
            raise DelegationAuthorizationError(
                DelegationRejectionCode.resource_not_delegated,
                f"Requested {key} exceeds an authority ceiling.",
                field=f"resource_scope.{key}",
                details={"requested": values},
            )
        if (
            server_policy.read_only
            and key
            in {
                "actions",
                "effect_kinds",
                "workspace_write_roots",
            }
            and any(cls._is_write(value) for value in values)
        ):
            raise DelegationAuthorizationError(
                DelegationRejectionCode.resource_not_delegated,
                "Read-only subagents cannot receive write or execution authority.",
                field=f"resource_scope.{key}",
            )
        return tuple(values)

    @staticmethod
    def _attenuate_budget(key, requested, ceilings):
        requested_value = requested.get(key)
        ceiling_values = [item.get(key) for item in ceilings if item.get(key) is not None]
        if requested_value is not None and ceiling_values and requested_value > min(ceiling_values):
            raise DelegationAuthorizationError(
                DelegationRejectionCode.budget_rejected,
                f"Requested {key} exceeds an authority ceiling.",
                field=f"resource_scope.{key}",
            )
        if requested_value is not None:
            return min([requested_value, *ceiling_values])
        return min(ceiling_values, default=None)

    @staticmethod
    def _is_write(value: str) -> bool:
        lowered = value.lower()
        return any(marker in lowered for marker in DelegationScopeAttenuator.WRITE_MARKERS)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _values_are_subset(children: Iterable[str], parents: Iterable[str]) -> bool:
    parent_patterns = tuple(parents)
    return all(any(fnmatchcase(child, parent) for parent in parent_patterns) for child in children)
