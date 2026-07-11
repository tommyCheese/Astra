import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.repositories.runs import RunRepository, run_to_view
from app.runner.engine import start_run_in_process
from app.schemas.agent import CreateRunRequest, CreateRunResponse, RunView

router = APIRouter(prefix="/api", tags=["runs"])


@router.post("/runs", response_model=CreateRunResponse)
async def create_run(
    payload: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    goal = payload.goal.strip()
    if not goal:
        raise HTTPException(status_code=422, detail="Goal must not be empty")
    repo = RunRepository(session)
    try:
        run = await repo.create_task_run(goal, settings.model_policy, payload.task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
        raise HTTPException(status_code=404, detail="Run not found")
    return RunView.model_validate(run_to_view(run))


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    repo = RunRepository(session)
    if await repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

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
            if run and run.status in {"completed", "completed_with_warnings", "failed", "blocked"}:
                if not events:
                    yield "event: heartbeat\ndata: {}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
