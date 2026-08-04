from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.model_base import utc_now
from app.db.models.executions import RuntimeBuildRecord, RuntimeProfileRecord


class RuntimeProfilePersistence:
    """Maps runtime profile state between the application view and ORM records."""

    def __init__(self, session_factory: Any):
        self.session_factory = session_factory

    async def load(self, state: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
        if self.session_factory is None:
            return state, []
        recovered = []
        async with self.session_factory() as session:
            profile = await self._load_profile(session, state)
            state.update(
                dependencies=list(profile.dependencies),
                active_image=profile.active_image,
                dependency_digest=profile.dependency_digest,
            )
            build = await session.scalar(
                select(RuntimeBuildRecord)
                .where(RuntimeBuildRecord.profile_id == "default")
                .order_by(RuntimeBuildRecord.created_at.desc())
                .limit(1)
            )
            if build is not None:
                if build.status in {"queued", "building"}:
                    self._cancel_interrupted(build)
                    recovered.append(
                        (build.id, build.staging_image or f"astra-data-viz:build-{build.id}")
                    )
                state["build"] = self.build_view(build)
            await session.commit()
        return state, recovered

    @staticmethod
    async def _load_profile(session, state) -> RuntimeProfileRecord:
        profile = await session.get(RuntimeProfileRecord, "default")
        if profile is None:
            profile = RuntimeProfileRecord(
                id="default",
                dependencies=state["dependencies"],
                active_image=state["active_image"],
                dependency_digest=state["dependency_digest"],
            )
            session.add(profile)
        return profile

    @staticmethod
    def _cancel_interrupted(build: RuntimeBuildRecord) -> None:
        build.status = "cancelled"
        build.phase = "构建已中断"
        build.error_code = "runtime_restarted"
        build.completed_at = utc_now()

    @staticmethod
    def build_view(build: RuntimeBuildRecord) -> dict[str, Any]:
        return {
            "id": build.id,
            "status": build.status,
            "phase": build.phase,
            "progress": build.progress,
            "log": build.log_summary,
            "image": build.activated_image,
            "dependencies": list(build.dependencies),
            "dependency_digest": build.dependency_digest,
        }

    async def persist(self, state: dict[str, Any]) -> None:
        if self.session_factory is None:
            return
        async with self.session_factory() as session:
            profile = await self._load_profile(session, state)
            self._update_profile(profile, state)
            raw_build = state.get("build") or {}
            if raw_build.get("id"):
                build = await session.get(RuntimeBuildRecord, raw_build["id"])
                if build is None:
                    build = self._new_build(raw_build, state)
                    session.add(build)
                self._update_build(build, raw_build, state)
            await session.commit()

    @staticmethod
    def _update_profile(profile: RuntimeProfileRecord, state: dict[str, Any]) -> None:
        raw_build = state.get("build") or {}
        if raw_build and raw_build.get("status") != "succeeded":
            return
        changed = (
            profile.dependencies != state["dependencies"]
            or profile.active_image != state["active_image"]
            or profile.dependency_digest != state["dependency_digest"]
        )
        profile.dependencies = list(state["dependencies"])
        profile.active_image = state["active_image"]
        profile.dependency_digest = state["dependency_digest"]
        if changed:
            profile.version += 1
        profile.updated_at = utc_now()

    @staticmethod
    def _new_build(raw_build, state) -> RuntimeBuildRecord:
        return RuntimeBuildRecord(
            id=raw_build["id"],
            profile_id="default",
            dependencies=list(raw_build.get("dependencies", state["dependencies"])),
            dependency_digest=raw_build.get("dependency_digest", state["dependency_digest"]),
        )

    @staticmethod
    def _update_build(build, raw_build, state) -> None:
        build.dependencies = list(raw_build.get("dependencies", state["dependencies"]))
        build.dependency_digest = raw_build.get("dependency_digest", state["dependency_digest"])
        build.status = raw_build.get("status", build.status)
        build.phase = raw_build.get("phase", build.phase)
        build.progress = int(raw_build.get("progress", build.progress))
        build.log_summary = str(raw_build.get("log") or "")
        build.staging_image = f"astra-data-viz:build-{build.id}"
        build.activated_image = raw_build.get("image")
        build.updated_at = utc_now()
        if build.status == "building" and build.started_at is None:
            build.started_at = utc_now()
        if build.status in {"succeeded", "failed", "cancelled"}:
            build.completed_at = build.completed_at or utc_now()
