from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts import LocalArtifactStore
from app.core.config import Settings, get_settings
from app.core.errors import ResourceError
from app.db.models import (
    AgentDelegationRecord,
    AgentIdentityRecord,
    ApprovalGrantRecord,
    ArtifactRecord,
    CredentialGrantRecord,
    DataFlowStateRecord,
    RunEventRecord,
    RunRecord,
    TaskRecord,
    TaskWorkspaceRecord,
    ToolCatalogSnapshotRecord,
    WorkspaceChangeRecord,
    WorkspaceCheckpointRecord,
    WorkspaceFileRecord,
)
from app.db.session import get_session
from app.deliverables import DeliverableCatalog
from app.permissions.engine import PermissionEngine
from app.repositories.runs import RunRepository
from app.repositories.workspaces import WorkspaceRepository
from app.schemas.permissions import PolicySimulationRequest, PolicySimulationResult
from app.schemas.schedules import ScheduledDeliverableView
from app.workspaces.runtime import WorkspaceRuntimeService

router = APIRouter(prefix="/api", tags=["permissions", "workspaces"])


@router.get("/runs/{run_id}/permissions")
async def permission_center(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await session.get(RunRecord, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    grants = (
        await session.scalars(
            select(ApprovalGrantRecord).where(
                (ApprovalGrantRecord.run_id == run_id)
                | (ApprovalGrantRecord.task_id == run.task_id)
            )
        )
    ).all()
    identities = (
        await session.scalars(
            select(AgentIdentityRecord).where(
                (AgentIdentityRecord.run_id == run_id)
                | (AgentIdentityRecord.task_id == run.task_id)
            )
        )
    ).all()
    identity_ids = [identity.id for identity in identities]
    delegations = (
        await session.scalars(
            select(AgentDelegationRecord).where(
                AgentDelegationRecord.parent_identity_id.in_(identity_ids)
                | AgentDelegationRecord.child_identity_id.in_(identity_ids)
            )
        )
    ).all() if identity_ids else []
    credentials = (
        await session.scalars(
            select(CredentialGrantRecord).where(CredentialGrantRecord.run_id == run_id)
        )
    ).all()
    data_flow = await session.scalar(
        select(DataFlowStateRecord).where(DataFlowStateRecord.run_id == run_id)
    )
    catalog = await session.scalar(
        select(ToolCatalogSnapshotRecord).where(ToolCatalogSnapshotRecord.run_id == run_id)
    )
    explanations = (
        await session.scalars(
            select(RunEventRecord).where(
                RunEventRecord.run_id == run_id,
                RunEventRecord.type.in_(
                    [
                        "approval.requested",
                        "approval.decided",
                        "permission.decided",
                    ]
                ),
            ).order_by(RunEventRecord.created_at.desc()).limit(50)
        )
    ).all()
    return {
        "grants": [
            {
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
            for item in grants
        ],
        "identities": [
            {
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
            for item in identities
        ],
        "delegations": [
            {
                "id": item.id,
                "parent_identity_id": item.parent_identity_id,
                "child_identity_id": item.child_identity_id,
                "delegated_scope": item.delegated_scope,
                "expires_at": item.expires_at,
                "revoked_at": item.revoked_at,
            }
            for item in delegations
        ],
        "credentials": [
            {
                "id": item.id,
                "service": item.service,
                "scopes": item.scopes,
                "resources": item.resources,
                "actions": item.actions,
                "expires_at": item.expires_at,
                "revoked_at": item.revoked_at,
                "metadata": item.metadata_,
            }
            for item in credentials
        ],
        "data_flow": {
            "trust_sources": data_flow.trust_sources,
            "data_labels": data_flow.data_labels,
            "allowed_destinations": data_flow.allowed_destinations,
            "prohibited_destinations": data_flow.prohibited_destinations,
            "state_version": data_flow.state_version,
        } if data_flow else None,
        "tool_catalog": {
            "digest": catalog.digest,
            "catalog": catalog.catalog,
            "created_at": catalog.created_at,
        } if catalog else None,
        "policy_explanations": [
            {
                "id": item.id,
                "type": item.type,
                "payload": item.payload,
                "created_at": item.created_at,
            }
            for item in explanations
        ],
    }


@router.delete("/permission-grants/{grant_id}")
async def revoke_permission_grant(
    grant_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    grant = await RunRepository(session).revoke_approval_grant(grant_id)
    return {"id": grant.id, "status": grant.status, "revoked_at": grant.revoked_at}


@router.post("/permissions/simulate", response_model=PolicySimulationResult)
async def simulate_permission(payload: PolicySimulationRequest) -> PolicySimulationResult:
    engine = PermissionEngine()
    effective = engine.evaluate(payload.request, payload.policies)
    shadow = (
        engine.evaluate(payload.request, payload.shadow_policies)
        if payload.shadow_policies is not None
        else None
    )
    return PolicySimulationResult(
        effective=effective,
        shadow=shadow,
        changed=shadow is not None and shadow.decision != effective.decision,
    )


@router.get("/tasks/{task_id}/workspace")
async def task_workspace_view(
    task_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace = await WorkspaceRepository(session).get_or_create(task_id)
    files = (
        await session.scalars(
            select(WorkspaceFileRecord).where(
                WorkspaceFileRecord.workspace_id == workspace.id
            ).order_by(WorkspaceFileRecord.relative_path)
        )
    ).all()
    changes = (
        await session.scalars(
            select(WorkspaceChangeRecord).where(
                WorkspaceChangeRecord.workspace_id == workspace.id
            ).order_by(WorkspaceChangeRecord.created_at.desc())
        )
    ).all()
    checkpoints = (
        await session.scalars(
            select(WorkspaceCheckpointRecord).where(
                WorkspaceCheckpointRecord.workspace_id == workspace.id
            ).order_by(WorkspaceCheckpointRecord.created_at.desc())
        )
    ).all()
    return {
        "id": workspace.id,
        "task_id": task_id,
        "status": workspace.status,
        "quotas": workspace.quotas,
        "files": [
            {
                "id": item.id,
                "path": item.relative_path,
                "status": item.status,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "checksum": item.checksum,
                "security_status": item.security_status,
                "deliverable_candidate": item.deliverable_candidate,
                "content_url": f"/api/tasks/{task_id}/workspace/files/{item.id}/content"
                if item.status == "present" and item.deliverable_candidate
                else None,
                "updated_at": item.updated_at,
                "deleted_at": item.deleted_at,
            }
            for item in files
        ],
        "changes": [
            {
                "id": item.id,
                "run_id": item.run_id,
                "tool_call_id": item.tool_call_id,
                "path": item.relative_path,
                "kind": item.change_kind,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "deliverable_candidate": item.deliverable_candidate,
                "created_at": item.created_at,
            }
            for item in changes
        ],
        "checkpoints": [
            {
                "id": item.id,
                "run_id": item.run_id,
                "manifest_hash": item.manifest_hash,
                "status": item.status,
                "file_count": len(item.manifest),
                "created_at": item.created_at,
            }
            for item in checkpoints
        ],
    }


@router.get("/library/files")
async def library_files(
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (
        await session.execute(
            select(WorkspaceFileRecord, TaskWorkspaceRecord, TaskRecord)
            .join(TaskWorkspaceRecord, WorkspaceFileRecord.workspace_id == TaskWorkspaceRecord.id)
            .join(TaskRecord, TaskWorkspaceRecord.task_id == TaskRecord.id)
            .where(WorkspaceFileRecord.status == "present")
            .order_by(WorkspaceFileRecord.updated_at.desc())
        )
    ).all()
    return [
        {
            "id": file.id,
            "task_id": task.id,
            "conversation_title": task.title,
            "path": file.relative_path,
            "mime_type": file.mime_type,
            "size_bytes": file.size_bytes,
            "security_status": file.security_status,
            "deliverable_candidate": file.deliverable_candidate,
            "content_url": f"/api/tasks/{task.id}/workspace/files/{file.id}/content"
            if file.deliverable_candidate
            else None,
            "created_at": file.created_at,
            "updated_at": file.updated_at,
        }
        for file, _workspace, task in rows
    ]


@router.get("/library/deliverables", response_model=list[ScheduledDeliverableView])
async def library_deliverables(
    limit: int = Query(default=500, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    return await DeliverableCatalog(session).list(limit=limit)


@router.get("/deliverables/artifacts/{artifact_id}/content")
async def deliverable_artifact_content(
    artifact_id: str,
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    artifact = await session.get(ArtifactRecord, artifact_id)
    if artifact is None or not artifact.storage_key or artifact.security_status != "verified":
        raise ResourceError("DELIVERABLE_NOT_FOUND", "找不到可访问的制品。")
    path = LocalArtifactStore(settings.artifact_store_path).resolve(artifact.storage_key)
    if not path.is_file():
        raise ResourceError("DELIVERABLE_NOT_FOUND", "制品内容已不可用。")
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=str(artifact.metadata_.get("filename") or Path(artifact.path or path).name),
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/tasks/{task_id}/workspace/files/{file_id}/content")
async def workspace_file_content(
    task_id: str,
    file_id: str,
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    workspace = await WorkspaceRepository(session).get_or_create(task_id)
    file = await session.get(WorkspaceFileRecord, file_id)
    if (
        file is None
        or file.workspace_id != workspace.id
        or file.status != "present"
        or not file.deliverable_candidate
        or file.security_status != "verified"
    ):
        raise ValueError("Workspace file is not available")
    service = WorkspaceRuntimeService(
        WorkspaceRepository(session),
        settings.task_workspace_store_path,
        max_files=settings.task_workspace_max_files,
        max_bytes=settings.task_workspace_max_bytes,
        max_file_bytes=settings.task_workspace_max_file_bytes,
    )
    workspace_dir = await service.prepare(task_id)
    path = service.resolve_file(workspace_dir, file.relative_path)
    return FileResponse(
        Path(path),
        media_type=file.mime_type or "application/octet-stream",
        filename=Path(file.relative_path).name,
        content_disposition_type="inline" if inline else "attachment",
    )
