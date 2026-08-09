import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.run_management.lifecycle.service import RunApplicationService
from app.application.run_management.projections.events import run_event_broker
from app.application.run_management.projections.query_service import (
    initial_run,
    recent_runs,
    run_detail,
)
from app.application.subagents.lifecycle import SubagentCancellationService
from app.application.subagents.observability import SubagentTelemetryRepository
from app.application.workspaces.artifacts import LocalArtifactStore
from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.core.errors import AstraInputValidationError, AstraResourceNotFoundError
from app.common.schemas.agent.api_views import (
    ContinueRunRequest,
    CreateRunRequest,
    CreateRunResponse,
    RunView,
)
from app.common.schemas.agent.planning import PlanGraphDiff, PlanVersionSummary, PlanView
from app.common.schemas.agent.tool_invocation import ApprovalDecisionRequest
from app.infrastructure.db.session import SessionLocal, get_session
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.plans import (
    PlanRepository,
    diff_plans,
    plan_to_summary,
    plan_to_view,
)
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.run_view_projection import run_view
from app.interfaces.platform.http.dependencies import (
    AstraApplicationServices,
    get_application_container,
)

router = APIRouter(prefix="/api", tags=["runs"])
logger = logging.getLogger("astra.runs")
SSE_FALLBACK_POLL_SECONDS = 0.2
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def get_run_application_service(
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
    container: AstraApplicationServices = Depends(get_application_container),
) -> RunApplicationService:
    return RunApplicationService(session, settings, container.run_dispatcher)


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
            await asyncio.sleep(0)
        finally:
            if start_after_ready is not None:
                start_after_ready()
        if start_after_ready is not None:
            await asyncio.sleep(0)
        while True:
            published = None if database_refresh_required else run_event_broker.events_after(run_id, last_id)
            if published is None:
                payloads, status = await _database_event_payloads(run_id, last_id)
                if payloads:
                    last_id = payloads[-1]["id"]
                run_event_broker.mark_database_synced(run_id, last_id)
            else:
                payloads = _published_event_payloads(published)
                for event in published:
                    if event.type == "run.status_changed":
                        status = event.payload.get("status", status)
                    elif event.type == "run.cancelled":
                        status = "cancelled"
            for payload in payloads:
                run_sequence += 1
                _assign_event_sequences(payload, run_sequence, agent_sequences)
                last_id = payload["id"]
                yield f"id: {payload['id']}\ndata: {json.dumps(payload)}\n\n"
            if status in RunUnitOfWork.TERMINAL_STATUSES:
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


async def _database_event_payloads(run_id: str, after_id: int) -> tuple[list[dict], str | None]:
    async with SessionLocal() as stream_session:
        events, status = await RunUnitOfWork(stream_session).list_events_with_status(run_id, after_id)
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
    return payloads, status


def _published_event_payloads(events) -> list[dict]:
    return [
        {
            "id": event.id,
            "run_sequence": event.id,
            "agent_execution_id": event.agent_execution_id,
            "type": event.type,
            "payload": event.payload,
            "created_at": event.created_at,
        }
        for event in events
    ]


def _assign_event_sequences(payload: dict, run_sequence: int, agent_sequences: dict[str, int]) -> None:
    payload["run_sequence"] = run_sequence
    agent_execution_id = payload.get("agent_execution_id")
    if isinstance(agent_execution_id, str):
        agent_sequence = agent_sequences.get(agent_execution_id, 0) + 1
        agent_sequences[agent_execution_id] = agent_sequence
        payload["agent_sequence"] = agent_sequence


def _streaming_response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(
    artifact_id: str,
    session: AsyncSession = Depends(get_session),
    settings: AstraRuntimeSettings = Depends(get_settings),
    workspace_id: str | None = Header(default=None, alias="X-Astra-Workspace-Id"),
):
    scoped = await RunUnitOfWork(session).get_artifact_with_workspace(artifact_id)
    artifact, required_workspace = scoped if scoped else (None, None)
    if artifact is None or not artifact.storage_key or artifact.security_status != "verified":
        raise AstraResourceNotFoundError("ARTIFACT_NOT_FOUND", "找不到可访问的工件。")
    if required_workspace and workspace_id != required_workspace:
        raise AstraResourceNotFoundError("ARTIFACT_NOT_FOUND", "找不到可访问的工件。")
    path = LocalArtifactStore(settings.artifact_store_path).resolve(artifact.storage_key)
    if not path.is_file():
        raise AstraResourceNotFoundError("ARTIFACT_NOT_FOUND", "工件内容已不可用。")
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.metadata_.get("filename"))


