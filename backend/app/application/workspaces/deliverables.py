from __future__ import annotations

from datetime import timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas.schedules import ScheduledDeliverableView
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.scheduling import ScheduledJobRecord, ScheduledJobRunRecord
from app.infrastructure.db.models.workspaces import (
    ArtifactRecord,
    TaskWorkspaceRecord,
    WorkspaceChangeRecord,
    WorkspaceFileRecord,
)

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


def _first_scalar(sources, keys, *, limit=500):
    return next(
        (
            value
            for source in sources
            for key in keys
            if (value := _short_scalar(source.get(key), limit=limit)) is not None
        ),
        None,
    )


def _first_external_url(sources):
    return next(
        (
            value
            for source in sources
            for key in ("url", "link", "resource_url")
            if (value := _short_scalar(source.get(key), limit=2000)) is not None
            and value.startswith(("https://", "http://"))
        ),
        None,
    )


def operation_receipt(call: ToolCallRecord) -> dict | None:
    if call.status != "succeeded" or call.side_effect_level not in RECEIPT_SIDE_EFFECT_LEVELS:
        return None
    output = call.output if isinstance(call.output, dict) else {}
    tool_input = call.input if isinstance(call.input, dict) else {}
    receipt_keys = {
        "destination",
        "event_id",
        "id",
        "link",
        "message_id",
        "object_id",
        "resource",
        "resource_id",
        "resource_url",
        "target",
        "url",
    }
    if call.side_effect_level == "external_side_effect" and not receipt_keys.intersection(
        {*output, *tool_input}
    ):
        return None
    summary = (
        _first_scalar((output,), ("summary", "message", "result", "status_text"))
        or f"{call.tool_name} 已成功完成"
    )
    return {
        "summary": summary,
        "status": _short_scalar(output.get("status"), limit=80) or call.status,
        "object_id": _first_scalar(
            (output,),
            ("object_id", "resource_id", "message_id", "event_id", "id"),
            limit=240,
        ),
        "target": _first_scalar((tool_input, output), ("destination", "target", "resource", "url")),
        "external_url": _first_external_url((output, tool_input)),
    }


