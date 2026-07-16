from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    RunRecord,
    TaskRecord,
    TaskWorkspaceRecord,
    WorkspaceChangeRecord,
    WorkspaceCheckpointRecord,
    WorkspaceFileRecord,
    utc_now,
)

DEFAULT_WORKSPACE_QUOTAS = {
    "max_files": 10_000,
    "max_total_bytes": 1_073_741_824,
    "max_file_bytes": 104_857_600,
    "max_depth": 32,
    "max_checkpoints": 100,
}


def validate_workspace_path(relative_path: str) -> str:
    if not relative_path or "\x00" in relative_path or "\\" in relative_path:
        raise ValueError("Invalid Workspace path")
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Workspace path must be a normalized relative path")
    normalized = path.as_posix()
    if normalized.startswith("-"):
        raise ValueError("Workspace path cannot begin with '-'")
    return normalized


class WorkspaceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create(
        self,
        task_id: str,
        *,
        storage_key: str | None = None,
        quotas: dict[str, Any] | None = None,
    ) -> TaskWorkspaceRecord:
        task = await self.session.get(TaskRecord, task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        existing = await self.session.scalar(
            select(TaskWorkspaceRecord).where(TaskWorkspaceRecord.task_id == task_id)
        )
        if existing is not None:
            return existing
        workspace = TaskWorkspaceRecord(
            task_id=task_id,
            storage_key=storage_key or f"task-workspaces/{task_id}",
            quotas={**DEFAULT_WORKSPACE_QUOTAS, **(quotas or {})},
        )
        self.session.add(workspace)
        await self.session.commit()
        return workspace

    async def upsert_file(
        self,
        workspace_id: str,
        relative_path: str,
        *,
        mime_type: str | None = None,
        size_bytes: int = 0,
        checksum: str | None = None,
        security_status: str = "pending",
        deliverable_candidate: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceFileRecord:
        workspace = await self._require_workspace(workspace_id)
        path = validate_workspace_path(relative_path)
        if size_bytes < 0:
            raise ValueError("Workspace file size cannot be negative")
        max_file_bytes = int(workspace.quotas.get("max_file_bytes", 0))
        if max_file_bytes and size_bytes > max_file_bytes:
            raise ValueError("Workspace file exceeds quota")
        file = await self.session.scalar(
            select(WorkspaceFileRecord).where(
                WorkspaceFileRecord.workspace_id == workspace_id,
                WorkspaceFileRecord.relative_path == path,
            )
        )
        now = utc_now()
        if file is None:
            file = WorkspaceFileRecord(
                workspace_id=workspace_id,
                relative_path=path,
                created_at=now,
            )
            self.session.add(file)
        file.status = "present"
        file.mime_type = mime_type
        file.size_bytes = size_bytes
        file.checksum = checksum
        file.security_status = security_status
        file.deliverable_candidate = deliverable_candidate
        file.metadata_ = deepcopy(metadata or {})
        file.deleted_at = None
        file.updated_at = now
        await self.session.commit()
        return file

    async def record_change(
        self,
        *,
        workspace_id: str,
        run_id: str,
        relative_path: str,
        change_kind: str,
        tool_call_id: str | None = None,
        checkpoint_id: str | None = None,
        before_checksum: str | None = None,
        after_checksum: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        security_status: str = "pending",
        deliverable_candidate: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceChangeRecord:
        workspace = await self._require_workspace(workspace_id)
        run = await self._require_run(run_id)
        if run.task_id != workspace.task_id:
            raise ValueError("Workspace change cannot cross Task boundaries")
        if change_kind not in {"created", "modified", "deleted"}:
            raise ValueError(f"Unsupported Workspace change kind: {change_kind}")
        path = validate_workspace_path(relative_path)
        change = WorkspaceChangeRecord(
            workspace_id=workspace_id,
            run_id=run_id,
            tool_call_id=tool_call_id,
            checkpoint_id=checkpoint_id,
            relative_path=path,
            change_kind=change_kind,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
            mime_type=mime_type,
            size_bytes=size_bytes,
            security_status=security_status,
            deliverable_candidate=deliverable_candidate,
            metadata_=deepcopy(metadata or {}),
        )
        self.session.add(change)
        if change_kind == "deleted":
            file = await self.session.scalar(
                select(WorkspaceFileRecord).where(
                    WorkspaceFileRecord.workspace_id == workspace_id,
                    WorkspaceFileRecord.relative_path == path,
                )
            )
            if file is not None:
                file.status = "deleted"
                file.deleted_at = utc_now()
                file.updated_at = file.deleted_at
        await self.session.commit()
        return change

    async def create_checkpoint(
        self,
        *,
        workspace_id: str,
        run_id: str,
        manifest: dict[str, Any],
        manifest_hash: str,
        status: str = "valid",
    ) -> WorkspaceCheckpointRecord:
        workspace = await self._require_workspace(workspace_id)
        run = await self._require_run(run_id)
        if run.task_id != workspace.task_id:
            raise ValueError("Workspace checkpoint cannot cross Task boundaries")
        checkpoint = WorkspaceCheckpointRecord(
            workspace_id=workspace_id,
            run_id=run_id,
            manifest=deepcopy(manifest),
            manifest_hash=manifest_hash,
            status=status,
        )
        self.session.add(checkpoint)
        await self.session.commit()
        return checkpoint

    async def _require_workspace(self, workspace_id: str) -> TaskWorkspaceRecord:
        workspace = await self.session.get(TaskWorkspaceRecord, workspace_id)
        if workspace is None or workspace.deleted_at is not None:
            raise ValueError(f"Task Workspace not found: {workspace_id}")
        return workspace

    async def _require_run(self, run_id: str) -> RunRecord:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run