@router.get("/runs", response_model=list[RunView])
async def list_runs(
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[RunView]:
    return await recent_runs(RunUnitOfWork(session), limit)


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(
    payload: CreateRunRequest,
    service: RunApplicationService = Depends(get_run_application_service),
) -> CreateRunResponse:
    return await service.create_and_start(payload)


@router.post("/runs/stream")
async def create_run_stream(
    payload: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    service: RunApplicationService = Depends(get_run_application_service),
) -> StreamingResponse:
    """Create a run and stream it on the same HTTP request."""
    prepared_run = await service.prepare(payload)
    created = prepared_run.response
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
            start_after_ready=lambda: service.start(prepared_run),
        )
    )


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: str,
    detail: str = Query(default="full", pattern="^(full|initial)$"),
    session: AsyncSession = Depends(get_session),
) -> RunView:
    reader = RunUnitOfWork(session)
    run_view = await (initial_run(reader, run_id) if detail == "initial" else run_detail(reader, run_id))
    if run_view is None:
        raise AstraResourceNotFoundError("RUN_NOT_FOUND", "找不到指定运行记录。")
    return run_view


@router.get("/runs/{run_id}/plans", response_model=list[PlanVersionSummary])
async def list_run_plans(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> list[PlanVersionSummary]:
    run = await RunUnitOfWork(session).get_run(run_id)
    if run is None:
        raise AstraResourceNotFoundError("RUN_NOT_FOUND", "找不到指定运行记录。")
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
        raise AstraResourceNotFoundError("PLAN_NOT_FOUND", "找不到指定计划版本。")
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
        raise AstraResourceNotFoundError("PLAN_NOT_FOUND", "找不到指定计划版本。")
    if before.version >= after.version:
        raise AstraInputValidationError("PLAN_DIFF_INVALID", "只能比较较早计划与较新计划。")
    return diff_plans(before, after)


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(
    run_id: str,
    service: RunApplicationService = Depends(get_run_application_service),
) -> RunView:
    run = await service.cancel(run_id)
    return run_view(run)


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
        raise AstraResourceNotFoundError("SUBAGENT_NOT_FOUND", "找不到指定子系统执行。")
    await SubagentCancellationService(session).cancel_tree(
        agent_execution_id,
        reason="user_cancelled_child",
    )
    run_view = await run_detail(RunUnitOfWork(session), run_id)
    if run_view is None:
        raise AstraResourceNotFoundError("RUN_NOT_FOUND", "找不到指定运行记录。")
    return run_view


@router.get("/runs/{run_id}/subagents/metrics")
async def get_subagent_metrics(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    try:
        return await SubagentTelemetryRepository(session).summary(run_id)
    except ValueError as exc:
        raise AstraResourceNotFoundError("RUN_NOT_FOUND", "找不到指定运行记录。") from exc


@router.post("/runs/{run_id}/resume", response_model=CreateRunResponse)
async def resume_run(
    run_id: str,
    payload: ContinueRunRequest,
    service: RunApplicationService = Depends(get_run_application_service),
) -> CreateRunResponse:
    return await service.resume_and_start(run_id, payload)


@router.post(
    "/runs/{run_id}/approvals/{approval_id}/decision",
    response_model=CreateRunResponse,
)
async def decide_tool_approval(
    run_id: str,
    approval_id: str,
    payload: ApprovalDecisionRequest,
    service: RunApplicationService = Depends(get_run_application_service),
) -> CreateRunResponse:
    return await service.decide_approval_and_start(run_id, approval_id, payload)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    repo = RunUnitOfWork(session)
    if await repo.get_run_status(run_id) is None:
        raise AstraResourceNotFoundError("RUN_NOT_FOUND", "找不到指定运行记录。")
    # FastAPI keeps dependency scopes alive until a streaming response closes.
    # End the existence-check transaction now so an idle SSE connection does not
    # pin a database connection for the lifetime of the run.
    await session.rollback()
    return _streaming_response(_run_event_stream(run_id, after_id=after_id))
