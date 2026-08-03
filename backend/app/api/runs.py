import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts import LocalArtifactStore
from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.session import SessionLocal, get_session
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.permissions import PermissionRepository
from app.repositories.plans import PlanRepository, diff_plans, plan_to_summary, plan_to_view
from app.repositories.runs import RunRepository, run_to_initial_view, run_to_view
from app.repositories.tool_settings import (
    ToolSettingsRepository,
    apply_tool_states,
    default_tool_states,
)
from app.run_creation import RunCreationService
from app.runner.engine import start_run_in_process
from app.runner.plan_revision import PlanRevisionError, revise_waiting_plan
from app.runtime_events import run_event_broker
from app.schemas.agent import (
    ApprovalDecisionRequest,
    ContinuationAction,
    ContinueRunRequest,
    CreateRunRequest,
    CreateRunResponse,
    PlanGraphDiff,
    PlanVersionSummary,
    PlanView,
    RunView,
)
from app.subagents.lifecycle import SubagentCancellationService
from app.subagents.observability import SubagentTelemetryRepository

router = APIRouter(prefix="/api", tags=["runs"])
logger = logging.getLogger("astra.runs")
SSE_FALLBACK_POLL_SECONDS = 0.2
_background_tasks: set[asyncio.Task[None]] = set()
_background_tasks_by_run: dict[str, asyncio.Task[None]] = {}
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _run_event_stream(
    run_id: str,
    *,
    after_id: int = 0,
    ready_payload: dict[str, object] | None = None,
    start_after_ready: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    if not isinstance(after_id, int):
        after_id = 0
    logger.info("sse.open run_id=%s after_id=%s", run_id, after_id)
    last_id = after_id
    broker_version = run_event_broker.subscribe(run_id)
    database_refresh_required = True
    status: str | None = None
    run_sequence = after_id
    agent_sequences: dict[str, int] = {}
    try:
        try:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "stream.ready",
                        "payload": ready_payload or {"run_id": run_id},
                    }
                )
                + "\n\n"
            )
            # Give the ASGI server one scheduling turn to flush the response
            # headers and ready event before the run engine starts doing work.
            await asyncio.sleep(0)
        finally:
            if start_after_ready is not None:
                start_after_ready()
        if start_after_ready is not None:
            # Let the newly-created engine task reach its first async I/O
            # before this stream performs the initial database replay.
            await asyncio.sleep(0)
        while True:
            published = (
                None
                if database_refresh_required
                else run_event_broker.events_after(run_id, last_id)
            )
            if published is None:
                async with SessionLocal() as stream_session:
                    stream_repo = RunRepository(stream_session)
                    events, status = await stream_repo.list_events_with_status(run_id, last_id)
                    payloads = [
                        {
                            "id": event.id,
                            "run_sequence": event.id,
                            "agent_execution_id": event.agent_execution_id,
                            "type": event.type,
                            "payload": event.payload,
                            "created_at": event.created_at.isoformat(),
                        }
                        for event in events
                    ]
                if payloads:
                    last_id = payloads[-1]["id"]
                run_event_broker.mark_database_synced(run_id, last_id)
            else:
                payloads = [
                    {
                        "id": event.id,
                        "run_sequence": event.id,
                        "agent_execution_id": event.agent_execution_id,
                        "type": event.type,
                        "payload": event.payload,
                        "created_at": event.created_at,
                    }
                    for event in published
                ]
                for event in published:
                    if event.type == "run.status_changed":
                        status = event.payload.get("status", status)
                    elif event.type == "run.cancelled":
                        status = "cancelled"
            for payload in payloads:
                run_sequence += 1
                payload["run_sequence"] = run_sequence
                agent_execution_id = payload.get("agent_execution_id")
                if isinstance(agent_execution_id, str):
                    agent_sequence = agent_sequences.get(agent_execution_id, 0) + 1
                    agent_sequences[agent_execution_id] = agent_sequence
                    payload["agent_sequence"] = agent_sequence
                last_id = payload["id"]
                yield f"id: {payload['id']}\ndata: {json.dumps(payload)}\n\n"
            if status in RunRepository.TERMINAL_STATUSES:
                if not payloads:
                    yield 'data: {"type": "heartbeat", "payload": {}}\n\n'
                break
            next_version = await run_event_broker.wait_for_change(
                run_id,
                broker_version,
                timeout=SSE_FALLBACK_POLL_SECONDS,
            )
            database_refresh_required = next_version == broker_version
            broker_version = next_version
    finally:
        run_event_broker.unsubscribe(run_id)
    logger.info("sse.close run_id=%s last_id=%s", run_id, last_id)


