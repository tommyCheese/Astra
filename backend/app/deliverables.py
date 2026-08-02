from __future__ import annotations

from datetime import timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ArtifactRecord,
    RunRecord,
    ScheduledJobRecord,
    ScheduledJobRunRecord,
    TaskRecord,
    TaskWorkspaceRecord,
    ToolCallRecord,
    WorkspaceChangeRecord,
    WorkspaceFileRecord,
)
from app.schemas.schedules import ScheduledDeliverableView

DATA_MIME_TYPES = {
    "application/json",
    "application/geo+json",
    "application/vnd.apache.parquet",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/tab-separated-values",
}
RECEIPT_SIDE_EFFECT_LEVELS = {
    "control_plane",
    "external_side_effect",
    "external_write",
    "network_write",
    "persistent",
    "persistent_side_effect",
}


def file_deliverable_kind(mime_type: str | None, path: str | None = None) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    data_suffixes = {".csv", ".json", ".parquet", ".tsv", ".xls", ".xlsx"}
    return (
        "data"
        if normalized in DATA_MIME_TYPES or Path(path or "").suffix.lower() in data_suffixes
        else "file"
    )


def _short_scalar(value, *, limit: int = 500) -> str | None:
    if not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def operation_receipt(call: ToolCallRecord) -> dict | None:
    if call.status != "succeeded" or call.side_effect_level not in RECEIPT_SIDE_EFFECT_LEVELS:
        return None
    output = call.output if isinstance(call.output, dict) else {}
    tool_input = call.input if isinstance(call.input, dict) else {}
    receipt_keys = {
        "destination", "event_id", "id", "link", "message_id", "object_id",
        "resource", "resource_id", "resource_url", "target", "url",
    }
    if call.side_effect_level == "external_side_effect" and not receipt_keys.intersection(
        {*output, *tool_input}
    ):
        return None
    summary = next(
        (
            value
            for key in ("summary", "message", "result", "status_text")
            if (value := _short_scalar(output.get(key))) is not None
        ),
        f"{call.tool_name} 已成功完成",
    )
    return {
        "summary": summary,
        "status": _short_scalar(output.get("status"), limit=80) or call.status,
        "object_id": next(
            (
                value
                for key in ("object_id", "resource_id", "message_id", "event_id", "id")
                if (value := _short_scalar(output.get(key), limit=240)) is not None
            ),
            None,
        ),
        "target": next(
            (
                value
                for source in (tool_input, output)
                for key in ("destination", "target", "resource", "url")
                if (value := _short_scalar(source.get(key), limit=500)) is not None
            ),
            None,
        ),
        "external_url": next(
            (
                value
                for source in (output, tool_input)
                for key in ("url", "link", "resource_url")
                if (value := _short_scalar(source.get(key), limit=2000)) is not None
                and value.startswith(("https://", "http://"))
            ),
            None,
        ),
    }


