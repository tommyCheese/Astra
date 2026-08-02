from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.runs import _create_run, _schedule_run
from app.core.config import Settings
from app.core.errors import AstraError
from app.db.models import RunRecord, ScheduledJobRecord, ScheduledJobRunRecord, utc_now
from app.repositories.workspaces import WorkspaceRepository
from app.schemas.agent import AnswerMode, CreateRunRequest
from app.schemas.models import RunModelConfig
from app.schemas.schedules import ScheduledExecutionConfig

TERMINAL_RUN_STATUSES = {
    "completed",
    "completed_with_warnings",
    "blocked",
    "failed",
    "cancelled",
}
_finalizer_tasks: set[asyncio.Task[None]] = set()


def _model_config(raw: dict | None) -> RunModelConfig | None:
    if not raw:
        return None
    values = dict(raw)
    if "model" in values and "name" not in values:
        values["name"] = values.pop("model")
    return RunModelConfig.model_validate(values)


class ScheduledRunDispatcher:
    """Create a normal Astra Run bound to the schedule's target conversation."""

    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ):
        self.settings = settings
        self.session_factory = session_factory

    async def dispatch(self, schedule_run_id: str) -> ScheduledJobRunRecord:
        async with self.session_factory() as session:
            schedule_run = await session.get(ScheduledJobRunRecord, schedule_run_id)
            if schedule_run is None:
                raise LookupError(schedule_run_id)
            if schedule_run.run_id:
                return schedule_run
            job = await session.get(ScheduledJobRecord, schedule_run.job_id)
            if job is None or not job.target_task_id:
                return await self._block(
                    session,
                    schedule_run,
                    code="SCHEDULE_TARGET_REQUIRED",
                    message="任务没有可用的结果对话，请先重新绑定。",
                )

            # A task workspace is unique by task_id. Creating or resolving it before
            # the Run makes workspace reuse an explicit dispatch invariant.
            try:
                workspace = await WorkspaceRepository(session).get_or_create(
                    job.target_task_id
                )
            except ValueError:
                return await self._block(
                    session,
                    schedule_run,
                    code="SCHEDULE_TARGET_NOT_FOUND",
                    message="目标对话已不存在，请先重新绑定。",
                )

            try:
                execution = ScheduledExecutionConfig.model_validate(job.execution)
                request = CreateRunRequest(
                    goal=job.prompt,
                    task_id=job.target_task_id,
                    answer_mode=AnswerMode(execution.answer_mode),
                    model=_model_config(execution.model),
                    interactive=False,
                    permission_bundle=execution.permission_bundle,
                    skill_ids=execution.skill_ids,
                )
                created, run_settings = await _create_run(
                    request,
                    session,
                    self.settings,
                )
            except (AstraError, ValueError) as exc:
                return await self._block(
                    session,
                    schedule_run,
                    code=getattr(getattr(exc, "payload", None), "code", "SCHEDULE_RUN_BLOCKED"),
                    message=getattr(getattr(exc, "payload", None), "message", str(exc)),
                )

            run = await session.get(RunRecord, created.run_id)
            assert run is not None
            trigger = {
                "type": "heartbeat" if job.kind == "heartbeat" else "schedule",
                "job_id": job.id,
                "schedule_run_id": schedule_run.id,
                "scheduled_for": schedule_run.scheduled_for.isoformat(),
                "trigger_type": schedule_run.trigger_type,
                "workspace_id": workspace.id,
                "target_task_id": job.target_task_id,
            }
            run.execution_profile = {**(run.execution_profile or {}), "trigger": trigger}
            schedule_run.task_id = job.target_task_id
            schedule_run.run_id = created.run_id
            schedule_run.status = "running"
            schedule_run.started_at = utc_now()
            schedule_run.updated_at = schedule_run.started_at
            schedule_run.outcome = {"trigger": trigger}
            await session.commit()

            engine_task = _schedule_run(created.run_id, run_settings)
            engine_task.add_done_callback(
                lambda _completed: self._schedule_finalizer(
                    schedule_run.id,
                    created.run_id,
                )
            )
            return schedule_run

    def _schedule_finalizer(self, schedule_run_id: str, run_id: str) -> None:
        task = asyncio.create_task(
            self._finalize(schedule_run_id, run_id),
            name=f"astra-schedule-finalize-{schedule_run_id}",
        )
        _finalizer_tasks.add(task)
        task.add_done_callback(_finalizer_tasks.discard)

    async def _finalize(self, schedule_run_id: str, run_id: str) -> None:
        async with self.session_factory() as session:
            schedule_run = await session.get(ScheduledJobRunRecord, schedule_run_id)
            run = await session.get(RunRecord, run_id)
            if schedule_run is None or run is None:
                return
            status = run.status if run.status in TERMINAL_RUN_STATUSES else "failed"
            schedule_run.status = (
                "completed" if status == "completed_with_warnings" else status
            )
            schedule_run.completed_at = utc_now()
            schedule_run.updated_at = schedule_run.completed_at
            schedule_run.outcome = {
                **(schedule_run.outcome or {}),
                "run_status": run.status,
                "summary": run.summary,
            }
            await session.commit()

    @staticmethod
    async def _block(
        session: AsyncSession,
        schedule_run: ScheduledJobRunRecord,
        *,
        code: str,
        message: str,
    ) -> ScheduledJobRunRecord:
        now = datetime.now(timezone.utc)
        schedule_run.status = "blocked"
        schedule_run.completed_at = now
        schedule_run.updated_at = now
        schedule_run.outcome = {"error": {"code": code, "message": message}}
        await session.commit()
        return schedule_run
