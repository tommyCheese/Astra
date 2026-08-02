from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts import LocalArtifactStore
from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.models import (
    ArtifactRecord,
    RunRecord,
    ScheduledJobRunRecord,
    WorkspaceChangeRecord,
    WorkspaceFileRecord,
)
from app.db.session import get_session
from app.repositories.conversations import ConversationRepository
from app.repositories.schedules import (
    ScheduleNotFoundError,
    ScheduleRepository,
    ScheduleVersionConflictError,
    SystemManagedScheduleError,
)
from app.scheduling.dispatcher import ScheduledRunDispatcher
from app.scheduling.execution import ScheduledExecutionResolver
from app.schemas.schedules import (
    HeartbeatConfig,
    HeartbeatConfigRequest,
    ScheduledDeliverableView,
    ScheduledJobCreate,
    ScheduledJobCreateRequest,
    ScheduledJobKind,
    ScheduledJobManualRunRequest,
    ScheduledJobRunView,
    ScheduledJobUpdate,
    ScheduledJobVersionRequest,
    ScheduledJobView,
)

router = APIRouter(prefix="/api", tags=["scheduled-tasks"])


def _translate_schedule_error(exc: Exception) -> Exception:
    if isinstance(exc, ScheduleNotFoundError):
        return ResourceError("SCHEDULE_NOT_FOUND", "找不到指定的已安排任务。")
    if isinstance(exc, ScheduleVersionConflictError):
        return StateError(
            "SCHEDULE_VERSION_CONFLICT",
            "已安排任务已被其他操作更新，请刷新后重试。",
        )
    if isinstance(exc, SystemManagedScheduleError):
        return StateError(
            "SYSTEM_MANAGED_SCHEDULE",
            "Heartbeat 必须通过 heartbeat 设置修改。",
        )
    return exc


def _raise_schedule_error(exc: Exception):
    translated = _translate_schedule_error(exc)
    if translated is exc:
        raise exc
    raise translated from exc


