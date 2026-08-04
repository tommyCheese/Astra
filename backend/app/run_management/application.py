"""Public Run use-case facade shared by HTTP, schedules, and commands."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ResourceError
from app.db.models.runs import RunRecord
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.run_management.continuation import RunContinuationService
from app.run_management.contracts import PreparedRun, RunDispatcher
from app.run_management.creation import RunCreator
from app.run_management.settings import RunSettingsResolver
from app.schemas.agent.api_views import ContinueRunRequest, CreateRunRequest, CreateRunResponse
from app.schemas.agent.tool_invocation import ApprovalDecisionRequest
from app.schemas.models import RunModelConfig


class RunApplicationService:
    """Expose cohesive Run use cases without leaking transport concerns."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        dispatcher: RunDispatcher,
    ) -> None:
        self.session = session
        self.dispatcher = dispatcher
        settings_resolver = RunSettingsResolver(session, settings)
        self._creator = RunCreator(session, settings, settings_resolver)
        self._continuation = RunContinuationService(
            session,
            settings_resolver,
            dispatcher,
        )

    async def create_and_start(self, request: CreateRunRequest) -> CreateRunResponse:
        prepared_run = await self.prepare(request)
        self.start(prepared_run)
        return prepared_run.response

    async def prepare(
        self,
        request: CreateRunRequest,
        *,
        commit: bool = True,
    ) -> PreparedRun:
        return await self._creator.prepare(request, commit=commit)

    def start(self, prepared_run: PreparedRun) -> asyncio.Task[None]:
        return self.dispatcher.start(prepared_run.response.run_id, prepared_run.settings)

    async def cancel(self, run_id: str) -> RunRecord:
        repository = RunUnitOfWork(self.session)
        run = await repository.get_run(run_id)
        if run is None:
            raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
        if run.status in RunUnitOfWork.TERMINAL_STATUSES and run.status != "waiting_user":
            return run
        await self.dispatcher.cancel(run_id)
        await self.session.rollback()
        run = await repository.get_run(run_id)
        if run is None:
            raise ResourceError("RUN_NOT_FOUND", "找不到指定运行记录。")
        if run.status not in RunUnitOfWork.TERMINAL_STATUSES or run.status == "waiting_user":
            run = await repository.cancel_run(run_id)
            await repository.commit()
        return run

    async def resume_and_start(
        self,
        run_id: str,
        request: ContinueRunRequest,
    ) -> CreateRunResponse:
        return await self._continuation.resume_and_start(run_id, request)

    async def decide_approval_and_start(
        self,
        run_id: str,
        approval_id: str,
        request: ApprovalDecisionRequest,
    ) -> CreateRunResponse:
        return await self._continuation.decide_approval_and_start(
            run_id,
            approval_id,
            request,
        )

    @staticmethod
    def apply_model_config(
        settings: Settings,
        model: RunModelConfig | dict | None,
    ) -> Settings:
        """Compatibility entry point while callers migrate to RunSettingsResolver."""
        return RunSettingsResolver.apply_model_config(settings, model)
