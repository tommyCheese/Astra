"""Public projections for permission-center persistence records."""


def permission_center_view(*, grants, identities, delegations, credentials, data_flow, catalog, explanations) -> dict:
    return {
        "grants": [_grant_view(item) for item in grants],
        "identities": [_identity_view(item) for item in identities],
        "delegations": [_delegation_view(item) for item in delegations],
        "credentials": [_credential_view(item) for item in credentials],
        "data_flow": _data_flow_view(data_flow),
        "tool_catalog": _catalog_view(catalog),
        "policy_explanations": [_explanation_view(item) for item in explanations],
    }


def _grant_view(item) -> dict:
    return {
        "id": item.id,
        "scope": item.scope,
        "tool_name": item.tool_name,
        "tool_version": item.tool_version,
        "effect_kinds": item.effect_kinds,
        "resource_matcher": item.resource_matcher,
        "invocation_constraints": item.invocation_constraints,
        "status": item.status,
        "use_count": item.use_count,
        "max_uses": item.max_uses,
        "expires_at": item.expires_at,
        "created_at": item.created_at,
    }


def _identity_view(item) -> dict:
    return {
        "id": item.id,
        "type": item.identity_type,
        "principal": item.principal,
        "task_id": item.task_id,
        "run_id": item.run_id,
        "parent_identity_id": item.parent_identity_id,
        "trust_level": item.trust_level,
        "attributes": item.attributes,
        "created_at": item.created_at,
        "revoked_at": item.revoked_at,
    }


def _delegation_view(item) -> dict:
    return {
        "id": item.id,
        "parent_identity_id": item.parent_identity_id,
        "child_identity_id": item.child_identity_id,
        "delegated_scope": item.delegated_scope,
        "expires_at": item.expires_at,
        "revoked_at": item.revoked_at,
    }


def _credential_view(item) -> dict:
    return {
        "id": item.id,
        "service": item.service,
        "scopes": item.scopes,
        "resources": item.resources,
        "actions": item.actions,
        "expires_at": item.expires_at,
        "revoked_at": item.revoked_at,
        "metadata": item.metadata_,
    }


def _data_flow_view(item) -> dict | None:
    if item is None:
        return None
    return {
        "trust_sources": item.trust_sources,
        "data_labels": item.data_labels,
        "allowed_destinations": item.allowed_destinations,
        "prohibited_destinations": item.prohibited_destinations,
        "state_version": item.state_version,
    }


def _catalog_view(item) -> dict | None:
    if item is None:
        return None
    return {"digest": item.digest, "catalog": item.catalog, "created_at": item.created_at}


def _explanation_view(item) -> dict:
    return {"id": item.id, "type": item.type, "payload": item.payload, "created_at": item.created_at}
