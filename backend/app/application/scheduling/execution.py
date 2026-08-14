from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.permissions.governance import permission_bundle_digest, verify_permission_bundle
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import AstraInputValidationError
from app.common.schemas.permissions import PermissionBundle
from app.common.schemas.schedules import ScheduledExecutionConfig
from app.infrastructure.db.models.runs import RunRecord


@dataclass
class ScheduledExecutionResolver:
    """Resolve the same reusable unattended execution context for every entry point."""

    session: AsyncSession
    settings: AstraRuntimeSettings

    async def from_task(self, task_id: str) -> ScheduledExecutionConfig:
        return await self._from_query(
            select(RunRecord).where(RunRecord.task_id == task_id).order_by(RunRecord.created_at.desc()).limit(20)
        )

    async def from_workspace(self) -> ScheduledExecutionConfig:
        return await self._from_query(select(RunRecord).order_by(RunRecord.created_at.desc()).limit(100))

    async def from_task_or_workspace(self, task_id: str) -> ScheduledExecutionConfig:
        try:
            return await self.from_task(task_id)
        except AstraInputValidationError:
            return await self.from_workspace()

    async def for_management(self, task_id: str, *, workspace_fallback: bool = True) -> ScheduledExecutionConfig:
        """Resolve an existing unattended grant or create a signed no-tool profile.

        The management API is an explicit local-user action, so a model-only
        schedule can be created without first running a privileged tool. Tool
        access remains fail-closed because the fallback bundle grants no tool
        identities, actions, effects, resources, network, credentials, or outputs.
        """

        try:
            if workspace_fallback:
                return await self.from_task_or_workspace(task_id)
            return await self.from_task(task_id)
        except AstraInputValidationError as exc:
            if exc.payload.code != "AUTOMATION_PERMISSION_BUNDLE_REQUIRED":
                raise
            return self._model_only_execution()

    def _model_only_execution(self) -> ScheduledExecutionConfig:
        bundle = PermissionBundle(
            id=f"pb_{uuid4().hex}",
            version="1",
            allowed_actions=[],
            allowed_resources=[],
            allowed_effect_kinds=[],
            allowed_tool_identities=[],
            network_destinations=[],
            allowed_data_labels=[],
            allowed_credential_scopes=[],
            output_destinations=[],
            max_tool_calls=1,
            max_runtime_seconds=600,
            expires_at=datetime.now(timezone.utc) + timedelta(days=365),
            digest="",
        )
        bundle = bundle.model_copy(
            update={
                "digest": permission_bundle_digest(
                    bundle,
                    self.settings.permission_bundle_signing_secret,
                )
            }
        )
        return ScheduledExecutionConfig(
            permission_bundle=bundle.model_dump(mode="json"),
        )

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
                for key in ("provider", "model", "base_url")
                if run.model_policy.get(key) is not None
            }
            return ScheduledExecutionConfig(
                answer_mode=run.answer_mode,
                model=model or None,
                permission_bundle=bundle.model_dump(mode="json"),
            )
        raise AstraInputValidationError(
            "AUTOMATION_PERMISSION_BUNDLE_REQUIRED",
            "创建自动化需要工作区中仍有效的无人值守权限包。",
        )