class DeliverableCatalog:
    """Canonical projection used by both Library and Scheduled Tasks."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(
        self, *, job_id: str | None = None, limit: int = 500
    ) -> list[ScheduledDeliverableView]:
        schedule_runs = await self._schedule_runs(job_id, limit)
        scheduled_by_run = {item.run_id: item for item in schedule_runs if item.run_id}
        artifacts = await self._artifacts(job_id, list(scheduled_by_run), limit)
        runs = await self._records_by_id(
            RunRecord, {*scheduled_by_run, *(item.run_id for item in artifacts)}
        )
        file_rows, latest_changes = await self._library_files(job_id)
        task_ids = {item.task_id for item in runs.values()}
        task_ids.update(task.id for _file, _workspace, task in file_rows)
        tasks = await self._records_by_id(TaskRecord, task_ids)
        jobs = await self._records_by_id(
            ScheduledJobRecord, {item.job_id for item in schedule_runs}
        )
        changes, files_by_location, calls = await self._scheduled_sources(list(scheduled_by_run))
        deliverables, scheduled_locations = self._scheduled_deliverables(
            schedule_runs, runs, tasks, jobs, artifacts, changes, files_by_location, calls
        )
        if job_id is None:
            self._append_library_deliverables(
                deliverables,
                artifacts,
                runs,
                tasks,
                file_rows,
                latest_changes,
                scheduled_by_run,
                scheduled_locations,
            )
        deliverables.sort(key=_deliverable_timestamp, reverse=True)
        return deliverables[:limit]

    async def _schedule_runs(self, job_id, limit):
        schedule_query = select(ScheduledJobRunRecord).where(
            ScheduledJobRunRecord.run_id.is_not(None)
        )
        if job_id is not None:
            schedule_query = schedule_query.where(ScheduledJobRunRecord.job_id == job_id)
        return list(
            (
                await self.session.scalars(
                    schedule_query.order_by(ScheduledJobRunRecord.created_at.desc()).limit(limit)
                )
            ).all()
        )

    async def _artifacts(self, job_id, scheduled_run_ids, limit):
        artifact_query = select(ArtifactRecord).where(
            ArtifactRecord.storage_key.is_not(None),
            ArtifactRecord.security_status == "verified",
        )
        if job_id is not None:
            if not scheduled_run_ids:
                return []
            artifact_query = artifact_query.where(ArtifactRecord.run_id.in_(scheduled_run_ids))
        return list(
            (
                await self.session.scalars(
                    artifact_query.order_by(ArtifactRecord.created_at.desc()).limit(limit)
                )
            ).all()
        )

    async def _library_files(self, job_id):
        file_rows: list[tuple[WorkspaceFileRecord, TaskWorkspaceRecord, TaskRecord]] = []
        latest_change_by_location: dict[tuple[str, str], WorkspaceChangeRecord] = {}
        if job_id is None:
            file_rows = list(
                (
                    await self.session.execute(
                        select(WorkspaceFileRecord, TaskWorkspaceRecord, TaskRecord)
                        .join(
                            TaskWorkspaceRecord,
                            WorkspaceFileRecord.workspace_id == TaskWorkspaceRecord.id,
                        )
                        .join(TaskRecord, TaskWorkspaceRecord.task_id == TaskRecord.id)
                        .where(
                            WorkspaceFileRecord.status == "present",
                            WorkspaceFileRecord.deliverable_candidate.is_(True),
                            WorkspaceFileRecord.security_status == "verified",
                        )
                        .order_by(WorkspaceFileRecord.updated_at.desc())
                    )
                ).all()
            )
            library_workspace_ids = {workspace.id for _file, workspace, _task in file_rows}
            if library_workspace_ids:
                library_changes = list(
                    (
                        await self.session.scalars(
                            select(WorkspaceChangeRecord)
                            .where(
                                WorkspaceChangeRecord.workspace_id.in_(library_workspace_ids),
                                WorkspaceChangeRecord.deliverable_candidate.is_(True),
                                WorkspaceChangeRecord.change_kind != "deleted",
                            )
                            .order_by(WorkspaceChangeRecord.created_at.desc())
                        )
                    ).all()
                )
                for change in library_changes:
                    latest_change_by_location.setdefault(
                        (change.workspace_id, change.relative_path), change
                    )
        return file_rows, latest_change_by_location

    async def _records_by_id(self, model, record_ids):
        return (
            {
                item.id: item
                for item in (
                    await self.session.scalars(select(model).where(model.id.in_(record_ids)))
                ).all()
            }
            if record_ids
            else {}
        )

    async def _scheduled_sources(self, scheduled_run_ids):
        changes = (
            list(
                (
                    await self.session.scalars(
                        select(WorkspaceChangeRecord)
                        .where(
                            WorkspaceChangeRecord.run_id.in_(scheduled_run_ids),
                            WorkspaceChangeRecord.deliverable_candidate.is_(True),
                            WorkspaceChangeRecord.change_kind != "deleted",
                        )
                        .order_by(WorkspaceChangeRecord.created_at.desc())
                    )
                ).all()
            )
            if scheduled_run_ids
            else []
        )
        workspace_ids = {item.workspace_id for item in changes}
        current_files = (
            list(
                (
                    await self.session.scalars(
                        select(WorkspaceFileRecord).where(
                            WorkspaceFileRecord.workspace_id.in_(workspace_ids),
                            WorkspaceFileRecord.status == "present",
                            WorkspaceFileRecord.deliverable_candidate.is_(True),
                            WorkspaceFileRecord.security_status == "verified",
                        )
                    )
                ).all()
            )
            if workspace_ids
            else []
        )
        files_by_location = {
            (item.workspace_id, item.relative_path): item for item in current_files
        }
        tool_calls = (
            list(
                (
                    await self.session.scalars(
                        select(ToolCallRecord)
                        .where(
                            ToolCallRecord.run_id.in_(scheduled_run_ids),
                            ToolCallRecord.status == "succeeded",
                            ToolCallRecord.side_effect_level.in_(RECEIPT_SIDE_EFFECT_LEVELS),
                        )
                        .order_by(ToolCallRecord.completed_at.desc())
                    )
                ).all()
            )
            if scheduled_run_ids
            else []
        )
        return changes, files_by_location, tool_calls

    def _scheduled_deliverables(
        self, schedule_runs, runs, tasks, jobs, artifacts, changes, files_by_location, tool_calls
    ):
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
            run = runs.get(schedule_run.run_id)
            if run is not None:
                projected, locations = _scheduled_run_deliverables(
                    schedule_run,
                    run,
                    tasks.get(run.task_id),
                    jobs.get(schedule_run.job_id),
                    artifacts_by_run.get(run.id, []),
                    changes_by_run.get(run.id, []),
                    files_by_location,
                    calls_by_run.get(run.id, []),
                )
                deliverables.extend(projected)
                scheduled_locations.update(locations)
        return deliverables, scheduled_locations

    def _append_library_deliverables(
        self,
        deliverables,
        artifacts,
        runs,
        tasks,
        file_rows,
        latest_changes,
        scheduled_by_run,
        scheduled_locations,
    ):
        scheduled_artifact_ids = {
            artifact.id for artifact in artifacts if artifact.run_id in scheduled_by_run
        }
        snapshotted_task_paths = {
            (runs[artifact.run_id].task_id, artifact.path)
            for artifact in artifacts
            if artifact.type == "workspace_snapshot" and artifact.path and artifact.run_id in runs
        }
        deliverables.extend(_library_artifacts(artifacts, scheduled_artifact_ids, runs, tasks))
        deliverables.extend(
            _library_workspace_files(
                file_rows, latest_changes, scheduled_locations, snapshotted_task_paths
            )
        )


def _result_deliverable(schedule_run, run, common, summary):
    return ScheduledDeliverableView(
        **common,
        id=f"result:{schedule_run.id}",
        kind="result",
        title="执行结果",
        summary=summary,
        metadata={"run_status": run.status},
        created_at=run.completed_at or run.updated_at,
    )


def _scheduled_run_deliverables(
    schedule_run, run, task, job, artifacts, changes, files_by_location, calls
):
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
    deliverables = []
    raw_result = run.result if isinstance(run.result, dict) else {}
    if summary := str(raw_result.get("summary") or run.summary or "").strip():
        deliverables.append(_result_deliverable(schedule_run, run, common, summary))
    emitted_paths = set()
    for artifact in artifacts:
        path = artifact.path or _short_scalar(artifact.metadata_.get("relative_path"))
        if path:
            emitted_paths.add(path)
        deliverables.append(_artifact_deliverable(artifact, run, common, path))
    locations = _append_changed_files(
        deliverables, schedule_run, run, common, changes, files_by_location, emitted_paths
    )
    for call in calls:
        if receipt := operation_receipt(call):
            deliverables.append(_receipt_deliverable(call, common, receipt))
    return deliverables, locations


def _append_changed_files(
    deliverables, schedule_run, run, common, changes, files_by_location, emitted_paths
):
    locations = set()
    for change in changes:
        locations.add((change.workspace_id, change.relative_path))
        if change.relative_path in emitted_paths:
            continue
        file = files_by_location.get((change.workspace_id, change.relative_path))
        if file is None or (change.after_checksum and file.checksum != change.after_checksum):
            continue
        emitted_paths.add(change.relative_path)
        deliverables.append(
            _workspace_deliverable(
                file,
                run.task_id,
                change.created_at,
                common,
                identifier=f"workspace-file:{file.id}:{schedule_run.id}",
            )
        )
    return locations


def _library_artifacts(artifacts, scheduled_ids, runs, tasks):
    deliverables = []
    for artifact in artifacts:
        run = runs.get(artifact.run_id)
        if artifact.id in scheduled_ids or run is None:
            continue
        task = tasks.get(run.task_id)
        path = artifact.path or _short_scalar(artifact.metadata_.get("relative_path"))
        common = {
            "run_id": run.id,
            "task_id": run.task_id,
            "conversation_title": task.title if task else "未命名对话",
        }
        deliverables.append(_artifact_deliverable(artifact, run, common, path))
    return deliverables


def _library_workspace_files(file_rows, latest_changes, scheduled_locations, snapshots):
    deliverables = []
    for file, workspace, task in file_rows:
        location = (workspace.id, file.relative_path)
        if location in scheduled_locations or (task.id, file.relative_path) in snapshots:
            continue
        latest_change = latest_changes.get(location)
        common = {
            "run_id": latest_change.run_id if latest_change else None,
            "task_id": task.id,
            "conversation_title": task.title,
        }
        deliverables.append(
            _workspace_deliverable(
                file,
                task.id,
                file.updated_at,
                common,
                identifier=f"workspace-file:{file.id}",
            )
        )
    return deliverables


def _artifact_deliverable(artifact, run, common, path):
    title = str(artifact.metadata_.get("filename") or Path(path or artifact.type).name)
    return ScheduledDeliverableView(
        **common,
        id=f"artifact:{artifact.id}",
        kind=file_deliverable_kind(artifact.mime_type, path),
        title=title,
        summary=path,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        content_url=f"/api/deliverables/artifacts/{artifact.id}/content",
        metadata={
            "source": "workspace" if artifact.type == "workspace_snapshot" else "artifact",
            "artifact_type": artifact.type,
            **({"path": path} if path else {}),
        },
        created_at=artifact.created_at,
    )


def _workspace_deliverable(file, task_id, created_at, common, *, identifier):
    return ScheduledDeliverableView(
        **common,
        id=identifier,
        kind=file_deliverable_kind(file.mime_type, file.relative_path),
        title=Path(file.relative_path).name,
        summary=file.relative_path,
        mime_type=file.mime_type,
        size_bytes=file.size_bytes,
        content_url=f"/api/tasks/{task_id}/workspace/files/{file.id}/content",
        metadata={"source": "workspace", "path": file.relative_path, "version": "current"},
        created_at=created_at,
    )


def _receipt_deliverable(call, common, receipt):
    return ScheduledDeliverableView(
        **common,
        id=f"receipt:{call.id}",
        kind="receipt",
        title=f"{call.tool_name} 操作回执",
        summary=receipt["summary"],
        external_url=receipt["external_url"],
        metadata={
            "tool_name": call.tool_name,
            "status": receipt["status"],
            "target": receipt["target"],
            "object_id": receipt["object_id"],
        },
        created_at=call.completed_at or call.started_at,
    )


def _deliverable_timestamp(item):
    created_at = item.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.timestamp()
