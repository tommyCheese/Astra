from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.models import RunRecord, ScheduledJobRecord, TaskRecord
from app.permissions.governance import verify_permission_bundle
from app.repositories.schedules import (
    ScheduleNotFoundError,
    ScheduleRepository,
    ScheduleVersionConflictError,
)
from app.schemas.permissions import PermissionBundle
from app.schemas.schedules import (
    ActiveHours,
    HeartbeatConfig,
    ScheduledExecutionConfig,
    ScheduledJobCreate,
    ScheduledJobKind,
)
from app.system_command_parsing import (
    CommandUsageError,
    HeartbeatCommand,
    ScheduleCommand,
    parse_heartbeat_command,
    parse_schedule_command,
)


class AutomationCommandService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings
        self.repo = ScheduleRepository(session)

    async def execute_schedule(
        self,
        task: TaskRecord,
        arguments: str,
    ) -> tuple[str, dict[str, object]]:
        try:
            command = parse_schedule_command(arguments)
        except CommandUsageError as exc:
            raise ValidationError(
                "SYSTEM_COMMAND_USAGE_INVALID",
                str(exc),
                {"usage": exc.usage, "command": "/schedule"},
            ) from exc
        try:
            return await self._execute_schedule(task, command)
        except ScheduleNotFoundError as exc:
            raise ResourceError(
                "SCHEDULE_NOT_FOUND",
                "找不到当前对话中的指定定时任务。",
            ) from exc
        except ScheduleVersionConflictError as exc:
            raise StateError(
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
            raise ValidationError(
                "SYSTEM_COMMAND_USAGE_INVALID",
                str(exc),
                {"usage": exc.usage, "command": "/heartbeat"},
            ) from exc
        try:
            return await self._execute_heartbeat(task, command)
        except ScheduleNotFoundError as exc:
            raise ResourceError(
                "HEARTBEAT_NOT_CONFIGURED",
                "当前对话尚未配置 heartbeat。",
            ) from exc

    async def _execute_schedule(
        self,
        task: TaskRecord,
        command: ScheduleCommand,
    ) -> tuple[str, dict[str, object]]:
        if command.action == "list":
            jobs = await self.repo.list(
                target_task_id=task.id,
                kind=ScheduledJobKind.agent,
            )
            if jobs:
                summary = "\n".join(
                    f"- {job.name} · {'启用' if job.enabled else '暂停'} · "
                    f"{job.id} · 下次 {self._display_time(job.next_fire_at)}"
                    for job in jobs
                )
                message = f"当前对话共有 {len(jobs)} 个定时任务：\n{summary}"
            else:
                message = "当前对话还没有定时任务。"
            return (
                message,
                {"jobs": [self._job_view(job) for job in jobs]},
            )
        if command.action == "create":
            assert command.schedule is not None and command.prompt is not None
            execution = await self._current_execution(task.id)
            try:
                payload = ScheduledJobCreate(
                    name=(command.name or command.prompt[:80]),
                    prompt=command.prompt,
                    target_task_id=task.id,
                    schedule=command.schedule,
                    timezone=command.timezone,
                    execution=execution,
                )
            except ValueError as exc:
                raise ValidationError(
                    "SCHEDULE_COMMAND_INVALID",
                    "定时任务参数无效。",
                    {"reason": str(exc)[:600]},
                ) from exc
            job = await self.repo.create(
                payload,
                owner_principal="system-command",
            )
            return (
                f"已创建定时任务“{job.name}”（{job.id}），"
                f"下次触发：{self._display_time(job.next_fire_at)}。",
                {"job": self._job_view(job)},
            )

        assert command.job_id is not None
        job = await self._require_scoped_job(
            task.id,
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
            return "已删除定时任务并保留运行历史。", {
                "job": self._job_view(job)
            }
        if not job.enabled:
            raise StateError(
                "SCHEDULE_DISABLED",
                "定时任务已暂停，请先恢复后再手动运行。",
            )
        schedule_run = await self.repo.manual_trigger(
            job,
            idempotency_key=command.idempotency_key,
            claimed_by="system-command",
        )
        return "已排队手动运行。", {
            "schedule_run": self._schedule_run_view(schedule_run)
        }

    async def _execute_heartbeat(
        self,
        task: TaskRecord,
        command: HeartbeatCommand,
    ) -> tuple[str, dict[str, object]]:
        heartbeat = await self.repo.get_heartbeat(task.id)
        if command.action == "status":
            if heartbeat is None:
                return "当前对话尚未配置 heartbeat。", {
                    "heartbeat": {"configured": False, "enabled": False}
                }
            return (
                f"Heartbeat {'已启用' if heartbeat.enabled else '已关闭'} · "
                f"周期 {heartbeat.schedule.get('interval_seconds')} 秒 · "
                f"时区 {heartbeat.timezone} · "
                f"下次 {self._display_time(heartbeat.next_fire_at)}",
                {
                "heartbeat": {
                    "configured": True,
                    **self._job_view(heartbeat),
                }
                },
            )
        if command.action == "on":
            assert command.interval_seconds is not None
            if (
                command.interval_seconds
                < self.settings.scheduler_heartbeat_min_interval_seconds
            ):
                raise ValidationError(
                    "HEARTBEAT_INTERVAL_TOO_SHORT",
                    "heartbeat 周期低于系统允许的最小值。",
                    {
                        "minimum_seconds": (
                            self.settings.scheduler_heartbeat_min_interval_seconds
                        )
                    },
                )
            execution = await self._current_execution(task.id)
            existing_active = (heartbeat.heartbeat or {}).get("active_hours") if heartbeat else None
            active_hours = command.active_hours
            if active_hours is None and existing_active:
                active_hours = ActiveHours.model_validate(existing_active)
            current_prompt = (
                command.prompt
                or (heartbeat.prompt if heartbeat is not None else None)
            )
            payload_values = {
                "target_task_id": task.id,
                "enabled": True,
                "interval_seconds": command.interval_seconds,
                "timezone": (
                    command.timezone
                    or (heartbeat.timezone if heartbeat is not None else "UTC")
                ),
                "active_hours": active_hours,
                "execution": execution,
            }
            if current_prompt:
                payload_values["prompt"] = current_prompt
            try:
                payload = HeartbeatConfig.model_validate(payload_values)
            except ValueError as exc:
                raise ValidationError(
                    "HEARTBEAT_COMMAND_INVALID",
                    "heartbeat 参数无效。",
                    {"reason": str(exc)[:600]},
                ) from exc
            heartbeat = await self.repo.upsert_heartbeat(
                payload,
                owner_principal="system-command",
            )
            return "已启用 heartbeat。", {
                "heartbeat": {
                    "configured": True,
                    **self._job_view(heartbeat),
                }
            }
        if command.action == "off":
            heartbeat = await self.repo.disable_heartbeat(task.id)
            return "已关闭 heartbeat，配置和历史仍保留。", {
                "heartbeat": {
                    "configured": True,
                    **self._job_view(heartbeat),
                }
            }
        if heartbeat is None:
            raise ScheduleNotFoundError(f"heartbeat:{task.id}")
        schedule_run = await self.repo.manual_trigger(
            heartbeat,
            idempotency_key=command.idempotency_key,
            claimed_by="system-command",
        )
        return "已排队 heartbeat 手动检查。", {
            "schedule_run": self._schedule_run_view(schedule_run)
        }

    async def _current_execution(self, task_id: str) -> ScheduledExecutionConfig:
        runs = list(
            (
                await self.session.scalars(
                    select(RunRecord)
                    .where(RunRecord.task_id == task_id)
                    .order_by(RunRecord.created_at.desc())
                    .limit(20)
                )
            ).all()
        )
        now = datetime.now(timezone.utc)
        for run in runs:
            raw_bundle = (run.execution_profile or {}).get("permission_bundle")
            if not raw_bundle:
                continue
            try:
                bundle = PermissionBundle.model_validate(raw_bundle)
            except ValueError:
                continue
            expires_at = bundle.expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at.astimezone(timezone.utc) <= now:
                    continue
            if not verify_permission_bundle(
                bundle,
                self.settings.permission_bundle_signing_secret,
            ):
                continue
            model = {
                key: run.model_policy.get(key)
                for key in ("provider", "model", "base_url", "thinking")
                if run.model_policy.get(key) is not None
            }
            return ScheduledExecutionConfig(
                answer_mode=run.answer_mode,
                model=model or None,
                permission_bundle=bundle.model_dump(mode="json"),
            )
        raise ValidationError(
            "AUTOMATION_PERMISSION_BUNDLE_REQUIRED",
            "创建自动化需要当前对话中仍有效的无人值守权限包。",
        )

    async def _require_scoped_job(
        self,
        task_id: str,
        job_id: str,
        *,
        kind: ScheduledJobKind,
    ) -> ScheduledJobRecord:
        job = await self.repo.require(job_id)
        if job.target_task_id != task_id or job.kind != kind.value:
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
            "next_fire_at": (
                job.next_fire_at.isoformat() if job.next_fire_at else None
            ),
            "last_fire_at": (
                job.last_fire_at.isoformat() if job.last_fire_at else None
            ),
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