class DeliverableCatalog:
    """Canonical projection used by both Library and Scheduled Tasks."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, *, job_id: str | None = None, limit: int = 500) -> list[ScheduledDeliverableView]:
        schedule_query = select(ScheduledJobRunRecord).where(
            ScheduledJobRunRecord.run_id.is_not(None)
        )
        if job_id is not None:
            schedule_query = schedule_query.where(ScheduledJobRunRecord.job_id == job_id)
        schedule_runs = list(
            (await self.session.scalars(schedule_query.order_by(
                ScheduledJobRunRecord.created_at.desc()
            ).limit(limit))).all()
        )
        scheduled_run_by_run = {
            item.run_id: item for item in schedule_runs if item.run_id is not None
        }
        scheduled_run_ids = list(scheduled_run_by_run)

        artifact_query = select(ArtifactRecord).where(
            ArtifactRecord.storage_key.is_not(None),
            ArtifactRecord.security_status == "verified",
        )
        if job_id is not None:
            if not scheduled_run_ids:
                return []
            artifact_query = artifact_query.where(ArtifactRecord.run_id.in_(scheduled_run_ids))
        artifacts = list((await self.session.scalars(
            artifact_query.order_by(ArtifactRecord.created_at.desc()).limit(limit)
        )).all())

        run_ids = {*scheduled_run_ids, *(item.run_id for item in artifacts)}
        runs = {
            item.id: item
            for item in (await self.session.scalars(
                select(RunRecord).where(RunRecord.id.in_(run_ids))
            )).all()
        } if run_ids else {}
        task_ids = {item.task_id for item in runs.values()}

        file_rows: list[tuple[WorkspaceFileRecord, TaskWorkspaceRecord, TaskRecord]] = []
        latest_change_by_location: dict[tuple[str, str], WorkspaceChangeRecord] = {}
        if job_id is None:
            file_rows = list((await self.session.execute(
                select(WorkspaceFileRecord, TaskWorkspaceRecord, TaskRecord)
                .join(TaskWorkspaceRecord, WorkspaceFileRecord.workspace_id == TaskWorkspaceRecord.id)
                .join(TaskRecord, TaskWorkspaceRecord.task_id == TaskRecord.id)
                .where(
                    WorkspaceFileRecord.status == "present",
                    WorkspaceFileRecord.deliverable_candidate.is_(True),
                    WorkspaceFileRecord.security_status == "verified",
                )
                .order_by(WorkspaceFileRecord.updated_at.desc())
            )).all())
            task_ids.update(task.id for _file, _workspace, task in file_rows)
            library_workspace_ids = {workspace.id for _file, workspace, _task in file_rows}
            if library_workspace_ids:
                library_changes = list((await self.session.scalars(
                    select(WorkspaceChangeRecord).where(
                        WorkspaceChangeRecord.workspace_id.in_(library_workspace_ids),
                        WorkspaceChangeRecord.deliverable_candidate.is_(True),
                        WorkspaceChangeRecord.change_kind != "deleted",
                    ).order_by(WorkspaceChangeRecord.created_at.desc())
                )).all())
                for change in library_changes:
                    latest_change_by_location.setdefault(
                        (change.workspace_id, change.relative_path), change
                    )

        tasks = {
            item.id: item
            for item in (await self.session.scalars(
                select(TaskRecord).where(TaskRecord.id.in_(task_ids))
            )).all()
        } if task_ids else {}
        job_ids = {item.job_id for item in schedule_runs}
        jobs = {
            item.id: item
            for item in (await self.session.scalars(
                select(ScheduledJobRecord).where(ScheduledJobRecord.id.in_(job_ids))
            )).all()
        } if job_ids else {}

        changes = list((await self.session.scalars(
            select(WorkspaceChangeRecord).where(
                WorkspaceChangeRecord.run_id.in_(scheduled_run_ids),
                WorkspaceChangeRecord.deliverable_candidate.is_(True),
                WorkspaceChangeRecord.change_kind != "deleted",
            ).order_by(WorkspaceChangeRecord.created_at.desc())
        )).all()) if scheduled_run_ids else []
        workspace_ids = {item.workspace_id for item in changes}
        current_files = list((await self.session.scalars(
            select(WorkspaceFileRecord).where(
                WorkspaceFileRecord.workspace_id.in_(workspace_ids),
                WorkspaceFileRecord.status == "present",
                WorkspaceFileRecord.deliverable_candidate.is_(True),
                WorkspaceFileRecord.security_status == "verified",
            )
        )).all()) if workspace_ids else []
        files_by_location = {(item.workspace_id, item.relative_path): item for item in current_files}
        tool_calls = list((await self.session.scalars(
            select(ToolCallRecord).where(
                ToolCallRecord.run_id.in_(scheduled_run_ids),
                ToolCallRecord.status == "succeeded",
                ToolCallRecord.side_effect_level.in_(RECEIPT_SIDE_EFFECT_LEVELS),
            ).order_by(ToolCallRecord.completed_at.desc())
        )).all()) if scheduled_run_ids else []

        artifacts_by_run: dict[str, list[ArtifactRecord]] = {}
        for artifact in artifacts:
            artifacts_by_run.setdefault(artifact.run_id, []).append(artifact)
        changes_by_run: dict[str, list[WorkspaceChangeRecord]] = {}
        for change in changes:
            changes_by_run.setdefault(change.run_id, []).append(change)
        calls_by_run: dict[str, list[ToolCallRecord]] = {}
        for call in tool_calls:
            calls_by_run.setdefault(call.run_id, []).append(call)

        deliverables: list[ScheduledDeliverableView] = []
        scheduled_locations: set[tuple[str, str]] = set()
        for schedule_run in schedule_runs:
            if schedule_run.run_id is None or (run := runs.get(schedule_run.run_id)) is None:
                continue
            task = tasks.get(run.task_id)
            job = jobs.get(schedule_run.job_id)
            common = {
                "job_id": schedule_run.job_id,
                "job_name": job.name if job else None,
                "job_kind": job.kind if job else None,
                "schedule_run_id": schedule_run.id,
                "trigger_type": schedule_run.trigger_type,
                "run_id": run.id,
                "task_id": run.task_id,
                "conversation_title": task.title if task else "未命名对话",
            }
            raw_result = run.result if isinstance(run.result, dict) else {}
            result_summary = str(raw_result.get("summary") or run.summary or "").strip()
            if result_summary:
                deliverables.append(ScheduledDeliverableView(
                    **common,
                    id=f"result:{schedule_run.id}", kind="result", title="执行结果",
                    summary=result_summary, metadata={"run_status": run.status},
                    created_at=run.completed_at or run.updated_at,
                ))

            emitted_paths: set[str] = set()
            for artifact in artifacts_by_run.get(run.id, []):
                path = artifact.path or _short_scalar(artifact.metadata_.get("relative_path"))
                if path:
                    emitted_paths.add(path)
                title = str(artifact.metadata_.get("filename") or Path(path or artifact.type).name)
                deliverables.append(ScheduledDeliverableView(
                    **common,
                    id=f"artifact:{artifact.id}",
                    kind=file_deliverable_kind(artifact.mime_type, path),
                    title=title, summary=path, mime_type=artifact.mime_type,
                    size_bytes=artifact.size_bytes,
                    content_url=f"/api/deliverables/artifacts/{artifact.id}/content",
                    metadata={
                        "source": "workspace" if artifact.type == "workspace_snapshot" else "artifact",
                        "artifact_type": artifact.type,
                        **({"path": path} if path else {}),
                    },
                    created_at=artifact.created_at,
                ))
            for change in changes_by_run.get(run.id, []):
                scheduled_locations.add((change.workspace_id, change.relative_path))
                if change.relative_path in emitted_paths:
                    continue
                file = files_by_location.get((change.workspace_id, change.relative_path))
                # Never point a historical run at newer bytes written to the same path.
                if file is None or (change.after_checksum and file.checksum != change.after_checksum):
                    continue
                emitted_paths.add(change.relative_path)
                deliverables.append(ScheduledDeliverableView(
                    **common,
                    id=f"workspace-file:{file.id}:{schedule_run.id}",
                    kind=file_deliverable_kind(file.mime_type, file.relative_path),
                    title=Path(file.relative_path).name, summary=file.relative_path,
                    mime_type=file.mime_type, size_bytes=file.size_bytes,
                    content_url=f"/api/tasks/{run.task_id}/workspace/files/{file.id}/content",
                    metadata={"source": "workspace", "path": file.relative_path, "version": "current"},
                    created_at=change.created_at,
                ))
            for call in calls_by_run.get(run.id, []):
                if (receipt := operation_receipt(call)) is None:
                    continue
                deliverables.append(ScheduledDeliverableView(
                    **common,
                    id=f"receipt:{call.id}", kind="receipt",
                    title=f"{call.tool_name} 操作回执", summary=receipt["summary"],
                    external_url=receipt["external_url"],
                    metadata={
                        "tool_name": call.tool_name, "status": receipt["status"],
                        "target": receipt["target"], "object_id": receipt["object_id"],
                    },
                    created_at=call.completed_at or call.started_at,
                ))

        if job_id is None:
            scheduled_artifact_ids = {
                artifact.id for artifact in artifacts
                if artifact.run_id in scheduled_run_by_run
            }
            snapshotted_task_paths = {
                (runs[artifact.run_id].task_id, artifact.path)
                for artifact in artifacts
                if artifact.type == "workspace_snapshot"
                and artifact.path
                and artifact.run_id in runs
            }
            for artifact in artifacts:
                if artifact.id in scheduled_artifact_ids:
                    continue
                run = runs.get(artifact.run_id)
                if run is None:
                    continue
                task = tasks.get(run.task_id)
                path = artifact.path or _short_scalar(artifact.metadata_.get("relative_path"))
                deliverables.append(ScheduledDeliverableView(
                    id=f"artifact:{artifact.id}", run_id=run.id, task_id=run.task_id,
                    conversation_title=task.title if task else "未命名对话",
                    kind=file_deliverable_kind(artifact.mime_type, path),
                    title=str(artifact.metadata_.get("filename") or Path(path or artifact.type).name),
                    summary=path, mime_type=artifact.mime_type, size_bytes=artifact.size_bytes,
                    content_url=f"/api/deliverables/artifacts/{artifact.id}/content",
                    metadata={
                        "source": "workspace" if artifact.type == "workspace_snapshot" else "artifact",
                        "artifact_type": artifact.type,
                        **({"path": path} if path else {}),
                    },
                    created_at=artifact.created_at,
                ))
            for file, workspace, task in file_rows:
                if (
                    (workspace.id, file.relative_path) in scheduled_locations
                    or (task.id, file.relative_path) in snapshotted_task_paths
                ):
                    continue
                latest_change = latest_change_by_location.get(
                    (workspace.id, file.relative_path)
                )
                deliverables.append(ScheduledDeliverableView(
                    id=f"workspace-file:{file.id}",
                    run_id=latest_change.run_id if latest_change else None,
                    task_id=task.id, conversation_title=task.title,
                    kind=file_deliverable_kind(file.mime_type, file.relative_path),
                    title=Path(file.relative_path).name, summary=file.relative_path,
                    mime_type=file.mime_type, size_bytes=file.size_bytes,
                    content_url=f"/api/tasks/{task.id}/workspace/files/{file.id}/content",
                    metadata={"source": "workspace", "path": file.relative_path, "version": "current"},
                    created_at=file.updated_at,
                ))

        deliverables.sort(
            key=lambda item: (
                item.created_at
                if item.created_at.tzinfo is not None
                else item.created_at.replace(tzinfo=timezone.utc)
            ).timestamp(),
            reverse=True,
        )
        return deliverables[:limit]
