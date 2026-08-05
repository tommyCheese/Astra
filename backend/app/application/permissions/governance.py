from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from typing import Any

from app.common.schemas.permissions import ActionEffectPlan, ExtensionDescriptor, PermissionBundle


class PermissionBundleEvaluator:
    def __init__(self, signing_secret: str = ""):
        self.signing_secret = signing_secret

    def validate(
        self,
        bundle: PermissionBundle | None,
        plan: ActionEffectPlan,
        *,
        tool_identity: str,
        unattended: bool,
        tool_call_count: int = 0,
        run_started_at: datetime | None = None,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        if not unattended:
            return True, "interactive_run"
        now = now or datetime.now(timezone.utc)
        checks = (
            _bundle_identity_denial(bundle, self.signing_secret, tool_identity, now),
            _bundle_budget_denial(bundle, tool_call_count, run_started_at, now),
            _bundle_effect_denial(bundle, plan),
            _bundle_destination_denial(bundle, plan),
        )
        if denial := next((reason for reason in checks if reason), None):
            return False, denial
        return True, "permission_bundle_allowed"


def _bundle_identity_denial(bundle, signing_secret, tool_identity, now) -> str | None:
    if bundle is None:
        return "permission_bundle_required"
    if not verify_permission_bundle(bundle, signing_secret):
        return "permission_bundle_signature_invalid"
    if bundle.expires_at is not None and _utc(bundle.expires_at) <= _utc(now):
        return "permission_bundle_expired"
    if tool_identity not in bundle.allowed_tool_identities:
        return "tool_identity_not_in_bundle"
    return None


def _bundle_budget_denial(bundle, tool_call_count, run_started_at, now) -> str | None:
    if bundle is None:
        return None
    if bundle.max_tool_calls is not None and tool_call_count >= bundle.max_tool_calls:
        return "permission_bundle_budget_exhausted"
    if bundle.max_runtime_seconds is None:
        return None
    if run_started_at is None:
        return "permission_bundle_runtime_origin_missing"
    elapsed = (_utc(now) - _utc(run_started_at)).total_seconds()
    return "permission_bundle_runtime_exhausted" if elapsed >= bundle.max_runtime_seconds else None


def _bundle_effect_denial(bundle, plan) -> str | None:
    if bundle is None:
        return None
    if not set(plan.required_permissions) <= set(bundle.allowed_actions):
        return "action_not_in_bundle"
    if not {effect.kind for effect in plan.effects} <= set(bundle.allowed_effect_kinds):
        return "effect_not_in_bundle"
    if any(
        not any(fnmatchcase(effect.resource, item) for item in bundle.allowed_resources)
        for effect in plan.effects
    ):
        return "resource_not_in_bundle"
    labels = {label for effect in plan.effects for label in effect.data_labels}
    if not labels <= set(bundle.allowed_data_labels):
        return "data_label_not_in_bundle"
    scopes = {
        str(scope)
        for effect in plan.effects
        for scope in effect.metadata.get("credential_scopes", [])
    }
    return (
        None
        if scopes <= set(bundle.allowed_credential_scopes)
        else "credential_scope_not_in_bundle"
    )


def _bundle_destination_denial(bundle, plan) -> str | None:
    if bundle is None:
        return None
    outputs = [
        effect.resource
        for effect in plan.effects
        if effect.kind.value in {"artifact_write", "external_write"}
    ]
    if outputs and not _all_match(outputs, bundle.output_destinations):
        return "output_destination_not_in_bundle"
    destinations = plan.network_scope.get("destinations", [])
    if destinations and not _all_match(destinations, bundle.network_destinations):
        return "network_destination_not_in_bundle"
    return None


def _all_match(values, patterns) -> bool:
    return all(any(fnmatchcase(value, pattern) for pattern in patterns) for value in values)


def permission_bundle_digest(bundle: PermissionBundle | dict[str, Any], signing_secret: str) -> str:
    if not signing_secret:
        raise ValueError("Permission Bundle signing secret is not configured")
    payload = (
        bundle.model_dump(mode="json", exclude={"digest"})
        if isinstance(bundle, PermissionBundle)
        else {key: value for key, value in bundle.items() if key != "digest"}
    )
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    signature = hmac.new(signing_secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{signature}"


def verify_permission_bundle(bundle: PermissionBundle, signing_secret: str) -> bool:
    if not signing_secret:
        return False
    expected = permission_bundle_digest(bundle, signing_secret)
    return hmac.compare_digest(bundle.digest, expected)


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
                "tool",
                "mcp",
                "plugin",
                "skill",
                "hook",
                "custom_agent",
                "marketplace",
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
