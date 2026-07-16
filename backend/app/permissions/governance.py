from __future__ import annotations

from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

from app.schemas.permissions import ActionEffectPlan, ExtensionDescriptor, PermissionBundle


class PermissionBundleEvaluator:
    def validate(
        self,
        bundle: PermissionBundle | None,
        plan: ActionEffectPlan,
        *,
        tool_identity: str,
        unattended: bool,
        tool_call_count: int = 0,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        if not unattended:
            return True, "interactive_run"
        if bundle is None:
            return False, "permission_bundle_required"
        now = now or datetime.now(timezone.utc)
        if bundle.expires_at is not None and _utc(bundle.expires_at) <= _utc(now):
            return False, "permission_bundle_expired"
        if tool_identity not in bundle.allowed_tool_identities:
            return False, "tool_identity_not_in_bundle"
        if bundle.max_tool_calls is not None and tool_call_count >= bundle.max_tool_calls:
            return False, "permission_bundle_budget_exhausted"
        if not set(plan.required_permissions) <= set(bundle.allowed_actions):
            return False, "action_not_in_bundle"
        kinds = {effect.kind for effect in plan.effects}
        if not kinds <= set(bundle.allowed_effect_kinds):
            return False, "effect_not_in_bundle"
        for effect in plan.effects:
            if not any(fnmatchcase(effect.resource, item) for item in bundle.allowed_resources):
                return False, "resource_not_in_bundle"
        destinations = plan.network_scope.get("destinations", [])
        if destinations and not all(
            any(fnmatchcase(destination, pattern) for pattern in bundle.network_destinations)
            for destination in destinations
        ):
            return False, "network_destination_not_in_bundle"
        return True, "permission_bundle_allowed"


class ExtensionTrustPolicy:
    def validate_catalog_entry(
        self,
        entry: dict[str, Any],
        *,
        allowed_providers: dict[str, set[str]],
    ) -> tuple[bool, str]:
        provider_id = str(entry.get("provider_id", ""))
        digest = str(entry.get("provider_digest", ""))
        if provider_id not in allowed_providers:
            return False, "provider_not_allowlisted"
        if digest not in allowed_providers[provider_id]:
            return False, "provider_digest_changed"
        if entry.get("trust_level") not in {"platform", "managed", "trusted"}:
            return False, "provider_trust_too_low"
        return True, "provider_trusted"

    def inventory(
        self,
        entries: list[ExtensionDescriptor],
        *,
        allowed_providers: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        inventory: list[dict[str, Any]] = []
        for descriptor in entries:
            if descriptor.extension_type not in {
                "tool", "mcp", "plugin", "skill", "hook", "custom_agent", "marketplace"
            }:
                raise ValueError("Unsupported extension type")
            trusted, reason = self.validate_catalog_entry(
                {
                    "provider_id": descriptor.provider_id,
                    "provider_digest": descriptor.digest,
                    "trust_level": descriptor.trust_level,
                },
                allowed_providers=allowed_providers,
            )
            if descriptor.enabled and not trusted:
                raise ValueError(f"Extension trust validation failed: {reason}")
            payload = descriptor.model_dump(mode="json")
            payload["annotations_trust"] = "untrusted_metadata"
            inventory.append(payload)
        return sorted(
            inventory,
            key=lambda item: (item["extension_type"], item["id"], item["version"]),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
