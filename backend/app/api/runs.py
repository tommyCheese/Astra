import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_profile import AgentProfileConfigurationError, load_agent_profile
from app.artifacts import LocalArtifactStore
from app.core.config import Settings, get_settings
from app.core.errors import ConfigurationError, ResourceError, StateError, ValidationError
from app.db.session import SessionLocal, get_session
from app.model_providers import API_KEY_OPTIONAL_MODEL_PROVIDERS, SUPPORTED_MODEL_PROVIDERS
from app.permissions.governance import verify_permission_bundle
from app.repositories.plans import PlanRepository, plan_to_view
from app.repositories.permissions import PermissionRepository
from app.repositories.runs import RunRepository, run_to_view
from app.repositories.tool_settings import (
    ToolSettingsRepository,
    apply_tool_states,
    default_tool_states,
)
from app.runner.engine import start_run_in_process
from app.runner.reasoning import RunProfileResolver
from app.schemas.agent import (
    AgentState,
    ApprovalDecisionRequest,
    ContinueRunRequest,
    CreateRunRequest,
    CreateRunResponse,
    RunView,
)
from app.schemas.permissions import PermissionBundle

router = APIRouter(prefix="/api", tags=["runs"])
logger = logging.getLogger("astra.runs")
_background_tasks: set[asyncio.Task[None]] = set()
_background_tasks_by_run: dict[str, asyncio.Task[None]] = {}


def _apply_model_config(settings: Settings, model: dict[str, str] | None) -> Settings:
    if not model:
        return settings
    provider = model.get("provider", "")
    if provider not in SUPPORTED_MODEL_PROVIDERS:
        raise ValidationError("MODEL_PROVIDER_UNSUPPORTED", "当前模型供应商尚未接入通用运行时。")
    configured = settings.model_copy(
        update={
            "model_provider": provider,
            "model_name": model.get("name", ""),
            "model_api_key": model.get("api_key", ""),
            "model_base_url": model.get("base_url", ""),
        }
    )
    if (
        not configured.model_name
        or not configured.model_base_url
        or (provider not in API_KEY_OPTIONAL_MODEL_PROVIDERS and not configured.model_api_key)
    ):
        raise ValidationError(
            "MODEL_CONFIGURATION_REQUIRED", "请先配置模型名称、API 地址和 API Key。"
        )
    return configured


def _schedule_run(run_id: str, settings: Settings) -> None:
    """Keep a strong reference to in-process runs until they finish."""
    task = asyncio.create_task(
        start_run_in_process(run_id, settings),
        name=f"astra-run-{run_id}",
    )
    _background_tasks.add(task)
    _background_tasks_by_run[run_id] = task
    task.add_done_callback(lambda completed: _finish_background_task(run_id, completed))


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
    with suppress(asyncio.CancelledError):
        await task
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
        tool_states = await ToolSettingsRepository(session).get_or_create(
            default_tool_states(settings)
        )
        # Keep the database-backed tool configuration active at creation time.
        run_settings = apply_tool_states(settings, tool_states)
        run_settings = _apply_model_config(run_settings, payload.model)
        profile = RunProfileResolver().resolve(payload.answer_mode, payload.reasoning_policy)
        if not payload.interactive and payload.permission_bundle is None:
            raise ValidationError(
                "PERMISSION_BUNDLE_REQUIRED",
                "无人值守、定时或后台运行必须提供显式权限包。",
            )
        permission_bundle = None
        if payload.permission_bundle is not None:
            try:
                permission_bundle = PermissionBundle.model_validate(
                    payload.permission_bundle
                )
            except ValueError as exc:
                raise ValidationError(
                    "PERMISSION_BUNDLE_INVALID", "权限包格式无效。"
                ) from exc
            if not verify_permission_bundle(
                permission_bundle, settings.permission_bundle_signing_secret
            ):
                raise ValidationError(
                    "PERMISSION_BUNDLE_INVALID", "权限包签名无效或签名密钥未配置。"
                )
        policy = profile.reasoning_policy
        execution_profile = profile.model_dump(mode="json")
        execution_profile["interactive"] = payload.interactive
        execution_profile["permission_bundle"] = (
            permission_bundle.model_dump(mode="json") if permission_bundle else None
        )
        run = await repo.create_task_run(
            goal,
            run_settings.model_policy,
            payload.task_id,
            reasoning_policy=policy.model_dump(mode="json"),
            answer_mode=profile.answer_mode.value,
            execution_profile=execution_profile,
            agent_profile_snapshot=load_agent_profile().snapshot(),
        )
        for adjustment in policy.adjustments:
            await repo.add_event(
                run.id, "reasoning.policy_adjusted", adjustment.model_dump(mode="json")
            )
        await session.commit()
    except AgentProfileConfigurationError as exc:
        raise ConfigurationError(
            "AGENT_PROFILE_INVALID", "Astra 身份配置无效，暂时无法创建任务。"
        ) from exc
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Task not found"):
            raise ResourceError("TASK_NOT_FOUND", "找不到指定任务。") from exc
        raise ValidationError("RUN_REQUEST_INVALID", "无法创建任务。") from exc
    _schedule_run(run.id, run_settings)
    logger.info(
        "run.create.accepted run_id=%s task_id=%s status=%s", run.id, run.task_id, run.status
    )
    return CreateRunResponse(
        task_id=run.task_id,
        run_id=run.id,
        status=run.status,
        answer_mode=profile.answer_mode,
    )


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


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunView:
    repo = RunRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    if run.status in RunRepository.TERMINAL_STATUSES:
        return RunView.model_validate(run_to_view(run))

    await _cancel_background_run(run_id)
    await session.rollback()
    run = await repo.get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    if run.status not in RunRepository.TERMINAL_STATUSES:
        run = await repo.cancel_run(run_id)
    return RunView.model_validate(run_to_view(run))