def _streaming_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _restore_run_model_config(
    settings: Settings,
    model_policy: dict,
) -> Settings:
    provider = str(model_policy.get("provider") or "")
    model = str(model_policy.get("model") or "")
    base_url = str(model_policy.get("base_url") or "")
    if not provider or not model:
        return settings
    if settings.model_provider != provider or settings.model_name != model:
        raise ValidationError(
            "RUN_MODEL_MISMATCH",
            "继续运行时必须使用该任务开始时选择的模型。",
            {"provider": provider, "model": model},
        )
    return settings.model_copy(
        update={
            "model_provider": provider,
            "model_name": model,
            "model_base_url": base_url or settings.model_base_url,
        }
    )


def _schedule_run(run_id: str, settings: Settings) -> asyncio.Task[None]:
    """Keep a strong reference to in-process runs until they finish."""
    task = asyncio.create_task(
        start_run_in_process(run_id, settings),
        name=f"astra-run-{run_id}",
    )
    _background_tasks.add(task)
    _background_tasks_by_run[run_id] = task
    task.add_done_callback(lambda completed: _finish_background_task(run_id, completed))
    return task


def _finish_background_task(run_id: str, task: asyncio.Task[None]) -> None:
    _background_tasks.discard(task)
    if _background_tasks_by_run.get(run_id) is task:
        _background_tasks_by_run.pop(run_id, None)
    _report_background_failure(task)


async def _cancel_background_run(run_id: str) -> bool:
    task = _background_tasks_by_run.get(run_id)
    if task is None or task.done():
        return False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        # Cancellation is finalized authoritatively by the request session
        # below. A best-effort background cleanup failure must not turn the
        # user's stop action into a database availability error.
        logger.warning(
            "run.background.cancel_cleanup_failed run_id=%s cause=%s",
            run_id,
            type(exc).__name__,
        )
    return True


def _report_background_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        logger.info("run.background.cancelled task=%s", task.get_name())
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "run.background.failed task=%s cause=%s",
            task.get_name(),
            type(error).__name__,
            exc_info=(type(error), error, error.__traceback__),
        )


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    workspace_id: str | None = Header(default=None, alias="X-Astra-Workspace-Id"),
):
    scoped = await RunRepository(session).get_artifact_with_workspace(artifact_id)
    artifact, required_workspace = scoped if scoped else (None, None)
    if artifact is None or not artifact.storage_key or artifact.security_status != "verified":
        raise ResourceError("ARTIFACT_NOT_FOUND", "找不到可访问的工件。")
    if required_workspace and workspace_id != required_workspace:
        raise ResourceError("ARTIFACT_NOT_FOUND", "找不到可访问的工件。")
    path = LocalArtifactStore(settings.artifact_store_path).resolve(artifact.storage_key)
    if not path.is_file():
        raise ResourceError("ARTIFACT_NOT_FOUND", "工件内容已不可用。")
    return FileResponse(
        path, media_type=artifact.mime_type, filename=artifact.metadata_.get("filename")
    )


