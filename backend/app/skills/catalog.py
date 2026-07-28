from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    RunEventRecord,
    RunSkillSnapshotRecord,
    SkillBlobRecord,
    SkillRecord,
    SkillRevisionRecord,
    utc_now,
)
from app.skills.contracts import (
    SkillActivation,
    SkillCatalogEntry,
    SkillOrigin,
    SkillResource,
)
from app.skills.packages import normalize_skill_path


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
        rows = (await self.session.execute(
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
        )).all()
        entries = [self._entry(skill, revision) for skill, revision in rows]
        for revision in revision_overrides or []:
            skill = await self.session.get(SkillRecord, revision.skill_id)
            if skill is not None:
                entries = [
                    item for item in entries if item.qualified_identity != f"{skill.origin}:{skill.name}"
                ]
                entries.append(self._entry(skill, revision))
        entries = [
            item
            for item in entries
            if self._compatible(
                item,
                runtime_capabilities=runtime_capabilities,
                runtime_version=runtime_version,
            )
        ]
        entries.sort(key=lambda item: item.qualified_identity)
        identities = [item.qualified_identity for item in entries]
        if len(identities) != len(set(identities)):
            raise ValueError("Skill Catalog contains duplicate identities")
        full_payload = [item.model_dump(mode="json") for item in entries]
        digest = _stable_digest(full_payload)
        rendered = json.dumps(full_payload, ensure_ascii=False, sort_keys=True)
        if len(rendered) <= self.metadata_chars:
            return SkillCatalog(tuple(entries), digest)
        explicit = set(explicit_identities or [])
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
            if selected and used + size > self.metadata_chars and item.qualified_identity not in explicit:
                continue
            selected.append(item)
            used += size
        selected.sort(key=lambda item: item.qualified_identity)
        selected_payload = [item.model_dump(mode="json") for item in selected]
        return SkillCatalog(
            tuple(selected),
            _stable_digest(selected_payload),
            truncated=True,
        )

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
        required = tuple(
            int(part) for part in (match.group(1).split(".") + ["0", "0"])[:3]
        )
        current = tuple(
            int(part) for part in (runtime_version.split(".") + ["0", "0"])[:3]
        )
        return current >= required

    @staticmethod
    def _entry(skill: SkillRecord, revision: SkillRevisionRecord) -> SkillCatalogEntry:
        frontmatter = revision.frontmatter or {}
        resources = [
            SkillResource.model_validate(item)
            for item in revision.manifest.get("resources", [])
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
                frontmatter.get("metadata")
                if isinstance(frontmatter.get("metadata"), dict)
                else {}
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
                select(RunSkillSnapshotRecord).where(
                    RunSkillSnapshotRecord.run_id == run_id
                )
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


class SkillActivationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        max_active: int = 8,
        max_resource_bytes: int = 8 * 1024 * 1024,
    ):
        self.session = session
        self.max_active = max_active
        self.max_resource_bytes = max_resource_bytes

    async def activate(
        self,
        run_id: str,
        identity: str,
        *,
        initiator: str,
        reason: str,
    ) -> dict[str, Any]:
        snapshot = await self._snapshot(run_id)
        entries = [SkillCatalogEntry.model_validate(item) for item in snapshot.catalog]
        entry = next((item for item in entries if item.qualified_identity == identity), None)
        if entry is None:
            await self._reject(run_id, identity, "absent_from_catalog")
        revision = await self.session.get(SkillRevisionRecord, entry.revision_id)
        if revision is None or revision.digest != entry.digest:
            await self._reject(run_id, identity, "revision_unavailable")
        if revision.revoked_at is not None:
            await self._reject(run_id, identity, "revision_revoked")
        activations = list(snapshot.activations or [])
        existing = next(
            (item for item in activations if item["qualified_identity"] == identity),
            None,
        )
        activated_now = existing is None
        activation = existing
        if activation is None:
            if len(activations) >= self.max_active:
                await self._reject(run_id, identity, "activation_budget_exceeded")
            activation = SkillActivation(
                qualified_identity=identity,
                revision_id=entry.revision_id,
                digest=entry.digest,
                initiator=initiator,
                reason=reason,
                activated_at=datetime.now(timezone.utc).isoformat(),
            ).model_dump(mode="json")
            activations.append(activation)
            activations.sort(key=lambda item: item["qualified_identity"])
            snapshot.activations = activations
            snapshot.updated_at = utc_now()
            self.session.add(
                RunEventRecord(
                    run_id=run_id,
                    type="skill.activated",
                    payload=activation,
                )
            )
        blob = await self.session.get(SkillBlobRecord, entry.instructions_blob)
        if blob is None:
            raise ValueError("Frozen Skill instructions are unavailable")
        raw_content = bytes(blob.content)
        if (
            f"sha256:{hashlib.sha256(raw_content).hexdigest()}"
            != entry.instructions_blob
        ):
            raise ValueError("Frozen Skill instructions failed digest verification")
        content = raw_content.decode("utf-8")
        body = content.split("---", 2)[-1].strip()
        mode_recommendation = None
        if entry.metadata.get("recommended_answer_mode") == "trusted":
            mode_recommendation = {
                "answer_mode": "trusted",
                "reason": str(
                    entry.metadata.get(
                        "recommendation_reason",
                        "This Skill declares a workflow that benefits from trusted planning and verification.",
                    )
                )[:500],
                "automatic_switch": False,
            }
            if snapshot.answer_mode == "standard" and activated_now:
                self.session.add(
                    RunEventRecord(
                        run_id=run_id,
                        type="skill.mode_recommended",
                        payload={
                            "qualified_identity": identity,
                            **mode_recommendation,
                        },
                    )
                )
        return {
            "activation": activation,
            "entry": entry.model_dump(mode="json"),
            "instructions": body,
            "resource_root": f"skill://{snapshot.id}/{identity}/",
            "resources": [
                item.model_dump(mode="json")
                for item in entry.resources
                if item.path != "SKILL.md"
            ],
            "mode_recommendation": mode_recommendation,
        }

    async def read_resource(self, run_id: str, identity: str, path: str) -> bytes:
        snapshot = await self._snapshot(run_id)
        normalized = normalize_skill_path(path)
        if not any(
            item["qualified_identity"] == identity for item in snapshot.activations or []
        ):
            raise ValueError("Skill is not active")
        entry = next(
            (
                SkillCatalogEntry.model_validate(item)
                for item in snapshot.catalog
                if item["qualified_identity"] == identity
            ),
            None,
        )
        if entry is None:
            raise ValueError("Skill is absent from the frozen Catalog")
        resource = next((item for item in entry.resources if item.path == normalized), None)
        if resource is None or resource.path == "SKILL.md":
            raise ValueError("Skill resource is not available")
        if not resource.text:
            raise ValueError("Binary Skill resources cannot be disclosed to model context")
        used = sum(int(item.get("size_bytes", 0)) for item in snapshot.resource_reads or [])
        if used + resource.size_bytes > self.max_resource_bytes:
            raise ValueError("Skill resource byte budget exceeded")
        blob = await self.session.get(SkillBlobRecord, resource.digest)
        content = bytes(blob.content) if blob is not None else b""
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if (
            blob is None
            or len(content) != resource.size_bytes
            or digest != resource.digest
        ):
            raise ValueError("Skill resource digest cannot be reconstructed")
        read = {
            "qualified_identity": identity,
            "path": normalized,
            "digest": resource.digest,
            "size_bytes": resource.size_bytes,
            "read_at": datetime.now(timezone.utc).isoformat(),
        }
        snapshot.resource_reads = [*(snapshot.resource_reads or []), read]
        snapshot.updated_at = utc_now()
        self.session.add(RunEventRecord(run_id=run_id, type="skill.resource_read", payload=read))
        return content

    async def deactivate(self, run_id: str, identity: str, *, reason: str) -> None:
        snapshot = await self._snapshot(run_id)
        activations = list(snapshot.activations or [])
        remaining = [
            item for item in activations if item["qualified_identity"] != identity
        ]
        if len(remaining) == len(activations):
            return
        snapshot.activations = remaining
        snapshot.updated_at = utc_now()
        self.session.add(
            RunEventRecord(
                run_id=run_id,
                type="skill.deactivated",
                payload={"qualified_identity": identity, "reason": reason},
            )
        )

    async def prompt_blocks(
        self,
        run_id: str,
        *,
        identities: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        blocks, _ = await self.prompt_blocks_with_snapshot(
            run_id,
            identities=identities,
        )
        return blocks

    async def prompt_blocks_with_snapshot(
        self,
        run_id: str,
        *,
        identities: set[str] | None = None,
        snapshot: RunSkillSnapshotRecord | None = None,
    ) -> tuple[list[dict[str, Any]], RunSkillSnapshotRecord]:
        if snapshot is None:
            snapshot = await self._snapshot(run_id)
        active = {
            item["qualified_identity"]: item
            for item in snapshot.activations or []
            if identities is None or item["qualified_identity"] in identities
        }
        entries = {
            item["qualified_identity"]: SkillCatalogEntry.model_validate(item)
            for item in snapshot.catalog
            if item["qualified_identity"] in active
        }
        blocks: list[dict[str, Any]] = []
        for identity in sorted(active):
            entry = entries.get(identity)
            if entry is None:
                raise ValueError("Active Skill is absent from the frozen Catalog")
            blob = await self.session.get(SkillBlobRecord, entry.instructions_blob)
            if blob is None:
                raise ValueError("Frozen Skill instructions are unavailable")
            content = bytes(blob.content)
            if f"sha256:{hashlib.sha256(content).hexdigest()}" != entry.instructions_blob:
                raise ValueError("Frozen Skill instructions failed digest verification")
            instructions = content.decode("utf-8").split("---", 2)[-1].strip()
            blocks.append(
                {
                    "qualified_identity": identity,
                    "revision_id": entry.revision_id,
                    "digest": entry.digest,
                    "instructions": instructions,
                    "metadata": entry.metadata,
                }
            )
        return blocks, snapshot

    async def materialize_inputs(
        self,
        run_id: str,
        bindings: list[dict[str, str]],
        destination: Path,
    ) -> list[dict[str, str]]:
        """Materialize immutable executable inputs under a sandbox's read-only /input mount."""
        snapshot = await self._snapshot(run_id)
        active = {
            item["qualified_identity"]: item for item in snapshot.activations or []
        }
        catalog = {
            item["qualified_identity"]: SkillCatalogEntry.model_validate(item)
            for item in snapshot.catalog
        }
        mounted: list[dict[str, str]] = []
        for binding in sorted(
            bindings, key=lambda item: item.get("qualified_identity", "")
        ):
            identity = binding.get("qualified_identity", "")
            entry = catalog.get(identity)
            activation = active.get(identity)
            if (
                entry is None
                or activation is None
                or binding.get("revision_id") != entry.revision_id
                or binding.get("digest") != entry.digest
                or activation.get("digest") != entry.digest
            ):
                raise ValueError("Skill sandbox binding does not match the frozen snapshot")
            revision = await self.session.get(SkillRevisionRecord, entry.revision_id)
            if revision is None or revision.revoked_at is not None:
                raise ValueError("Skill sandbox input is unavailable or revoked")
            origin, name = identity.split(":", 1)
            root = destination / "skills" / origin / name
            for resource in entry.resources:
                if resource.kind not in {"script", "asset"}:
                    continue
                blob = await self.session.get(SkillBlobRecord, resource.digest)
                content = bytes(blob.content) if blob is not None else b""
                if (
                    blob is None
                    or len(content) != resource.size_bytes
                    or f"sha256:{hashlib.sha256(content).hexdigest()}"
                    != resource.digest
                ):
                    raise ValueError("Skill sandbox resource failed digest verification")
                relative = Path(normalize_skill_path(resource.path))
                target = (root / relative).resolve()
                if not target.is_relative_to(root.resolve()):
                    raise ValueError("Skill sandbox resource escaped its input root")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o444)
                mounted.append(
                    {
                        "qualified_identity": identity,
                        "path": resource.path,
                        "digest": resource.digest,
                        "sandbox_path": f"/input/skills/{origin}/{name}/{resource.path}",
                    }
                )
        skills_root = destination / "skills"
        if skills_root.exists():
            for directory in sorted(
                (item for item in skills_root.rglob("*") if item.is_dir()),
                reverse=True,
            ):
                directory.chmod(0o555)
            skills_root.chmod(0o555)
        return mounted

    async def _snapshot(self, run_id: str) -> RunSkillSnapshotRecord:
        snapshot = await self.session.scalar(
            select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run_id)
        )
        if snapshot is None:
            raise ValueError("Run Skill Catalog snapshot is unavailable")
        return snapshot

    async def _reject(self, run_id: str, identity: str, reason: str) -> None:
        self.session.add(
            RunEventRecord(
                run_id=run_id,
                type="skill.activation_conflict",
                payload={
                    "qualified_identity": identity,
                    "reason": reason,
                },
            )
        )
        await self.session.flush()
        raise ValueError(f"Skill activation rejected: {reason}")
