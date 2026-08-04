from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.conversations import TaskRecord
from app.db.models.permissions import (
    AgentDelegationRecord,
    AgentIdentityRecord,
    CredentialGrantRecord,
    DataFlowStateRecord,
    ToolCatalogSnapshotRecord,
)
from app.db.models.runs import RunRecord
from app.permissions.engine import PermissionEngine
from app.schemas.permissions import (
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionRequest,
    PermissionSubject,
)


class PermissionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_identity(
        self,
        *,
        identity_type: str,
        principal: str,
        task_id: str | None = None,
        run_id: str | None = None,
        parent_identity_id: str | None = None,
        trust_level: str = "internal",
        attributes: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> AgentIdentityRecord:
        if run_id is not None:
            run = await self._require_run(run_id)
            if task_id is None:
                task_id = run.task_id
            elif task_id != run.task_id:
                raise ValueError("Identity task does not match its Run")
        elif task_id is not None:
            await self._require_task(task_id)
        if parent_identity_id is not None:
            parent = await self._require_identity(parent_identity_id)
            if task_id is not None and parent.task_id not in {None, task_id}:
                raise ValueError("Parent identity belongs to a different Task")
        identity = AgentIdentityRecord(
            identity_type=identity_type,
            principal=principal,
            task_id=task_id,
            run_id=run_id,
            parent_identity_id=parent_identity_id,
            trust_level=trust_level,
            attributes=deepcopy(attributes or {}),
        )
        self.session.add(identity)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return identity

    async def get_or_create_identity(
        self,
        *,
        identity_type: str,
        principal: str,
        task_id: str | None = None,
        run_id: str | None = None,
        parent_identity_id: str | None = None,
        trust_level: str = "internal",
        attributes: dict[str, Any] | None = None,
    ) -> AgentIdentityRecord:
        identity = await self.session.scalar(
            select(AgentIdentityRecord).where(
                AgentIdentityRecord.identity_type == identity_type,
                AgentIdentityRecord.principal == principal,
                AgentIdentityRecord.task_id == task_id,
                AgentIdentityRecord.run_id == run_id,
                AgentIdentityRecord.revoked_at.is_(None),
            )
        )
        if identity is not None:
            return identity
        return await self.create_identity(
            identity_type=identity_type,
            principal=principal,
            task_id=task_id,
            run_id=run_id,
            parent_identity_id=parent_identity_id,
            trust_level=trust_level,
            attributes=attributes,
        )

    async def create_delegation(
        self,
        *,
        parent_identity_id: str,
        child_identity_id: str,
        delegated_scope: dict[str, Any],
        expires_at: datetime | None = None,
        policies: PermissionPolicySet | None = None,
        commit: bool = True,
    ) -> AgentDelegationRecord:
        parent = await self._require_identity(parent_identity_id)
        child = await self._require_identity(child_identity_id)
        if parent.id == child.id:
            raise ValueError("An identity cannot delegate to itself")
        if child.parent_identity_id not in {None, parent.id}:
            raise ValueError("Child identity is attached to another parent")
        if parent.task_id is not None and child.task_id != parent.task_id:
            raise ValueError("Delegation cannot cross Task boundaries")
        parent_scope = parent.attributes.get("permission_scope", {})
        if not parent_scope or not _scope_is_subset(delegated_scope, parent_scope):
            raise ValueError("Delegation cannot amplify the parent permission scope")
        decision = PermissionEngine().authorize_request(
            PermissionRequest(
                subject=PermissionSubject(
                    agent_id=parent.id,
                    identity_type=parent.identity_type,
                    task_id=parent.task_id,
                    run_id=parent.run_id,
                ),
                action="delegation_create",
                resource=f"identity://{child.id}",
                context={"delegated_scope": deepcopy(delegated_scope)},
            ),
            policies,
        )
        if decision.decision != PermissionDecisionKind.allow:
            raise PermissionError(
                f"Delegation is not authorized: {decision.explanation.reason_code}"
            )
        child.parent_identity_id = parent.id
        delegation = AgentDelegationRecord(
            parent_identity_id=parent.id,
            child_identity_id=child.id,
            delegated_scope=deepcopy(delegated_scope),
            expires_at=expires_at,
        )
        self.session.add(delegation)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return delegation

    async def freeze_tool_catalog(
        self,
        run_id: str,
        *,
        catalog: list[dict[str, Any]],
        digest: str,
    ) -> ToolCatalogSnapshotRecord:
        await self._require_run(run_id)
        existing = await self.session.scalar(
            select(ToolCatalogSnapshotRecord).where(ToolCatalogSnapshotRecord.run_id == run_id)
        )
        if existing is not None:
            if existing.digest != digest or existing.catalog != catalog:
                raise ValueError("Tool Catalog Snapshot is immutable")
            return existing
        snapshot = ToolCatalogSnapshotRecord(
            run_id=run_id,
            catalog=deepcopy(catalog),
            digest=digest,
        )
        self.session.add(snapshot)
        await self.session.commit()
        return snapshot

    async def create_credential_grant(
        self,
        *,
        run_id: str,
        agent_identity_id: str,
        service: str,
        expires_at: datetime,
        tenant: str | None = None,
        scopes: list[str] | None = None,
        resources: list[str] | None = None,
        actions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CredentialGrantRecord:
        run = await self._require_run(run_id)
        identity = await self._require_identity(agent_identity_id)
        if identity.run_id not in {None, run_id} or identity.task_id not in {None, run.task_id}:
            raise ValueError("Credential Grant identity is outside the Run boundary")
        if expires_at <= utc_now():
            raise ValueError("Credential Grant must expire in the future")
        grant = CredentialGrantRecord(
            task_id=run.task_id,
            run_id=run_id,
            agent_identity_id=identity.id,
            service=service,
            tenant=tenant,
            scopes=list(scopes or []),
            resources=list(resources or []),
            actions=list(actions or []),
            metadata_=deepcopy(metadata or {}),
            expires_at=expires_at,
        )
        self.session.add(grant)
        await self.session.commit()
        return grant

    async def revoke_credential_grant(self, grant_id: str) -> CredentialGrantRecord:
        grant = await self.session.get(CredentialGrantRecord, grant_id)
        if grant is None:
            raise ValueError(f"Credential Grant not found: {grant_id}")
        if grant.revoked_at is None:
            grant.revoked_at = utc_now()
            await self.session.commit()
        return grant

    async def update_data_flow_state(
        self,
        run_id: str,
        *,
        expected_version: int | None = None,
        trust_sources: list[str] | None = None,
        data_labels: list[str] | None = None,
        allowed_destinations: list[str] | None = None,
        prohibited_destinations: list[str] | None = None,
        retention: dict[str, Any] | None = None,
    ) -> DataFlowStateRecord:
        await self._require_run(run_id)
        state = await self.session.scalar(
            select(DataFlowStateRecord).where(DataFlowStateRecord.run_id == run_id)
        )
        if state is None:
            if expected_version not in {None, 0}:
                raise ValueError("DataFlowState version conflict")
            state = DataFlowStateRecord(run_id=run_id, state_version=1)
            self.session.add(state)
        else:
            if expected_version is not None and state.state_version != expected_version:
                raise ValueError("DataFlowState version conflict")
            state.state_version += 1
        if trust_sources is not None:
            state.trust_sources = list(trust_sources)
        if data_labels is not None:
            state.data_labels = list(data_labels)
        if allowed_destinations is not None:
            state.allowed_destinations = list(allowed_destinations)
        if prohibited_destinations is not None:
            state.prohibited_destinations = list(prohibited_destinations)
        if retention is not None:
            state.retention = deepcopy(retention)
        state.updated_at = utc_now()
        await self.session.commit()
        return state

    async def get_data_flow_state(self, run_id: str) -> DataFlowStateRecord | None:
        return await self.session.scalar(
            select(DataFlowStateRecord).where(DataFlowStateRecord.run_id == run_id)
        )

    async def _require_run(self, run_id: str) -> RunRecord:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    async def _require_task(self, task_id: str) -> TaskRecord:
        task = await self.session.get(TaskRecord, task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task

    async def _require_identity(self, identity_id: str) -> AgentIdentityRecord:
        identity = await self.session.get(AgentIdentityRecord, identity_id)
        if identity is None:
            raise ValueError(f"Agent identity not found: {identity_id}")
        if identity.revoked_at is not None:
            raise ValueError("Agent identity is revoked")
        return identity


def _scope_is_subset(child: dict[str, Any], parent: dict[str, Any]) -> bool:
    for key in (
        "actions",
        "resources",
        "effect_kinds",
        "tools",
        "credential_scopes",
        "data_labels",
        "network_destinations",
        "skills",
        "allowed_purposes",
        "workspace_read_roots",
        "workspace_write_roots",
    ):
        parent_values = list(parent.get(key, []))
        child_values = list(child.get(key, []))
        if child_values and not parent_values:
            return False
        if any(
            not any(
                parent_value == "*"
                or child_value == parent_value
                or fnmatchcase(child_value, parent_value)
                for parent_value in parent_values
            )
            for child_value in child_values
        ):
            return False
    for key in ("max_uses", "max_tool_calls", "max_runtime_seconds"):
        parent_budget = parent.get(key)
        child_budget = child.get(key)
        if parent_budget is not None and (
            child_budget is None or child_budget > parent_budget
        ):
            return False
    return True
