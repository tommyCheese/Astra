from pathlib import Path

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.scheduling.dispatcher import ScheduledRunDispatcher
from app.application.scheduling.execution import ScheduledExecutionResolver
from app.application.workspaces.artifacts import LocalArtifactStore
from app.application.workspaces.deliverables import DeliverableCatalog
from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.core.errors import (
    AstraInputValidationError,
    AstraResourceNotFoundError,
    AstraStateConflictError,
)
from app.common.schemas.schedules import (
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
from app.infrastructure.db.models.scheduling import ScheduledJobRunRecord
from app.infrastructure.db.models.workspaces import ArtifactRecord
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.conversations import ConversationRepository
from app.infrastructure.repositories.heartbeats import HeartbeatRepository
from app.infrastructure.repositories.schedules import (
    ScheduleNotFoundError,
    ScheduleRepository,
    ScheduleVersionConflictError,
    SystemManagedScheduleError,
)
from app.interfaces.platform.http.dependencies import (
    AstraApplicationServices,
    get_application_container,
)

router = APIRouter(prefix="/api", tags=["scheduled-tasks"])


def _translate_schedule_error(exc: Exception) -> Exception:
    if isinstance(exc, ScheduleNotFoundError):
        return AstraResourceNotFoundError("SCHEDULE_NOT_FOUND", "找不到指定的已安排任务。")
    if isinstance(exc, ScheduleVersionConflictError):
        return AstraStateConflictError(
            "SCHEDULE_VERSION_CONFLICT",
            "已安排任务已被其他操作更新，请刷新后重试。",
        )
    if isinstance(exc, SystemManagedScheduleError):
        return AstraStateConflictError(
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
    settings: AstraRuntimeSettings = Depends(get_settings),
):
    if await ConversationRepository(session).get(payload.target_task_id) is None:
        raise AstraResourceNotFoundError("CONVERSATION_NOT_FOUND", "找不到定时任务的目标对话。")
    execution = payload.execution or await ScheduledExecutionResolver(session, settings).for_management(
        payload.target_task_id,
    )
    resolved = ScheduledJobCreate.model_validate({**payload.model_dump(exclude={"execution"}), "execution": execution})
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
    settings: AstraRuntimeSettings = Depends(get_settings),
):
    if "target_task_id" in payload.model_fields_set:
        if payload.target_task_id is None:
            raise AstraInputValidationError("SCHEDULE_TARGET_REQUIRED", "定时任务必须绑定结果对话。")
        if await ConversationRepository(session).get(payload.target_task_id) is None:
            raise AstraResourceNotFoundError("CONVERSATION_NOT_FOUND", "找不到定时任务的目标对话。")
        if payload.execution is None:
            execution = await ScheduledExecutionResolver(session, settings).for_management(payload.target_task_id)
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
    container: AstraApplicationServices = Depends(get_application_container),
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
):
    repo = ScheduleRepository(session)
    try:
        job = await repo.require(job_id)
        if not job.enabled:
            raise AstraStateConflictError("SCHEDULE_DISABLED", "已安排任务当前处于暂停状态。")
        schedule_run = await repo.manual_trigger(
            job,
            idempotency_key=payload.idempotency_key,
            claimed_by="scheduled-tasks-api",
        )
        return await ScheduledRunDispatcher(
            settings,
            container.session_factory,
            container.run_dispatcher,
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

    return await DeliverableCatalog(session).list(job_id=job_id, limit=limit)


@router.get("/schedules/{job_id}/deliverables/{artifact_id}/content")
async def scheduled_deliverable_content(
    job_id: str,
    artifact_id: str,
    inline: bool = False,
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
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
    if artifact is None or not artifact.storage_key or artifact.security_status != "verified":
        raise AstraResourceNotFoundError("SCHEDULE_DELIVERABLE_NOT_FOUND", "找不到可访问的制品。")
    path = LocalArtifactStore(settings.artifact_store_path).resolve(artifact.storage_key)
    if not path.is_file():
        raise AstraResourceNotFoundError("SCHEDULE_DELIVERABLE_NOT_FOUND", "制品内容已不可用。")
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=str(artifact.metadata_.get("filename") or Path(artifact.path or path).name),
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/heartbeat", response_model=ScheduledJobView | None)
async def get_heartbeat(session: AsyncSession = Depends(get_session)):
    return await HeartbeatRepository(session).get()


@router.put("/heartbeat", response_model=ScheduledJobView)
async def put_heartbeat(
    payload: HeartbeatConfigRequest,
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
):
    if payload.interval_seconds < settings.scheduler_heartbeat_min_interval_seconds:
        raise AstraInputValidationError(
            "HEARTBEAT_INTERVAL_TOO_SHORT",
            "heartbeat 周期低于系统允许的最小值。",
            {"minimum_seconds": settings.scheduler_heartbeat_min_interval_seconds},
        )
    execution = payload.execution or await ScheduledExecutionResolver(session, settings).for_management(
        payload.target_task_id,
        workspace_fallback=False,
    )
    resolved = HeartbeatConfig.model_validate({**payload.model_dump(exclude={"execution"}), "execution": execution})
    return await HeartbeatRepository(session).upsert(
        resolved,
        owner_principal="local-user",
    )


@router.post("/heartbeat/disable", response_model=ScheduledJobView)
async def disable_heartbeat(session: AsyncSession = Depends(get_session)):
    try:
        return await HeartbeatRepository(session).disable()
    except Exception as exc:
        _raise_schedule_error(exc)
