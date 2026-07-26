from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select

from app.db.models import ResourceLeaseRecord, utc_now
from app.repositories.executions import NodeExecutionRepository
from app.schemas.permissions import ActionEffectPlan, EffectKind

READ_EFFECTS = frozenset(
    {
        EffectKind.workspace_read,
        EffectKind.network_read,
        EffectKind.sensitive_data_read,
    }
)
EXCLUSIVE_EFFECTS = frozenset(
    {
        EffectKind.process_execute_unknown,
        EffectKind.external_write,
        EffectKind.credential_use,
        EffectKind.delegation_create,
        EffectKind.permission_change,
    }
)


@dataclass(frozen=True)
class ResourceClaim:
    resource_key: str
    resource_summary: str
    mode: str


def resource_claims_from_effect_plan(plan: ActionEffectPlan) -> tuple[ResourceClaim, ...]:
    claims: dict[tuple[str, str], ResourceClaim] = {}
    for effect in plan.effects:
        mode = (
            "exclusive"
            if effect.kind in EXCLUSIVE_EFFECTS
            or (effect.persistent and not effect.reversible and effect.kind not in READ_EFFECTS)
            else "read"
            if effect.kind in READ_EFFECTS
            else "write"
        )
        raw_resource = effect.resource.strip()
        if not raw_resource or raw_resource.endswith("://unknown"):
            raw_resource = f"provider://{plan.tool_name}"
            mode = "exclusive"
        key = normalize_resource_key(raw_resource)
        summary = safe_resource_summary(key)
        claims[(key, mode)] = ResourceClaim(key, summary, mode)
    if not claims:
        key = normalize_resource_key(f"provider://{plan.tool_name}")
        claims[(key, "exclusive")] = ResourceClaim(
            key,
            safe_resource_summary(key),
            "exclusive",
        )
    return tuple(
        claims[key]
        for key in sorted(claims, key=lambda item: (item[0], item[1]))
    )


def normalize_resource_key(resource: str) -> str:
    split = urlsplit(resource.strip())
    if not split.scheme:
        normalized_path = posixpath.normpath("/" + resource.lstrip("/"))
        return f"workspace://local{normalized_path}"
    scheme = split.scheme.casefold()
    authority = split.netloc.casefold()
    path = posixpath.normpath("/" + split.path.lstrip("/"))
    if split.path.endswith("/") and path != "/":
        path += "/"
    return urlunsplit((scheme, authority, path, "", ""))


def safe_resource_summary(resource_key: str) -> str:
    split = urlsplit(resource_key)
    category = {
        "workspace": "workspace path",
        "artifact": "task artifact",
        "web": "network resource",
        "http": "network resource",
        "https": "network resource",
        "credential": "credential scope",
        "provider": "tool provider",
        "sandbox": "sandbox resource",
    }.get(split.scheme, "protected resource")
    digest = hashlib.sha256(resource_key.encode()).hexdigest()[:10]
    return f"{category} · {digest}"


def resource_claims_conflict(left: ResourceClaim, right: ResourceClaim) -> bool:
    if left.mode == right.mode == "read":
        return False
    if left.mode == "exclusive" or right.mode == "exclusive":
        return _same_resource_tree(left.resource_key, right.resource_key)
    return _same_resource_tree(left.resource_key, right.resource_key)


def _same_resource_tree(left: str, right: str) -> bool:
    left_split = urlsplit(left)
    right_split = urlsplit(right)
    if (left_split.scheme, left_split.netloc) != (
        right_split.scheme,
        right_split.netloc,
    ):
        return False
    left_path = left_split.path.rstrip("/") or "/"
    right_path = right_split.path.rstrip("/") or "/"
    return (
        left_path == right_path
        or left_path == "/"
        or right_path == "/"
        or left_path.startswith(right_path + "/")
        or right_path.startswith(left_path + "/")
    )


async def acquire_resource_claims(
    repository: NodeExecutionRepository,
    *,
    run_id: str,
    execution_id: str,
    claims: tuple[ResourceClaim, ...],
    ttl_seconds: int = 30,
) -> bool:
    now = utc_now()
    result = await repository.session.execute(
        select(ResourceLeaseRecord).where(
            ResourceLeaseRecord.run_id == run_id,
            ResourceLeaseRecord.node_execution_id != execution_id,
            ResourceLeaseRecord.released_at.is_(None),
            ResourceLeaseRecord.expires_at > now,
        )
    )
    active = list(result.scalars().all())
    for claim in claims:
        if any(
            resource_claims_conflict(
                claim,
                ResourceClaim(
                    lease.resource_key,
                    lease.resource_summary,
                    lease.mode,
                ),
            )
            for lease in active
        ):
            return False
    for claim in claims:
        await repository.create_lease(
            run_id=run_id,
            execution_id=execution_id,
            resource_key=claim.resource_key,
            resource_summary=claim.resource_summary,
            mode=claim.mode,
            ttl_seconds=ttl_seconds,
        )
    return True