@router.get("/schedules", response_model=list[ScheduledJobView])
async def list_schedules(
    include_disabled: bool = True,
    kind: ScheduledJobKind | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    return await ScheduleRepository(session).list(
        include_disabled=include_disabled,
        kind=kind,
        limit=limit,
    )


@router.post("/schedules", response_model=ScheduledJobView, status_code=201)
async def create_schedule(
    payload: ScheduledJobCreateRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    if await ConversationRepository(session).get(payload.target_task_id) is None:
        raise ResourceError("CONVERSATION_NOT_FOUND", "找不到定时任务的目标对话。")
    execution = payload.execution or await ScheduledExecutionResolver(
        session, settings
    ).from_task_or_workspace(payload.target_task_id)
    resolved = ScheduledJobCreate.model_validate(
        {**payload.model_dump(exclude={"execution"}), "execution": execution}
    )
    return await ScheduleRepository(session).create(resolved, owner_principal="local-user")


@router.get("/schedules/{job_id}", response_model=ScheduledJobView)
async def get_schedule(job_id: str, session: AsyncSession = Depends(get_session)):
    try:
        return await ScheduleRepository(session).require(job_id)
    except Exception as exc:
        _raise_schedule_error(exc)


@router.patch("/schedules/{job_id}", response_model=ScheduledJobView)
async def update_schedule(
    job_id: str,
    payload: ScheduledJobUpdate,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    if "target_task_id" in payload.model_fields_set:
        if payload.target_task_id is None:
            raise ValidationError("SCHEDULE_TARGET_REQUIRED", "定时任务必须绑定结果对话。")
        if await ConversationRepository(session).get(payload.target_task_id) is None:
            raise ResourceError("CONVERSATION_NOT_FOUND", "找不到定时任务的目标对话。")
        if payload.execution is None:
            execution = await ScheduledExecutionResolver(
                session, settings
            ).from_task_or_workspace(payload.target_task_id)
            payload = payload.model_copy(update={"execution": execution})
    try:
        return await ScheduleRepository(session).update(job_id, payload)
    except Exception as exc:
        _raise_schedule_error(exc)


@router.delete("/schedules/{job_id}", status_code=204)
async def delete_schedule(
    job_id: str,
    version: int = Query(ge=1),
    session: AsyncSession = Depends(get_session),
):
    try:
        await ScheduleRepository(session).delete(job_id, version=version)
    except Exception as exc:
        _raise_schedule_error(exc)
    return Response(status_code=204)


async def _set_schedule_enabled(
    job_id: str,
    payload: ScheduledJobVersionRequest,
    *,
    enabled: bool,
    session: AsyncSession,
):
    try:
        return await ScheduleRepository(session).set_enabled(
            job_id,
            enabled=enabled,
            version=payload.version,
        )
    except Exception as exc:
        _raise_schedule_error(exc)


@router.post("/schedules/{job_id}/pause", response_model=ScheduledJobView)
async def pause_schedule(
    job_id: str,
    payload: ScheduledJobVersionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await _set_schedule_enabled(job_id, payload, enabled=False, session=session)


@router.post("/schedules/{job_id}/resume", response_model=ScheduledJobView)
async def resume_schedule(
    job_id: str,
    payload: ScheduledJobVersionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await _set_schedule_enabled(job_id, payload, enabled=True, session=session)


@router.post("/schedules/{job_id}/run", response_model=ScheduledJobRunView)
async def run_schedule(
    job_id: str,
    payload: ScheduledJobManualRunRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    repo = ScheduleRepository(session)
    try:
        job = await repo.require(job_id)
        if not job.enabled:
            raise StateError("SCHEDULE_DISABLED", "已安排任务当前处于暂停状态。")
        schedule_run = await repo.manual_trigger(
            job,
            idempotency_key=payload.idempotency_key,
            claimed_by="scheduled-tasks-api",
        )
        return await ScheduledRunDispatcher(
            settings,
            request.app.state.session_factory,
        ).dispatch(schedule_run.id)
    except Exception as exc:
        _raise_schedule_error(exc)


@router.get("/schedules/{job_id}/runs", response_model=list[ScheduledJobRunView])
async def list_schedule_runs(
    job_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await ScheduleRepository(session).list_runs(job_id, limit=limit)
    except Exception as exc:
        _raise_schedule_error(exc)


@router.get(
    "/schedules/{job_id}/deliverables",
    response_model=list[ScheduledDeliverableView],
)
async def list_schedule_deliverables(
    job_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    try:
        await ScheduleRepository(session).require(job_id)
    except Exception as exc:
        _raise_schedule_error(exc)

    schedule_runs = list(
        (
            await session.scalars(
                select(ScheduledJobRunRecord)
                .where(
                    ScheduledJobRunRecord.job_id == job_id,
                    ScheduledJobRunRecord.run_id.is_not(None),
                )
                .order_by(ScheduledJobRunRecord.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    run_ids = [item.run_id for item in schedule_runs if item.run_id]
    if not run_ids:
        return []

    runs = {
        item.id: item
        for item in (
            await session.scalars(select(RunRecord).where(RunRecord.id.in_(run_ids)))
        ).all()
    }
    changes = list(
        (
            await session.scalars(
                select(WorkspaceChangeRecord)
                .where(
                    WorkspaceChangeRecord.run_id.in_(run_ids),
                    WorkspaceChangeRecord.deliverable_candidate.is_(True),
                    WorkspaceChangeRecord.change_kind != "deleted",
                )
                .order_by(WorkspaceChangeRecord.created_at.desc())
            )
        ).all()
    )
    workspace_ids = {item.workspace_id for item in changes}
    files = list(
        (
            await session.scalars(
                select(WorkspaceFileRecord).where(
                    WorkspaceFileRecord.workspace_id.in_(workspace_ids),
                    WorkspaceFileRecord.status == "present",
                    WorkspaceFileRecord.deliverable_candidate.is_(True),
                    WorkspaceFileRecord.security_status == "verified",
                )
            )
        ).all()
    ) if workspace_ids else []
    files_by_location = {
        (item.workspace_id, item.relative_path): item for item in files
    }
    artifacts = list(
        (
            await session.scalars(
                select(ArtifactRecord)
                .where(
                    ArtifactRecord.run_id.in_(run_ids),
                    ArtifactRecord.storage_key.is_not(None),
                    ArtifactRecord.security_status == "verified",
                )
                .order_by(ArtifactRecord.created_at.desc())
            )
        ).all()
    )
    artifacts_by_run: dict[str, list[ArtifactRecord]] = {}
    for artifact in artifacts:
        artifacts_by_run.setdefault(artifact.run_id, []).append(artifact)
    changes_by_run: dict[str, list[WorkspaceChangeRecord]] = {}
    for change in changes:
        changes_by_run.setdefault(change.run_id, []).append(change)

    deliverables: list[ScheduledDeliverableView] = []
    for schedule_run in schedule_runs:
        if schedule_run.run_id is None:
            continue
        run = runs.get(schedule_run.run_id)
        if run is None:
            continue
        raw_result = run.result if isinstance(run.result, dict) else {}
        result_summary = str(raw_result.get("summary") or run.summary or "").strip()
        if result_summary:
            deliverables.append(
                ScheduledDeliverableView(
                    id=f"result:{schedule_run.id}",
                    job_id=job_id,
                    schedule_run_id=schedule_run.id,
                    run_id=run.id,
                    task_id=run.task_id,
                    kind="result",
                    title="执行结果",
                    summary=result_summary,
                    metadata={"run_status": run.status},
                    created_at=run.completed_at or run.updated_at,
                )
            )

        emitted_paths: set[str] = set()
        for change in changes_by_run.get(run.id, []):
            if change.relative_path in emitted_paths:
                continue
            file = files_by_location.get((change.workspace_id, change.relative_path))
            if file is None:
                continue
            emitted_paths.add(change.relative_path)
            deliverables.append(
                ScheduledDeliverableView(
                    id=f"workspace-file:{file.id}:{schedule_run.id}",
                    job_id=job_id,
                    schedule_run_id=schedule_run.id,
                    run_id=run.id,
                    task_id=run.task_id,
                    kind="file",
                    title=Path(file.relative_path).name,
                    summary=file.relative_path,
                    mime_type=file.mime_type,
                    size_bytes=file.size_bytes,
                    content_url=(
                        f"/api/tasks/{run.task_id}/workspace/files/{file.id}/content"
                    ),
                    metadata={"source": "workspace", "path": file.relative_path},
                    created_at=change.created_at,
                )
            )
        for artifact in artifacts_by_run.get(run.id, []):
            if artifact.path and artifact.path in emitted_paths:
                continue
            title = str(
                artifact.metadata_.get("filename")
                or (Path(artifact.path).name if artifact.path else artifact.type)
            )
            deliverables.append(
                ScheduledDeliverableView(
                    id=f"artifact:{artifact.id}",
                    job_id=job_id,
                    schedule_run_id=schedule_run.id,
                    run_id=run.id,
                    task_id=run.task_id,
                    kind="file",
                    title=title,
                    summary=artifact.path,
                    mime_type=artifact.mime_type,
                    size_bytes=artifact.size_bytes,
                    content_url=(
                        f"/api/schedules/{job_id}/deliverables/{artifact.id}/content"
                    ),
                    metadata={"source": "artifact", "artifact_type": artifact.type},
                    created_at=artifact.created_at,
                )
            )
    return deliverables[:limit]


@router.get("/schedules/{job_id}/deliverables/{artifact_id}/content")
async def scheduled_deliverable_content(
    job_id: str,
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    artifact = await session.scalar(
        select(ArtifactRecord)
        .join(
            ScheduledJobRunRecord,
            ScheduledJobRunRecord.run_id == ArtifactRecord.run_id,
        )
        .where(
            ScheduledJobRunRecord.job_id == job_id,
            ArtifactRecord.id == artifact_id,
        )
    )
    if (
        artifact is None
        or not artifact.storage_key
        or artifact.security_status != "verified"
    ):
        raise ResourceError("SCHEDULE_DELIVERABLE_NOT_FOUND", "找不到可访问的制品。")
    path = LocalArtifactStore(settings.artifact_store_path).resolve(artifact.storage_key)
    if not path.is_file():
        raise ResourceError("SCHEDULE_DELIVERABLE_NOT_FOUND", "制品内容已不可用。")
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=str(artifact.metadata_.get("filename") or Path(artifact.path or path).name),
    )


@router.get("/heartbeat", response_model=ScheduledJobView | None)
async def get_heartbeat(session: AsyncSession = Depends(get_session)):
    return await ScheduleRepository(session).get_heartbeat()


@router.put("/heartbeat", response_model=ScheduledJobView)
async def put_heartbeat(
    payload: HeartbeatConfigRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    if payload.interval_seconds < settings.scheduler_heartbeat_min_interval_seconds:
        raise ValidationError(
            "HEARTBEAT_INTERVAL_TOO_SHORT",
            "heartbeat 周期低于系统允许的最小值。",
            {"minimum_seconds": settings.scheduler_heartbeat_min_interval_seconds},
        )
    execution = payload.execution or await ScheduledExecutionResolver(session, settings).from_task(
        payload.target_task_id
    )
    resolved = HeartbeatConfig.model_validate(
        {**payload.model_dump(exclude={"execution"}), "execution": execution}
    )
    return await ScheduleRepository(session).upsert_heartbeat(
        resolved,
        owner_principal="local-user",
    )


@router.post("/heartbeat/disable", response_model=ScheduledJobView)
async def disable_heartbeat(session: AsyncSession = Depends(get_session)):
    try:
        return await ScheduleRepository(session).disable_heartbeat()
    except Exception as exc:
        _raise_schedule_error(exc)
