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
from app.repositories.permissions import PermissionRepository
from app.repositories.plans import PlanRepository, diff_plans, plan_to_summary, plan_to_view
from app.repositories.runs import RunRepository, run_to_initial_view, run_to_view
from app.repositories.tool_settings import (
    ToolSettingsRepository,
    apply_tool_states,
    default_tool_states,
)
from app.runner.engine import start_run_in_process
from app.runner.plan_revision import PlanRevisionError, revise_waiting_plan
from app.runner.reasoning import RunProfileResolver
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
from app.schemas.permissions import PermissionBundle
from app.skills.catalog import SkillActivationService, SkillCatalogBuilder

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
) -> AsyncIterator[str]:
    logger.info("sse.open run_id=%s after_id=%s", run_id, after_id)
    last_id = after_id
    broker_version = run_event_broker.subscribe(run_id)
    database_refresh_required = True
    status: str | None = None
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
        while True:
            published = (
                None
                if database_refresh_required
                else run_event_broker.events_after(run_id, last_id)
            )
            if published is None:
                async with SessionLocal() as stream_session:
                    stream_repo = RunRepository(stream_session)
                    events, status = await stream_repo.list_events_with_status(
                        run_id, last_id
                    )
                    payloads = [
                        {
                            "id": event.id,
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


def _configured_skill_capabilities(settings: Settings) -> set[str]:
    return {
        name
        for name, enabled in {
            "web_search": settings.tool_web_search_enabled,
            "web_fetch": settings.tool_web_fetch_enabled,
            "chart_render": settings.tool_chart_render_enabled,
            "bash_execute": settings.tool_bash_execute_enabled,
            "sandbox": settings.sandbox_enabled,
        }.items()
        if enabled
    }


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
        profile = RunProfileResolver().resolve(
            payload.answer_mode,
            payload.reasoning_policy,
            plan_execution=payload.plan_execution,
        )
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
        profile = profile.model_copy(
            update={
                "interactive": payload.interactive,
                "permission_bundle": (
                    permission_bundle.model_dump(mode="json") if permission_bundle else None
                ),
            }
        )
        execution_profile = profile.model_dump(mode="json")
        run = await repo.create_task_run(
            goal,
            run_settings.model_policy,
            payload.task_id,
            reasoning_policy=policy.model_dump(mode="json"),
            answer_mode=profile.answer_mode.value,
            execution_profile=execution_profile,
            agent_profile_snapshot=load_agent_profile().snapshot(),
            commit=False,
        )
        if run_settings.skills_enabled:
            catalog_builder = SkillCatalogBuilder(
                session, metadata_chars=run_settings.skills_catalog_metadata_chars
            )
            catalog = await catalog_builder.build(
                goal=goal,
                explicit_identities=payload.skill_ids,
                runtime_capabilities=_configured_skill_capabilities(run_settings),
            )
            await catalog_builder.freeze(
                run.id,
                profile.answer_mode.value,
                catalog,
            )
            for identity in payload.skill_ids:
                try:
                    catalog.require(identity)
                except ValueError as exc:
                    raise ValidationError(
                        "SKILL_SELECTION_INVALID",
                        f"无法激活 Skill：{identity}",
                        {"qualified_identity": identity, "reason": "absent_from_catalog"},
                    ) from exc
            activator = SkillActivationService(
                session,
                max_active=run_settings.skills_max_active,
                max_resource_bytes=run_settings.skills_max_resource_bytes_per_run,
            )
            for identity in payload.skill_ids:
                try:
                    await activator.activate(
                        run.id,
                        identity,
                        initiator="explicit",
                        reason="explicit run selection",
                    )
                except ValueError as exc:
                    raise ValidationError(
                        "SKILL_SELECTION_INVALID",
                        f"无法激活 Skill：{identity}",
                        {"qualified_identity": identity, "reason": str(exc)},
                    ) from exc
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


@router.post("/runs/stream")
async def create_run_stream(
    payload: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Create a run and stream it on the same HTTP request."""
    created = await create_run(payload, session, settings)
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
    # FastAPI keeps dependency scopes alive until a streaming response closes.
    # End the existence-check transaction now so an idle SSE connection does not
    # pin a database connection for the lifetime of the run.
    await session.rollback()
    return _streaming_response(_run_event_stream(run_id, after_id=after_id))
