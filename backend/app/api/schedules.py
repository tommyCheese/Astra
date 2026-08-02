from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.session import get_session
from app.repositories.schedules import (
    ScheduleNotFoundError,
    ScheduleRepository,
    ScheduleVersionConflictError,
    SystemManagedScheduleError,
)
from app.schemas.schedules import (
    HeartbeatConfig,
    ScheduledJobCreate,
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
    payload: ScheduledJobCreate,
    session: AsyncSession = Depends(get_session),
):
    return await ScheduleRepository(session).create(payload, owner_principal="local-user")


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
):
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
    session: AsyncSession = Depends(get_session),
):
    repo = ScheduleRepository(session)
    try:
        job = await repo.require(job_id)
        if not job.enabled:
            raise StateError("SCHEDULE_DISABLED", "已安排任务当前处于暂停状态。")
        return await repo.manual_trigger(
            job,
            idempotency_key=payload.idempotency_key,
            claimed_by="scheduled-tasks-api",
        )
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


@router.get("/heartbeat", response_model=ScheduledJobView | None)
async def get_heartbeat(session: AsyncSession = Depends(get_session)):
    return await ScheduleRepository(session).get_heartbeat()


@router.put("/heartbeat", response_model=ScheduledJobView)
async def put_heartbeat(
    payload: HeartbeatConfig,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    if payload.interval_seconds < settings.scheduler_heartbeat_min_interval_seconds:
        raise ValidationError(
            "HEARTBEAT_INTERVAL_TOO_SHORT",
            "heartbeat 周期低于系统允许的最小值。",
            {"minimum_seconds": settings.scheduler_heartbeat_min_interval_seconds},
        )
    return await ScheduleRepository(session).upsert_heartbeat(
        payload,
        owner_principal="local-user",
    )


@router.post("/heartbeat/disable", response_model=ScheduledJobView)
async def disable_heartbeat(session: AsyncSession = Depends(get_session)):
    try:
        return await ScheduleRepository(session).disable_heartbeat()
    except Exception as exc:
        _raise_schedule_error(exc)
