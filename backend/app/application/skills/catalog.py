from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.skills.contracts import (
    SkillCatalogEntry,
    SkillOrigin,
    SkillResource,
)
from app.infrastructure.db.models.runs import RunEventRecord
from app.infrastructure.db.models.skills import (
    RunSkillSnapshotRecord,
    SkillRecord,
    SkillRevisionRecord,
)


def _stable_digest(payload: Any) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(data.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class SkillCatalog:
    entries: tuple[SkillCatalogEntry, ...]
    digest: str
    truncated: bool = False

    def metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "qualified_identity": item.qualified_identity,
                "name": item.name,
                "description": item.description,
                "origin": item.origin.value,
                "revision_id": item.revision_id,
                "digest": item.digest,
                "compatibility": item.compatibility,
                "metadata": item.metadata,
                "requested_tool_patterns": item.requested_tool_patterns,
            }
            for item in self.entries
        ]

    def require(self, identity: str) -> SkillCatalogEntry:
        for entry in self.entries:
            if entry.qualified_identity == identity:
                return entry
        raise ValueError("Skill is absent from the frozen Catalog")


class SkillCatalogBuilder:
    def __init__(self, session: AsyncSession, *, metadata_chars: int = 24_000):
        self.session = session
        self.metadata_chars = metadata_chars

    async def build(
        self,
        *,
        goal: str = "",
        explicit_identities: list[str] | None = None,
        revision_overrides: list[SkillRevisionRecord] | None = None,
        runtime_capabilities: set[str] | None = None,
        runtime_version: str = "0.1.0",
    ) -> SkillCatalog:
        entries = await self._available_entries(revision_overrides or [])
        entries = [
            entry
            for entry in entries
            if self._compatible(
                entry,
                runtime_capabilities=runtime_capabilities,
                runtime_version=runtime_version,
            )
        ]
        entries.sort(key=lambda entry: entry.qualified_identity)
        self._ensure_unique(entries)
        payload = [entry.model_dump(mode="json") for entry in entries]
        if len(json.dumps(payload, ensure_ascii=False, sort_keys=True)) <= self.metadata_chars:
            return SkillCatalog(tuple(entries), _stable_digest(payload))
        selected = self._select_entries(entries, goal, set(explicit_identities or []))
        selected_payload = [entry.model_dump(mode="json") for entry in selected]
        return SkillCatalog(tuple(selected), _stable_digest(selected_payload), truncated=True)

    async def _available_entries(
        self, revision_overrides: list[SkillRevisionRecord]
    ) -> list[SkillCatalogEntry]:
        rows = (
            await self.session.execute(
                select(SkillRecord, SkillRevisionRecord)
                .join(
                    SkillRevisionRecord,
                    SkillRevisionRecord.id == SkillRecord.active_revision_id,
                )
                .where(
                    SkillRecord.enabled.is_(True),
                    SkillRecord.deleted_at.is_(None),
                    SkillRevisionRecord.revoked_at.is_(None),
                )
                .order_by(SkillRecord.origin, SkillRecord.name)
            )
        ).all()
        entries = [self._entry(skill, revision) for skill, revision in rows]
        for revision in revision_overrides:
            skill = await self.session.get(SkillRecord, revision.skill_id)
            if skill is not None:
                entries = [
                    item
                    for item in entries
                    if item.qualified_identity != f"{skill.origin}:{skill.name}"
                ]
                entries.append(self._entry(skill, revision))
        return entries

    @staticmethod
    def _ensure_unique(entries: list[SkillCatalogEntry]) -> None:
        identities = [entry.qualified_identity for entry in entries]
        if len(identities) != len(set(identities)):
            raise ValueError("Skill Catalog contains duplicate identities")

    def _select_entries(
        self,
        entries: list[SkillCatalogEntry],
        goal: str,
        explicit: set[str],
    ) -> list[SkillCatalogEntry]:
        terms = {term for term in goal.lower().replace("-", " ").split() if len(term) > 1}
        ranked = sorted(
            entries,
            key=lambda item: (
                item.qualified_identity not in explicit,
                -sum(term in f"{item.name} {item.description}".lower() for term in terms),
                item.qualified_identity,
            ),
        )
        selected: list[SkillCatalogEntry] = []
        used = 2
        for item in ranked:
            size = len(json.dumps(item.model_dump(mode="json"), ensure_ascii=False))
            if (
                selected
                and used + size > self.metadata_chars
                and item.qualified_identity not in explicit
            ):
                continue
            selected.append(item)
            used += size
        return sorted(selected, key=lambda item: item.qualified_identity)

    @staticmethod
    def _compatible(
        entry: SkillCatalogEntry,
        *,
        runtime_capabilities: set[str] | None,
        runtime_version: str,
    ) -> bool:
        if runtime_capabilities is not None and any(
            pattern not in runtime_capabilities
            for pattern in entry.requested_tool_patterns
            if "*" not in pattern
        ):
            return False
        declaration = entry.compatibility or ""
        match = re.search(
            r"(?:astra\s*(?:>=|v)?\s*|Astra\s+)(\d+(?:\.\d+){0,2})\+?",
            declaration,
            re.I,
        )
        if not match:
            return True
        required = tuple(int(part) for part in (match.group(1).split(".") + ["0", "0"])[:3])
        current = tuple(int(part) for part in (runtime_version.split(".") + ["0", "0"])[:3])
        return current >= required

    @staticmethod
    def _entry(skill: SkillRecord, revision: SkillRevisionRecord) -> SkillCatalogEntry:
        frontmatter = revision.frontmatter or {}
        resources = [
            SkillResource.model_validate(item) for item in revision.manifest.get("resources", [])
        ]
        skill_file = next(item for item in resources if item.path == "SKILL.md")
        return SkillCatalogEntry(
            qualified_identity=f"{skill.origin}:{skill.name}",
            name=skill.name,
            description=str(frontmatter.get("description", skill.description)),
            origin=SkillOrigin(skill.origin),
            revision_id=revision.id,
            digest=revision.digest,
            compatibility=frontmatter.get("compatibility"),
            metadata=(
                frontmatter.get("metadata") if isinstance(frontmatter.get("metadata"), dict) else {}
            ),
            requested_tool_patterns=(
                str(frontmatter.get("allowed-tools")).split()
                if frontmatter.get("allowed-tools")
                else []
            ),
            resources=resources,
            instructions_blob=skill_file.digest,
            revoked=revision.revoked_at is not None,
        )

    async def freeze(
        self,
        run_id: str,
        answer_mode: str,
        catalog: SkillCatalog,
        *,
        draft_test: bool = False,
        new_run: bool = False,
    ) -> RunSkillSnapshotRecord:
        existing = None
        if not new_run:
            existing = await self.session.scalar(
                select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run_id)
            )
        payload = [item.model_dump(mode="json") for item in catalog.entries]
        if existing is not None:
            if existing.catalog_digest != catalog.digest or existing.catalog != payload:
                raise ValueError("Run Skill Catalog snapshot is immutable")
            return existing
        snapshot = RunSkillSnapshotRecord(
            run_id=run_id,
            catalog_digest=catalog.digest,
            catalog=payload,
            answer_mode=answer_mode,
            draft_test=draft_test,
        )
        self.session.add(snapshot)
        self.session.add(
            RunEventRecord(
                run_id=run_id,
                type="skill.catalog_frozen",
                payload={
                    "digest": catalog.digest,
                    "count": len(catalog.entries),
                    "truncated": catalog.truncated,
                    "draft_test": draft_test,
                },
            )
        )
        await self.session.flush()
        return snapshot
