from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.scheduling.command_parsing import (
    CommandUsageError,
    HeartbeatCommand,
    ScheduleCommand,
    parse_heartbeat_command,
    parse_schedule_command,
)
from app.application.scheduling.execution import ScheduledExecutionResolver
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import (
    AstraInputValidationError,
    AstraResourceNotFoundError,
    AstraStateConflictError,
)
from app.common.schemas.schedules import (
    ActiveHours,
    HeartbeatConfig,
    ScheduledExecutionConfig,
    ScheduledJobCreate,
    ScheduledJobKind,
)
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.scheduling import ScheduledJobRecord
from app.infrastructure.repositories.heartbeats import HeartbeatRepository
from app.infrastructure.repositories.schedules import (
    ScheduleNotFoundError,
    ScheduleRepository,
    ScheduleVersionConflictError,
)


class AutomationCommandService:
    def __init__(self, session: AsyncSession, settings: AstraRuntimeSettings):
        self.session = session
        self.settings = settings
        self.repo = ScheduleRepository(session)
        self.heartbeats = HeartbeatRepository(session)

    async def execute_schedule(
        self,
        task: TaskRecord,
        arguments: str,
    ) -> tuple[str, dict[str, object]]:
        try:
            command = parse_schedule_command(arguments)
        except CommandUsageError as exc:
            raise AstraInputValidationError(
                "SYSTEM_COMMAND_USAGE_INVALID",
                str(exc),
                {"usage": exc.usage, "command": "/schedule"},
            ) from exc
        try:
            return await self._execute_schedule(task, command)
        except ScheduleNotFoundError as exc:
            raise AstraResourceNotFoundError(
                "SCHEDULE_NOT_FOUND",
                "找不到工作区中的指定定时任务。",
            ) from exc
        except ScheduleVersionConflictError as exc:
            raise AstraStateConflictError(
                "SCHEDULE_VERSION_CONFLICT",
                "定时任务已被更新，请刷新状态后重试。",
            ) from exc

    async def execute_heartbeat(
        self,
        task: TaskRecord,
        arguments: str,
    ) -> tuple[str, dict[str, object]]:
        try:
            command = parse_heartbeat_command(arguments)
        except CommandUsageError as exc:
            raise AstraInputValidationError(
                "SYSTEM_COMMAND_USAGE_INVALID",
                str(exc),
                {"usage": exc.usage, "command": "/heartbeat"},
            ) from exc
        try:
            return await self._execute_heartbeat(task, command)
        except ScheduleNotFoundError as exc:
            raise AstraResourceNotFoundError(
                "HEARTBEAT_NOT_CONFIGURED",
                "工作区尚未配置 heartbeat。",
            ) from exc

    async def _execute_schedule(
        self,
        task: TaskRecord,
        command: ScheduleCommand,
    ) -> tuple[str, dict[str, object]]:
        if command.action == "list":
            return await self._list_schedules()
        if command.action == "create":
            return await self._create_schedule(task, command)
        assert command.job_id is not None
        job = await self._require_global_job(
            command.job_id,
            kind=ScheduledJobKind.agent,
        )
        if command.action == "show":
            return (
                f"{job.name} · {'启用' if job.enabled else '暂停'} · "
                f"版本 {job.version} · 下次 {self._display_time(job.next_fire_at)}",
                {"job": self._job_view(job)},
            )
        if command.action == "pause":
            assert command.version is not None
            job = await self.repo.set_enabled(
                job.id,
                enabled=False,
                version=command.version,
            )
            return "已暂停定时任务。", {"job": self._job_view(job)}
        if command.action == "resume":
            assert command.version is not None
            job = await self.repo.set_enabled(
                job.id,
                enabled=True,
                version=command.version,
            )
            return "已恢复定时任务。", {"job": self._job_view(job)}
        if command.action == "delete":
            assert command.version is not None
            job = await self.repo.delete(job.id, version=command.version)
            return "已删除定时任务并保留运行历史。", {"job": self._job_view(job)}
        if not job.enabled:
            raise AstraStateConflictError(
                "SCHEDULE_DISABLED",
                "定时任务已暂停，请先恢复后再手动运行。",
            )
        schedule_run = await self.repo.manual_trigger(
            job,
            idempotency_key=command.idempotency_key,
            claimed_by="system-command",
        )
        return "已排队手动运行。", {"schedule_run": self._schedule_run_view(schedule_run)}

    async def _list_schedules(self) -> tuple[str, dict[str, object]]:
        jobs = await self.repo.list(kind=ScheduledJobKind.agent)
        if not jobs:
            return "工作区还没有定时任务。", {"jobs": []}
        summary = "\n".join(
            f"- {job.name} · {'启用' if job.enabled else '暂停'} · "
            f"{job.id} · 下次 {self._display_time(job.next_fire_at)}"
            for job in jobs
        )
        return (
            f"工作区共有 {len(jobs)} 个定时任务：\n{summary}",
            {"jobs": [self._job_view(job) for job in jobs]},
        )

    async def _create_schedule(
        self, task: TaskRecord, command: ScheduleCommand
    ) -> tuple[str, dict[str, object]]:
        assert command.schedule is not None and command.prompt is not None
        try:
            payload = ScheduledJobCreate(
                name=command.name or command.prompt[:80],
                target_task_id=task.id,
                prompt=command.prompt,
                schedule=command.schedule,
                timezone=command.timezone,
                execution=await self._current_execution(task.id),
            )
        except ValueError as error:
            raise AstraInputValidationError(
                "SCHEDULE_COMMAND_INVALID", "定时任务参数无效。", {"reason": str(error)[:600]}
            ) from error
        job = await self.repo.create(payload, owner_principal="system-command")
        message = f"已创建定时任务“{job.name}”（{job.id}），下次触发：{self._display_time(job.next_fire_at)}。"
        return message, {"job": self._job_view(job)}

    async def _execute_heartbeat(
        self,
        task: TaskRecord,
        command: HeartbeatCommand,
    ) -> tuple[str, dict[str, object]]:
        heartbeat = await self.heartbeats.get()
        if command.action == "status":
            return self._heartbeat_status(heartbeat)
        if command.action == "on":
            return await self._enable_heartbeat(task, command, heartbeat)
        if command.action == "off":
            heartbeat = await self.heartbeats.disable()
            return "已关闭 heartbeat，配置和历史仍保留。", {
                "heartbeat": {
                    "configured": True,
                    **self._job_view(heartbeat),
                }
            }
        if heartbeat is None:
            raise ScheduleNotFoundError("heartbeat:global")
        schedule_run = await self.repo.manual_trigger(
            heartbeat,
            idempotency_key=command.idempotency_key,
            claimed_by="system-command",
        )
        return "已排队 heartbeat 手动检查。", {
            "schedule_run": self._schedule_run_view(schedule_run)
        }

    def _heartbeat_status(self, heartbeat) -> tuple[str, dict[str, object]]:
        if heartbeat is None:
            return "工作区尚未配置 heartbeat。", {
                "heartbeat": {"configured": False, "enabled": False}
            }
        message = (
            f"Heartbeat {'已启用' if heartbeat.enabled else '已关闭'} · "
            f"周期 {heartbeat.schedule.get('interval_seconds')} 秒 · 时区 {heartbeat.timezone} · "
            f"下次 {self._display_time(heartbeat.next_fire_at)}"
        )
        return message, {"heartbeat": {"configured": True, **self._job_view(heartbeat)}}

    async def _enable_heartbeat(self, task, command, heartbeat):
        assert command.interval_seconds is not None
        minimum = self.settings.scheduler_heartbeat_min_interval_seconds
        if command.interval_seconds < minimum:
            raise AstraInputValidationError(
                "HEARTBEAT_INTERVAL_TOO_SHORT",
                "heartbeat 周期低于系统允许的最小值。",
                {"minimum_seconds": minimum},
            )
        payload_values = await self._heartbeat_payload(task, command, heartbeat)
        try:
            payload = HeartbeatConfig.model_validate(payload_values)
        except ValueError as error:
            raise AstraInputValidationError(
                "HEARTBEAT_COMMAND_INVALID", "heartbeat 参数无效。", {"reason": str(error)[:600]}
            ) from error
        updated = await self.heartbeats.upsert(payload, owner_principal="system-command")
        return "已启用 heartbeat。", {
            "heartbeat": {"configured": True, **self._job_view(updated)}
        }

    async def _heartbeat_payload(self, task, command, heartbeat) -> dict:
        existing_active = (heartbeat.heartbeat or {}).get("active_hours") if heartbeat else None
        active_hours = command.active_hours or (
            ActiveHours.model_validate(existing_active) if existing_active else None
        )
        values = {
            "target_task_id": task.id,
            "enabled": True,
            "interval_seconds": command.interval_seconds,
            "timezone": command.timezone or (heartbeat.timezone if heartbeat else "UTC"),
            "active_hours": active_hours,
            "execution": await self._current_execution(task.id),
        }
        prompt = command.prompt or (heartbeat.prompt if heartbeat else None)
        if prompt:
            values["prompt"] = prompt
        return values

    async def _current_execution(self, task_id: str) -> ScheduledExecutionConfig:
        return await ScheduledExecutionResolver(self.session, self.settings).from_task(task_id)

    async def _require_global_job(
        self,
        job_id: str,
        *,
        kind: ScheduledJobKind,
    ) -> ScheduledJobRecord:
        job = await self.repo.require(job_id)
        if job.kind != kind.value:
            raise ScheduleNotFoundError(job_id)
        return job

    @staticmethod
    def _job_view(job: ScheduledJobRecord) -> dict[str, object]:
        return {
            "id": job.id,
            "name": job.name,
            "kind": job.kind,
            "enabled": job.enabled,
            "schedule": job.schedule,
            "timezone": job.timezone,
            "next_fire_at": (job.next_fire_at.isoformat() if job.next_fire_at else None),
            "last_fire_at": (job.last_fire_at.isoformat() if job.last_fire_at else None),
            "version": job.version,
            "heartbeat": job.heartbeat,
        }

    @staticmethod
    def _schedule_run_view(schedule_run) -> dict[str, object]:
        return {
            "id": schedule_run.id,
            "job_id": schedule_run.job_id,
            "status": schedule_run.status,
            "trigger_type": schedule_run.trigger_type,
            "scheduled_for": schedule_run.scheduled_for.isoformat(),
            "run_id": schedule_run.run_id,
        }

    @staticmethod
    def _display_time(value: datetime | None) -> str:
        return value.isoformat() if value is not None else "无"
