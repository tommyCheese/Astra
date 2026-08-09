from __future__ import annotations

from importlib import resources
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.skills.contracts import SkillOrigin
from app.application.skills.packages import parse_skill_package
from app.application.skills.storage import SkillService
from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.skills import SkillRecord, SkillRevisionRecord


async def ensure_builtin_skills(session: AsyncSession, settings: AstraRuntimeSettings) -> None:
    if not settings.skills_enabled:
        return
    root = resources.files("app.infrastructure.builtin_skills")
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        files: dict[str, bytes] = {}
        _collect_traversable(child, "", files)
        if "SKILL.md" not in files:
            continue
        await _sync_builtin_skill(session, settings, child.name, files)


async def _sync_builtin_skill(
    session: AsyncSession,
    settings: AstraRuntimeSettings,
    directory_name: str,
    files: dict[str, bytes],
) -> None:
    package, normalized = parse_skill_package(
        files,
        origin=SkillOrigin.builtin,
        directory_name=directory_name,
        max_files=settings.skills_max_files,
        max_file_bytes=settings.skills_max_file_bytes,
        max_package_bytes=settings.skills_max_package_bytes,
        max_instruction_chars=settings.skills_max_instruction_chars,
        reject_reserved_custom_identity=False,
    )
    existing = await session.scalar(select(SkillRecord).where(SkillRecord.name == package.frontmatter.name))
    if existing is not None:
        if existing.origin != SkillOrigin.builtin.value:
            raise RuntimeError("Custom Skill conflicts with reserved built-in identity")
        active = await session.get(SkillRevisionRecord, existing.active_revision_id)
        if active is not None and active.digest == package.digest:
            return
        skill = existing
    else:
        skill = SkillRecord(
            name=package.frontmatter.name,
            origin=SkillOrigin.builtin.value,
            description=package.frontmatter.description,
            enabled=True,
        )
        session.add(skill)
        await session.flush()
    service = SkillService(session, settings)
    stored = await service._store_files(normalized, package)
    maximum = await session.scalar(
        select(func.max(SkillRevisionRecord.version)).where(SkillRevisionRecord.skill_id == skill.id)
    )
    revision = SkillRevisionRecord(
        skill_id=skill.id,
        version=int(maximum or 0) + 1,
        digest=package.digest,
        frontmatter=package.frontmatter.model_dump(by_alias=True, mode="json"),
        manifest={
            "files": stored,
            "resources": [item.model_dump(mode="json") for item in package.resources],
        },
        validation_report=service._report(package),
        predecessor_id=skill.active_revision_id,
        published_at=utc_now(),
    )
    session.add(revision)
    await session.flush()
    skill.active_revision_id = revision.id
    skill.description = package.frontmatter.description
    skill.updated_at = utc_now()


def _collect_traversable(root: Any, prefix: str, result: dict[str, bytes]) -> None:
    for child in root.iterdir():
        if child.name == "__pycache__" or child.name.endswith((".pyc", ".pyo")):
            continue
        path = f"{prefix}{child.name}"
        if child.is_dir():
            _collect_traversable(child, f"{path}/", result)
        elif child.is_file():
            result[path] = child.read_bytes()