@router.post("/runs/{run_id}/activate-plan", response_model=CreateRunResponse)
async def activate_planned_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    repo = RunRepository(session)
    run = await repo.get_run(run_id)
    if run is None:
        raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
    plan_repository = PlanRepository(session)
    plan = await plan_repository.latest_planned_for_run(run_id)
    if plan is None:
        raise StateError("PLAN_NOT_ACTIVATABLE", "该任务没有可激活的仅规划结果。")
    plan = await plan_repository.activate(plan.id, expected_version=plan.version)
    state = AgentState.model_validate(run.agent_state or {})
    state.active_plan_id = plan.id
    state.active_plan_version = plan.version
    state.active_node_id = None
    state.version = run.state_version + 1
    await repo.update_reasoning_state(
        run_id,
        expected_version=run.state_version,
        agent_state=state.model_dump(mode="json"),
        plan_graph=plan_to_view(plan).model_dump(mode="json"),
        waiting_state=None,
    )
    run = await repo.require_run(run_id)
    run.status = "executing"
    run.completed_at = None
    run.summary = None
    run.result = None
    await repo.add_event(
        run_id,
        "plan.activated",
        {"plan_id": plan.id, "plan_version": plan.version},
    )
    await session.commit()
    tool_states = await ToolSettingsRepository(session).get_or_create(default_tool_states(settings))
    _schedule_run(run_id, apply_tool_states(settings, tool_states))
    return CreateRunResponse(
        task_id=run.task_id,
        run_id=run.id,
        status=run.status,
        answer_mode=run.answer_mode,
    )


@router.post("/runs/{run_id}/resume", response_model=CreateRunResponse)
async def resume_run(
    run_id: str,
    payload: ContinueRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    repo = RunRepository(session)
    tool_states = await ToolSettingsRepository(session).get_or_create(default_tool_states(settings))
    run_settings = _apply_model_config(apply_tool_states(settings, tool_states), payload.model)
    try:
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
        if "not waiting" in message:
            raise StateError("RUN_NOT_WAITING", "该任务当前不需要补充信息。") from exc
        if "continuation token" in message:
            raise StateError("CONTINUATION_INVALID", "任务恢复凭据已失效，请刷新后重试。") from exc
        raise StateError("RUN_RESUME_CONFLICT", "当前任务无法恢复。") from exc
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
    tool_states = await ToolSettingsRepository(session).get_or_create(default_tool_states(settings))
    run_settings = _apply_model_config(apply_tool_states(settings, tool_states), payload.model)
    try:
        run = await repo.require_run(run_id)
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
            raise StateError("SIMILAR_APPROVAL_UNAVAILABLE", "该命令不能使用相似命令授权。") from exc
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

    async def event_stream() -> AsyncIterator[str]:
        logger.info("sse.open run_id=%s after_id=%s", run_id, after_id)
        last_id = after_id
        yield f"data: {json.dumps({'type': 'stream.ready', 'payload': {'run_id': run_id}})}\n\n"
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
                status = await stream_repo.get_run_status(run_id)
                if status in RunRepository.TERMINAL_STATUSES:
                    if not events:
                        yield 'data: {"type": "heartbeat", "payload": {}}\n\n'
                    break
                await asyncio.sleep(0.05)
        logger.info("sse.close run_id=%s last_id=%s", run_id, last_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
