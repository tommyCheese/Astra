import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.repositories.runs import RunRepository, run_to_view
from app.runner.engine import start_run_in_process
from app.runner.reasoning import PolicyCompiler
from app.core.errors import ResourceError, StateError, ValidationError
from app.schemas.agent import ContinueRunRequest, CreateRunRequest, CreateRunResponse, RunView

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(
    payload: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    goal = payload.goal.strip()
    if not goal:
        raise ValidationError("GOAL_REQUIRED", "请输入你想完成的目标。", {"field": "goal"})
    repo = RunRepository(session)
    try:
        policy = PolicyCompiler().compile(payload.reasoning_policy)
        run = await repo.create_task_run(goal, settings.model_policy, payload.task_id, reasoning_policy=policy.model_dump(mode="json"))
        for adjustment in policy.adjustments:
            await repo.add_event(run.id, "reasoning.policy_adjusted", adjustment.model_dump(mode="json"))
        await session.commit()
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Task not found"):
            raise ResourceError("TASK_NOT_FOUND", "找不到指定任务。") from exc
        raise ValidationError("RUN_REQUEST_INVALID", "无法创建任务。") from exc
    asyncio.create_task(start_run_in_process(run.id, settings))
    return CreateRunResponse(task_id=run.task_id, run_id=run.id, status=run.status)


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunView:
    repo = RunRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    return RunView.model_validate(run_to_view(run))


@router.post("/runs/{run_id}/resume", response_model=CreateRunResponse)
async def resume_run(
    run_id: str,
    payload: ContinueRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    repo = RunRepository(session)
    try:
        run = await repo.resume_waiting_run(
            run_id,
            {
                "kind": "approval_result" if payload.approved is not None else "user_response",
                "status": "approved" if payload.approved else "rejected" if payload.approved is False else "received",
                "summary": payload.content,
                "data": {"approved": payload.approved},
            },
            continuation_token=payload.continuation_token,
        )
    except ValueError as exc:
        message = str(exc)
        if "not waiting" in message:
            raise StateError("RUN_NOT_WAITING", "该任务当前不需要补充信息。") from exc
        if "continuation token" in message:
            raise StateError("CONTINUATION_INVALID", "任务恢复凭据已失效，请刷新后重试。") from exc
        raise StateError("RUN_RESUME_CONFLICT", "当前任务无法恢复。") from exc
    asyncio.create_task(start_run_in_process(run.id, settings))
    return CreateRunResponse(task_id=run.task_id, run_id=run.id, status=run.status)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    repo = RunRepository(session)
    if await repo.get_run(run_id) is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")

    async def event_stream() -> AsyncIterator[str]:
        last_id = after_id
        while True:
            events = await repo.list_events(run_id, last_id)
            for event in events:
                last_id = event.id
                payload = {
                    "id": event.id,
                    "type": event.type,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                }
                yield f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(payload)}\n\n"
            run = await repo.get_run(run_id)
            if run and run.status in {"completed", "completed_with_warnings", "failed", "blocked", "waiting_user"}:
                if not events:
                    yield "event: heartbeat\ndata: {}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
