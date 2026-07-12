import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal, get_session
from app.repositories.runs import RunRepository, run_to_view
from app.runner.engine import start_run_in_process
from app.runner.reasoning import PolicyCompiler
from app.core.errors import ResourceError, StateError, ValidationError
from app.schemas.agent import ContinueRunRequest, CreateRunRequest, CreateRunResponse, RunView
from app.artifacts import LocalArtifactStore

router = APIRouter(prefix="/api", tags=["runs"])
logger = logging.getLogger("astra.runs")


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(artifact_id: str, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings), workspace_id: str | None = Header(default=None, alias="X-Astra-Workspace-Id")):
    scoped = await RunRepository(session).get_artifact_with_workspace(artifact_id)
    artifact, required_workspace = scoped if scoped else (None, None)
    if artifact is None or not artifact.storage_key or artifact.security_status != "verified":
        raise ResourceError("ARTIFACT_NOT_FOUND", "找不到可访问的工件。")
    if required_workspace and workspace_id != required_workspace:
        raise ResourceError("ARTIFACT_NOT_FOUND", "找不到可访问的工件。")
    path = LocalArtifactStore(settings.artifact_store_path).resolve(artifact.storage_key)
    if not path.is_file():
        raise ResourceError("ARTIFACT_NOT_FOUND", "工件内容已不可用。")
    return FileResponse(path, media_type=artifact.mime_type, filename=artifact.metadata_.get("filename"))


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
    goal = payload.goal.strip()
    if not goal:
        raise ValidationError("GOAL_REQUIRED", "请输入你想完成的目标。", {"field": "goal"})
    repo = RunRepository(session)
    logger.info(
        "run.create.start task_id=%s provider=%s model=%s goal_chars=%s",
        payload.task_id,
        (payload.model or {}).get("provider", settings.model_provider),
        (payload.model or {}).get("name", settings.model_name),
        len(goal),
    )
    try:
        run_settings = settings
        if payload.model:
            provider = payload.model.get("provider", "")
            if provider not in {"openai", "deepseek", "qwen", "siliconflow", "compatible", "azure"}:
                raise ValidationError("MODEL_PROVIDER_UNSUPPORTED", "当前模型供应商尚未接入通用运行时。")
            run_settings = settings.model_copy(update={
                "model_provider": provider,
                "model_name": payload.model.get("name", ""),
                "model_api_key": payload.model.get("api_key", ""),
                "model_base_url": payload.model.get("base_url", ""),
            })
            if not run_settings.model_name or (provider != "compatible" and not run_settings.model_api_key):
                raise ValidationError("MODEL_CONFIGURATION_REQUIRED", "请先配置模型名称和 API Key。")
        policy = PolicyCompiler().compile(payload.reasoning_policy)
        run = await repo.create_task_run(goal, run_settings.model_policy, payload.task_id, reasoning_policy=policy.model_dump(mode="json"))
        for adjustment in policy.adjustments:
            await repo.add_event(run.id, "reasoning.policy_adjusted", adjustment.model_dump(mode="json"))
        await session.commit()
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Task not found"):
            raise ResourceError("TASK_NOT_FOUND", "找不到指定任务。") from exc
        raise ValidationError("RUN_REQUEST_INVALID", "无法创建任务。") from exc
    asyncio.create_task(start_run_in_process(run.id, run_settings))
    logger.info("run.create.accepted run_id=%s task_id=%s status=%s", run.id, run.task_id, run.status)
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
        logger.info("sse.open run_id=%s after_id=%s", run_id, after_id)
        last_id = after_id
        async with SessionLocal() as stream_session:
            stream_repo = RunRepository(stream_session)
            while True:
                events = await stream_repo.list_events(run_id, last_id)
                for event in events:
                    last_id = event.id
                    payload = {
                        "id": event.id,
                        "type": event.type,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    }
                    yield f"id: {event.id}\ndata: {json.dumps(payload)}\n\n"
                    if event.type == "answer.delta":
                        await asyncio.sleep(0.008)
                run = await stream_repo.get_run(run_id)
                if run and run.status in {"completed", "completed_with_warnings", "failed", "blocked", "waiting_user"}:
                    if not events:
                        yield "data: {\"type\": \"heartbeat\", \"payload\": {}}\n\n"
                    break
                await asyncio.sleep(0.05)
        logger.info("sse.close run_id=%s last_id=%s", run_id, last_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