@router.get("/runs", response_model=list[RunView])
async def list_runs(
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[RunView]:
    runs = await RunRepository(session).list_recent_runs(limit)
    return [RunView.model_validate(run_to_view(run)) for run in runs]


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(
    payload: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    created, run_settings = await RunCreationService(session, settings).create(payload)
    _schedule_run(created.run_id, run_settings)
    return created


@router.post("/runs/stream")
async def create_run_stream(
    payload: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Create a run and stream it on the same HTTP request."""
    created, run_settings = await RunCreationService(session, settings).create(payload)
    # The streaming response outlives this endpoint's dependency scope.
    await session.rollback()
    return _streaming_response(
        _run_event_stream(
            created.run_id,
            ready_payload={
                "run_id": created.run_id,
                "task_id": created.task_id,
                "status": created.status,
                "answer_mode": created.answer_mode.value,
            },
            start_after_ready=lambda: _schedule_run(created.run_id, run_settings),
        )
    )


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: str,
    detail: str = Query(default="full", pattern="^(full|initial)$"),
    session: AsyncSession = Depends(get_session),
) -> RunView:
    repo = RunRepository(session)
    if detail == "initial":
        run, loaded_full = await repo.get_run_initial(run_id)
    else:
        run, loaded_full = await repo.get_run(run_id), True
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    payload = run_to_view(run) if loaded_full else run_to_initial_view(run)
    return RunView.model_validate(payload)


@router.get("/runs/{run_id}/plans", response_model=list[PlanVersionSummary])
async def list_run_plans(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[PlanVersionSummary]:
    run = await RunRepository(session).get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    if run.answer_mode != "trusted":
        return []
    plans = await PlanRepository(session).list_for_run(run_id)
    return [plan_to_summary(plan) for plan in plans]


@router.get("/runs/{run_id}/plans/{version}", response_model=PlanView)
async def get_run_plan(
    run_id: str,
    version: int,
    session: AsyncSession = Depends(get_session),
) -> PlanView:
    plan = await PlanRepository(session).by_version(run_id, version)
    if plan is None:
        raise ResourceError("PLAN_NOT_FOUND", "找不到指定计划版本。")
    return plan_to_view(plan)


@router.get("/runs/{run_id}/plans/{version}/diff", response_model=PlanGraphDiff)
async def get_run_plan_diff(
    run_id: str,
    version: int,
    from_version: int = Query(ge=1),
    session: AsyncSession = Depends(get_session),
) -> PlanGraphDiff:
    repository = PlanRepository(session)
    before = await repository.by_version(run_id, from_version)
    after = await repository.by_version(run_id, version)
    if before is None or after is None:
        raise ResourceError("PLAN_NOT_FOUND", "找不到指定计划版本。")
    if before.version >= after.version:
        raise ValidationError("PLAN_DIFF_INVALID", "只能比较较早计划与较新计划。")
    return diff_plans(before, after)


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunView:
    repo = RunRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    if run.status in RunRepository.TERMINAL_STATUSES and run.status != "waiting_user":
        return RunView.model_validate(run_to_view(run))

    await _cancel_background_run(run_id)
    await session.rollback()
    run = await repo.get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    if run.status not in RunRepository.TERMINAL_STATUSES or run.status == "waiting_user":
        run = await repo.cancel_run(run_id)
    return RunView.model_validate(run_to_view(run))


@router.post(
    "/runs/{run_id}/agents/{agent_execution_id}/cancel",
    response_model=RunView,
)
async def cancel_subagent(
    run_id: str,
    agent_execution_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunView:
    execution = await AgentExecutionRepository(session).require(agent_execution_id)
    if execution.run_id != run_id or execution.parent_execution_id is None:
        raise ResourceError("SUBAGENT_NOT_FOUND", "找不到指定子系统执行。")
    await SubagentCancellationService(session).cancel_tree(
        agent_execution_id,
        reason="user_cancelled_child",
    )
    run = await RunRepository(session).get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    return RunView.model_validate(run_to_view(run))


@router.get("/runs/{run_id}/subagents/metrics")
async def get_subagent_metrics(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    try:
        return await SubagentTelemetryRepository(session).summary(run_id)
    except ValueError as exc:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。") from exc


@router.post("/runs/{run_id}/resume", response_model=CreateRunResponse)
async def resume_run(
    run_id: str,
    payload: ContinueRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    repo = RunRepository(session)
    existing_run = await repo.require_run(run_id)
    tool_states = await ToolSettingsRepository(session).get_or_create(default_tool_states(settings))
    run_settings = RunCreationService.apply_model_config(
        apply_tool_states(settings, tool_states), payload.model
    )
    run_settings = _restore_run_model_config(run_settings, existing_run.model_policy)
    try:
        if payload.action == ContinuationAction.execute_plan:
            run = await repo.confirm_waiting_plan(
                run_id,
                continuation_token=payload.continuation_token or "",
                plan_id=payload.plan_id or "",
                expected_plan_version=payload.expected_plan_version or 0,
                expected_state_version=payload.expected_state_version or 0,
            )
        elif payload.action == ContinuationAction.revise_plan:
            run = await revise_waiting_plan(
                repo,
                run_settings,
                run_id=run_id,
                request=payload.content or "",
                continuation_token=payload.continuation_token or "",
                plan_id=payload.plan_id or "",
                expected_plan_version=payload.expected_plan_version or 0,
                expected_state_version=payload.expected_state_version or 0,
            )
        else:
            run = await repo.resume_waiting_run(
                run_id,
                {
                    "kind": "approval_result" if payload.approved is not None else "user_response",
                    "status": "approved"
                    if payload.approved
                    else "rejected"
                    if payload.approved is False
                    else "received",
                    "summary": payload.content,
                    "data": {"approved": payload.approved},
                },
                continuation_token=payload.continuation_token,
            )
    except ValueError as exc:
        message = str(exc)
        if isinstance(exc, PlanRevisionError):
            raise ValidationError(
                exc.code,
                "计划调整未通过校验，原计划仍可继续使用。",
            ) from exc
        if "plan revision" in message:
            raise StateError(
                "PLAN_REVISION_STALE",
                "计划已变化，请刷新后基于最新版本调整。",
            ) from exc
        if "plan confirmation" in message:
            raise StateError(
                "PLAN_CONFIRMATION_INVALID",
                "计划确认已失效，请刷新后核对最新计划。",
            ) from exc
        if "not waiting" in message:
            raise StateError("RUN_NOT_WAITING", "该任务当前不需要补充信息。") from exc
        if "continuation token" in message:
            raise StateError("CONTINUATION_INVALID", "任务恢复凭据已失效，请刷新后重试。") from exc
        raise StateError("RUN_RESUME_CONFLICT", "当前任务无法恢复。") from exc
    if payload.action != ContinuationAction.revise_plan:
        _schedule_run(run.id, run_settings)
    return CreateRunResponse(
        task_id=run.task_id,
        run_id=run.id,
        status=run.status,
        answer_mode=run.answer_mode,
    )


@router.post(
    "/runs/{run_id}/approvals/{approval_id}/decision",
    response_model=CreateRunResponse,
)
async def decide_tool_approval(
    run_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    repo = RunRepository(session)
    existing_run = await repo.require_run(run_id)
    tool_states = await ToolSettingsRepository(session).get_or_create(default_tool_states(settings))
    run_settings = RunCreationService.apply_model_config(
        apply_tool_states(settings, tool_states), payload.model
    )
    run_settings = _restore_run_model_config(run_settings, existing_run.model_policy)
    try:
        run = existing_run
        reviewer = await PermissionRepository(session).get_or_create_identity(
            identity_type="reviewer",
            principal="local-user",
            task_id=run.task_id,
            run_id=run_id,
            trust_level="user",
        )
        await repo.decide_approval(
            run_id,
            approval_id,
            payload.decision.value,
            continuation_token=payload.continuation_token,
            reviewer_identity={
                "id": reviewer.id,
                "identity_type": reviewer.identity_type,
                "principal": reviewer.principal,
            },
            rejection_guidance=payload.guidance,
        )
    except ValueError as exc:
        message = str(exc)
        if "continuation token" in message:
            raise StateError("CONTINUATION_INVALID", "批准凭据已失效，请刷新后重试。") from exc
        if "already been decided" in message:
            raise StateError("APPROVAL_ALREADY_DECIDED", "该工具调用已经处理。") from exc
        if "not available" in message:
            raise StateError(
                "SIMILAR_APPROVAL_UNAVAILABLE", "该命令不能使用相似命令授权。"
            ) from exc
        raise StateError("APPROVAL_CONFLICT", "该批准请求当前无法处理。") from exc
    run = await repo.require_run(run_id)
    _schedule_run(run_id, run_settings)
    return CreateRunResponse(
        task_id=run.task_id,
        run_id=run.id,
        status=run.status,
        answer_mode=run.answer_mode,
    )


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    repo = RunRepository(session)
    if await repo.get_run_status(run_id) is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    # FastAPI keeps dependency scopes alive until a streaming response closes.
    # End the existence-check transaction now so an idle SSE connection does not
    # pin a database connection for the lifetime of the run.
    await session.rollback()
    return _streaming_response(_run_event_stream(run_id, after_id=after_id))
