from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.permissions.governance import verify_permission_bundle
from app.common.core.config import Settings
from app.common.core.errors import ValidationError
from app.common.schemas.permissions import PermissionBundle
from app.common.schemas.schedules import ScheduledExecutionConfig
from app.infrastructure.db.models.runs import RunRecord


class ScheduledExecutionResolver:
    """Resolve the same reusable unattended execution context for every entry point."""

    def __init__(self, session: AsyncSession, settings: Settings):
        self.session = session
        self.settings = settings

    async def from_task(self, task_id: str) -> ScheduledExecutionConfig:
        return await self._from_query(
            select(RunRecord)
            .where(RunRecord.task_id == task_id)
            .order_by(RunRecord.created_at.desc())
            .limit(20)
        )

    async def from_workspace(self) -> ScheduledExecutionConfig:
        return await self._from_query(
            select(RunRecord).order_by(RunRecord.created_at.desc()).limit(100)
        )

    async def from_task_or_workspace(self, task_id: str) -> ScheduledExecutionConfig:
        try:
            return await self.from_task(task_id)
        except ValidationError:
            return await self.from_workspace()

    async def _from_query(self, query) -> ScheduledExecutionConfig:
        runs = list((await self.session.scalars(query)).all())
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
            "创建自动化需要工作区中仍有效的无人值守权限包。",
        )
