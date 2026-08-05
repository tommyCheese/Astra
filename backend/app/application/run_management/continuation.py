"""Resume waiting Runs and apply frozen approval decisions."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.planning.revision import PlanRevisionError, revise_waiting_plan
from app.application.run_management.contracts import RunDispatcher
from app.application.run_management.settings import RunSettingsResolver
from app.common.core.config import Settings
from app.common.core.errors import StateError, ValidationError
from app.common.schemas.agent.api_views import ContinueRunRequest, CreateRunResponse
from app.common.schemas.agent.tool_invocation import ApprovalDecisionRequest
from app.common.schemas.agent.types import ContinuationAction
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


class RunContinuationService:
    def __init__(
        self,
        session: AsyncSession,
        settings_resolver: RunSettingsResolver,
        dispatcher: RunDispatcher,
    ) -> None:
        self._session = session
        self._settings_resolver = settings_resolver
        self._dispatcher = dispatcher

    async def resume_and_start(
        self,
        run_id: str,
        request: ContinueRunRequest,
    ) -> CreateRunResponse:
        repository = RunUnitOfWork(self._session)
        existing_run = await repository.require_run(run_id)
        run_settings = await self._settings_resolver.for_existing_run(
            existing_run,
            request.model,
        )
        try:
            run = await self._resume(repository, existing_run, request, run_settings)
        except PlanRevisionError as error:
            # A failed revision deliberately restores the previous plan with a
            # fresh continuation token. Persist that recovery before reporting
            # the validation error to the caller.
            await repository.commit()
            self._raise_resume_error(error)
        except ValueError as error:
            if getattr(error, "code", "").startswith("PLAN_REVISION_"):
                await repository.commit()
                raise ValidationError(
                    error.code,
                    "计划调整未通过校验，原计划仍可继续使用。",
                ) from error
            self._raise_resume_error(error)
        await repository.commit()
        if request.action != ContinuationAction.revise_plan:
            self._dispatcher.start(run.id, run_settings)
        return self._response_for_run(run)

    async def decide_approval_and_start(
        self,
        run_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> CreateRunResponse:
        repository = RunUnitOfWork(self._session)
        run = await repository.require_run(run_id)
        run_settings = await self._settings_resolver.for_existing_run(run, request.model)
        try:
            reviewer = await PermissionRepository(self._session).get_or_create_identity(
                identity_type="reviewer",
                principal="local-user",
                task_id=run.task_id,
                run_id=run_id,
                trust_level="user",
            )
            await repository.decide_approval(
                run_id,
                approval_id,
                request.decision.value,
                continuation_token=request.continuation_token,
                reviewer_identity={
                    "id": reviewer.id,
                    "identity_type": reviewer.identity_type,
                    "principal": reviewer.principal,
                },
                rejection_guidance=request.guidance,
            )
        except ValueError as error:
            self._raise_approval_error(error)
        await repository.commit()
        run = await repository.require_run(run_id)
        self._dispatcher.start(run_id, run_settings)
        return self._response_for_run(run)

    @staticmethod
    async def _resume(
        repository: RunUnitOfWork,
        run: RunRecord,
        request: ContinueRunRequest,
        run_settings: Settings,
    ) -> RunRecord:
        if request.action == ContinuationAction.execute_plan:
            return await RunContinuationService._confirm_plan(repository, run, request)
        if request.action == ContinuationAction.revise_plan:
            return await RunContinuationService._revise_plan(
                repository,
                run,
                request,
                run_settings,
            )
        return await RunContinuationService._resume_with_user_response(
            repository,
            run,
            request,
        )

    @staticmethod
    async def _confirm_plan(
        repository: RunUnitOfWork,
        run: RunRecord,
        request: ContinueRunRequest,
    ) -> RunRecord:
        return await repository.confirm_waiting_plan(
            run.id,
            continuation_token=request.continuation_token or "",
            plan_id=request.plan_id or "",
            expected_plan_version=request.expected_plan_version or 0,
            expected_state_version=request.expected_state_version or 0,
        )

    @staticmethod
    async def _revise_plan(
        repository: RunUnitOfWork,
        run: RunRecord,
        request: ContinueRunRequest,
        run_settings: Settings,
    ) -> RunRecord:
        return await revise_waiting_plan(
            repository,
            run_settings,
            run_id=run.id,
            request=request.content or "",
            continuation_token=request.continuation_token or "",
            plan_id=request.plan_id or "",
            expected_plan_version=request.expected_plan_version or 0,
            expected_state_version=request.expected_state_version or 0,
        )

    @staticmethod
    async def _resume_with_user_response(
        repository: RunUnitOfWork,
        run: RunRecord,
        request: ContinueRunRequest,
    ) -> RunRecord:
        return await repository.resume_waiting_run(
            run.id,
            {
                "kind": "approval_result" if request.approved is not None else "user_response",
                "status": (
                    "approved"
                    if request.approved
                    else "rejected"
                    if request.approved is False
                    else "received"
                ),
                "summary": request.content,
                "data": {"approved": request.approved},
            },
            continuation_token=request.continuation_token,
        )

    @staticmethod
    def _response_for_run(run: RunRecord) -> CreateRunResponse:
        return CreateRunResponse(
            task_id=run.task_id,
            run_id=run.id,
            status=run.status,
            answer_mode=run.answer_mode,
        )

    @staticmethod
    def _raise_resume_error(error: ValueError) -> None:
        message = str(error)
        if isinstance(error, PlanRevisionError):
            raise ValidationError(
                error.code,
                "计划调整未通过校验，原计划仍可继续使用。",
            ) from error
        if "plan revision" in message:
            raise StateError(
                "PLAN_REVISION_STALE", "计划已变化，请刷新后基于最新版本调整。"
            ) from error
        if "plan confirmation" in message:
            raise StateError(
                "PLAN_CONFIRMATION_INVALID",
                "计划确认已失效，请刷新后核对最新计划。",
            ) from error
        if "not waiting" in message:
            raise StateError("RUN_NOT_WAITING", "该任务当前不需要补充信息。") from error
        if "continuation token" in message:
            raise StateError(
                "CONTINUATION_INVALID", "任务恢复凭据已失效，请刷新后重试。"
            ) from error
        raise StateError("RUN_RESUME_CONFLICT", "当前任务无法恢复。") from error

    @staticmethod
    def _raise_approval_error(error: ValueError) -> None:
        message = str(error)
        if "continuation token" in message:
            raise StateError("CONTINUATION_INVALID", "批准凭据已失效，请刷新后重试。") from error
        if "already been decided" in message:
            raise StateError("APPROVAL_ALREADY_DECIDED", "该工具调用已经处理。") from error
        if "not available" in message:
            raise StateError(
                "SIMILAR_APPROVAL_UNAVAILABLE",
                "该命令不能使用相似命令授权。",
            ) from error
        raise StateError("APPROVAL_CONFLICT", "该批准请求当前无法处理。") from error
