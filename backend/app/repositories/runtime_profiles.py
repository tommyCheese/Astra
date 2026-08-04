from __future__ import annotations

from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.executions import RuntimeBuildRecord, RuntimeProfileRecord


class RuntimeBuildStateError(RuntimeError):
    pass


class RuntimeProfileRepository:
    ACTIVE_BUILD_STATUSES = frozenset({"queued", "building"})
    TERMINAL_BUILD_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
    TRANSITIONS: ClassVar[dict[str, frozenset[str]]] = {
        "queued": frozenset({"building", "cancelled"}),
        "building": TERMINAL_BUILD_STATUSES,
    }

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_default(
        self, *, active_image: str, dependency_digest: str
    ) -> RuntimeProfileRecord:
        profile = await self.session.get(RuntimeProfileRecord, "default")
        if profile is None:
            profile = RuntimeProfileRecord(
                id="default",
                dependencies=[],
                active_image=active_image,
                dependency_digest=dependency_digest,
            )
            self.session.add(profile)
            await self.session.flush()
        return profile

    async def create_build(
        self,
        *,
        dependencies: list[dict[str, str]],
        dependency_digest: str,
        profile_id: str = "default",
    ) -> RuntimeBuildRecord:
        active = await self.session.scalar(
            select(RuntimeBuildRecord.id)
            .where(
                RuntimeBuildRecord.profile_id == profile_id,
                RuntimeBuildRecord.status.in_(self.ACTIVE_BUILD_STATUSES),
            )
            .limit(1)
        )
        if active is not None:
            raise RuntimeBuildStateError("runtime_build_in_progress")
        build = RuntimeBuildRecord(
            profile_id=profile_id,
            dependencies=dependencies,
            dependency_digest=dependency_digest,
            status="queued",
            phase="等待构建",
            progress=0,
        )
        self.session.add(build)
        await self.session.flush()
        return build

    async def transition(
        self,
        build_id: str,
        status: str,
        *,
        phase: str | None = None,
        progress: int | None = None,
        log_summary: str | None = None,
        staging_image: str | None = None,
        error_code: str | None = None,
    ) -> RuntimeBuildRecord:
        build = await self._require_build(build_id)
        if status not in self.TRANSITIONS.get(build.status, frozenset()):
            raise RuntimeBuildStateError(f"invalid_runtime_build_transition:{build.status}:{status}")
        now = utc_now()
        build.status = status
        build.phase = phase or build.phase
        if progress is not None:
            build.progress = max(0, min(100, progress))
        if log_summary is not None:
            build.log_summary = log_summary
        if staging_image is not None:
            build.staging_image = staging_image
        build.error_code = error_code
        build.updated_at = now
        if status == "building":
            build.started_at = now
        elif status in self.TERMINAL_BUILD_STATUSES:
            build.completed_at = now
        await self.session.flush()
        return build

    async def activate(self, build_id: str, *, image: str) -> RuntimeBuildRecord:
        build = await self._require_build(build_id)
        if build.status != "building":
            raise RuntimeBuildStateError(f"runtime_build_not_activatable:{build.status}")
        profile = await self.session.get(RuntimeProfileRecord, build.profile_id)
        if profile is None:
            raise RuntimeBuildStateError("runtime_profile_missing")
        now = utc_now()
        profile.dependencies = list(build.dependencies)
        profile.active_image = image
        profile.dependency_digest = build.dependency_digest
        profile.version += 1
        profile.updated_at = now
        build.status = "succeeded"
        build.phase = "已激活"
        build.progress = 100
        build.activated_image = image
        build.completed_at = now
        build.updated_at = now
        await self.session.flush()
        return build

    async def recover_interrupted(self) -> tuple[str, ...]:
        builds = list(
            (
                await self.session.scalars(
                    select(RuntimeBuildRecord).where(
                        RuntimeBuildRecord.status.in_(self.ACTIVE_BUILD_STATUSES)
                    )
                )
            ).all()
        )
        now = utc_now()
        for build in builds:
            build.status = "cancelled"
            build.phase = "构建已中断"
            build.error_code = "runtime_restarted"
            build.completed_at = now
            build.updated_at = now
        await self.session.flush()
        return tuple(build.id for build in builds)

    async def _require_build(self, build_id: str) -> RuntimeBuildRecord:
        build = await self.session.get(RuntimeBuildRecord, build_id)
        if build is None:
            raise RuntimeBuildStateError("runtime_build_not_found")
        return build
