from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.skills.storage import SkillService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.skills import (
    SkillDetailView,
    SkillFileView,
    SkillRevisionView,
    SkillSummaryView,
)
from app.infrastructure.db.models.skills import SkillRecord, SkillRevisionRecord


def skill_diagnostics(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    return list((report or {}).get("diagnostics", []))


async def skill_revision_view(
    session: AsyncSession, revision_id: str | None
) -> SkillRevisionView | None:
    if not revision_id:
        return None
    revision = await session.get(SkillRevisionRecord, revision_id)
    if revision is None:
        return None
    return SkillRevisionView(
        id=revision.id,
        version=revision.version,
        digest=revision.digest,
        published_at=revision.published_at.isoformat() if revision.published_at else None,
        revoked_at=revision.revoked_at.isoformat() if revision.revoked_at else None,
        test_only=revision.test_only,
        diagnostics=skill_diagnostics(revision.validation_report),
    )


async def skill_summary_view(session: AsyncSession, skill: SkillRecord) -> SkillSummaryView:
    report = skill.draft.validation_report if skill.draft is not None else {}
    if skill.deleted_at is not None:
        state = "removed"
    elif not skill.enabled:
        state = "disabled"
    elif skill.active_revision_id:
        state = "published"
    else:
        state = "draft"
    return SkillSummaryView(
        id=skill.id,
        name=skill.name,
        qualified_identity=f"{skill.origin}:{skill.name}",
        origin=skill.origin,
        description=skill.description,
        enabled=skill.enabled,
        readonly=skill.origin == "builtin",
        lifecycle_state=state,
        active_revision=await skill_revision_view(session, skill.active_revision_id),
        draft_revision_token=skill.draft.revision_token if skill.draft else None,
        diagnostics=skill_diagnostics(report),
        created_at=skill.created_at.isoformat(),
        updated_at=skill.updated_at.isoformat(),
    )


def skill_file_view(skill: SkillRecord, item: dict[str, Any]) -> SkillFileView:
    revision_key = skill.draft.revision_token if skill.draft else skill.active_revision_id
    scheme = "skill-draft" if skill.draft else "skill-revision"
    return SkillFileView(
        path=item["path"],
        uri=f"{scheme}://{skill.id}/{revision_key}/{item['path']}",
        digest=item["digest"],
        size_bytes=item["size_bytes"],
        media_type=item["media_type"],
        kind=item["kind"],
        text=item["text"],
        readonly=skill.origin == "builtin",
    )


def skill_revision_file_view(
    skill: SkillRecord, revision: SkillRevisionRecord, item: dict[str, Any]
) -> SkillFileView:
    return SkillFileView(
        path=item["path"],
        uri=f"skill-revision://{skill.id}/{revision.id}/{item['path']}",
        digest=item["digest"],
        size_bytes=item["size_bytes"],
        media_type=item["media_type"],
        kind=item["kind"],
        text=item["text"],
        readonly=True,
    )


async def skill_detail_view(
    session: AsyncSession, settings: AstraRuntimeSettings, skill: SkillRecord
) -> SkillDetailView:
    service = SkillService(session, settings)
    summary = await skill_summary_view(session, skill)
    files = await service.draft_files(skill)
    frontmatter: dict[str, Any] = {}
    if skill.active_revision_id:
        frontmatter = (await service.require_active_revision(skill)).frontmatter or {}
    elif skill.draft:
        try:
            text = (await service.read_file(skill, "SKILL.md")).decode("utf-8")
            import yaml

            frontmatter = yaml.safe_load(text.split("---", 2)[1]) or {}
        except (UnicodeDecodeError, ValueError):
            frontmatter = {}
    return SkillDetailView(
        **summary.model_dump(),
        files=[skill_file_view(skill, item) for _, item in sorted(files.items())],
        requested_tool_patterns=str(frontmatter.get("allowed-tools", "")).split(),
        compatibility=frontmatter.get("compatibility"),
    )
