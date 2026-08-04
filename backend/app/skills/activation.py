from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.runs import RunEventRecord
from app.db.models.skills import (
    RunSkillSnapshotRecord,
    SkillBlobRecord,
    SkillRevisionRecord,
)
from app.skills.contracts import (
    SkillActivation,
    SkillCatalogEntry,
    SkillResource,
)
from app.skills.packages import normalize_skill_path


def _binding_matches(
    binding: dict[str, str],
    activation: dict[str, Any],
    entry: SkillCatalogEntry,
) -> bool:
    return (
        binding.get("revision_id") == entry.revision_id
        and binding.get("digest") == entry.digest
        and activation.get("digest") == entry.digest
    )


def _catalog_entry(
    snapshot: RunSkillSnapshotRecord, identity: str
) -> SkillCatalogEntry | None:
    return next(
        (
            SkillCatalogEntry.model_validate(item)
            for item in snapshot.catalog
            if item["qualified_identity"] == identity
        ),
        None,
    )


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
        entry = await self._activatable_entry(run_id, snapshot, identity)
        activations = list(snapshot.activations or [])
        existing = next(
            (item for item in activations if item["qualified_identity"] == identity),
            None,
        )
        activated_now = existing is None
        activation = existing or await self._record_activation(
            run_id, snapshot, entry, identity, initiator, reason, activations
        )
        body = await self._instructions(entry)
        mode_recommendation = self._mode_recommendation(entry)
        if mode_recommendation and snapshot.answer_mode == "standard" and activated_now:
            self.session.add(
                RunEventRecord(
                    run_id=run_id,
                    type="skill.mode_recommended",
                    payload={"qualified_identity": identity, **mode_recommendation},
                )
            )
        return {
            "activation": activation,
            "entry": entry.model_dump(mode="json"),
            "instructions": body,
            "resource_root": f"skill://{snapshot.id}/{identity}/",
            "resources": [
                item.model_dump(mode="json") for item in entry.resources if item.path != "SKILL.md"
            ],
            "mode_recommendation": mode_recommendation,
        }

    async def _activatable_entry(
        self, run_id: str, snapshot: RunSkillSnapshotRecord, identity: str
    ) -> SkillCatalogEntry:
        entries = [SkillCatalogEntry.model_validate(item) for item in snapshot.catalog]
        entry = next((item for item in entries if item.qualified_identity == identity), None)
        if entry is None:
            await self._reject(run_id, identity, "absent_from_catalog")
        revision = await self.session.get(SkillRevisionRecord, entry.revision_id)
        if revision is None or revision.digest != entry.digest:
            await self._reject(run_id, identity, "revision_unavailable")
        if revision.revoked_at is not None:
            await self._reject(run_id, identity, "revision_revoked")
        return entry

    async def _record_activation(
        self,
        run_id: str,
        snapshot: RunSkillSnapshotRecord,
        entry: SkillCatalogEntry,
        identity: str,
        initiator: str,
        reason: str,
        activations: list[dict[str, Any]],
    ) -> dict[str, Any]:
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
        snapshot.activations = sorted(
            [*activations, activation], key=lambda item: item["qualified_identity"]
        )
        snapshot.updated_at = utc_now()
        self.session.add(RunEventRecord(run_id=run_id, type="skill.activated", payload=activation))
        return activation

    async def _instructions(self, entry: SkillCatalogEntry) -> str:
        blob = await self.session.get(SkillBlobRecord, entry.instructions_blob)
        if blob is None:
            raise ValueError("Frozen Skill instructions are unavailable")
        content = bytes(blob.content)
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != entry.instructions_blob:
            raise ValueError("Frozen Skill instructions failed digest verification")
        return content.decode("utf-8").split("---", 2)[-1].strip()

    @staticmethod
    def _mode_recommendation(entry: SkillCatalogEntry) -> dict[str, Any] | None:
        if entry.metadata.get("recommended_answer_mode") != "trusted":
            return None
        return {
            "answer_mode": "trusted",
            "reason": str(
                entry.metadata.get(
                    "recommendation_reason",
                    "This Skill declares a workflow that benefits from trusted planning and verification.",
                )
            )[:500],
            "automatic_switch": False,
        }

    async def read_resource(self, run_id: str, identity: str, path: str) -> bytes:
        snapshot = await self._snapshot(run_id)
        normalized = normalize_skill_path(path)
        resource = self._readable_resource(snapshot, identity, normalized)
        used = sum(int(item.get("size_bytes", 0)) for item in snapshot.resource_reads or [])
        if used + resource.size_bytes > self.max_resource_bytes:
            raise ValueError("Skill resource byte budget exceeded")
        content = await self._resource_content(resource)
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

    @staticmethod
    def _readable_resource(
        snapshot: RunSkillSnapshotRecord, identity: str, path: str
    ) -> SkillResource:
        if not any(item["qualified_identity"] == identity for item in snapshot.activations or []):
            raise ValueError("Skill is not active")
        entry = _catalog_entry(snapshot, identity)
        if entry is None:
            raise ValueError("Skill is absent from the frozen Catalog")
        resource = next((item for item in entry.resources if item.path == path), None)
        if resource is None or resource.path == "SKILL.md":
            raise ValueError("Skill resource is not available")
        if not resource.text:
            raise ValueError("Binary Skill resources cannot be disclosed to model context")
        return resource

    async def _resource_content(self, resource: SkillResource) -> bytes:
        blob = await self.session.get(SkillBlobRecord, resource.digest)
        content = bytes(blob.content) if blob is not None else b""
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if blob is None or len(content) != resource.size_bytes or digest != resource.digest:
            raise ValueError("Skill resource digest cannot be reconstructed")
        return content

    async def deactivate(self, run_id: str, identity: str, *, reason: str) -> None:
        snapshot = await self._snapshot(run_id)
        activations = list(snapshot.activations or [])
        remaining = [item for item in activations if item["qualified_identity"] != identity]
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
            blocks.append(await self._prompt_block(identity, entry))
        return blocks, snapshot

    async def _prompt_block(self, identity: str, entry: SkillCatalogEntry) -> dict[str, Any]:
        return {
            "qualified_identity": identity,
            "revision_id": entry.revision_id,
            "digest": entry.digest,
            "instructions": await self._instructions(entry),
            "metadata": entry.metadata,
        }

    async def materialize_inputs(
        self,
        run_id: str,
        bindings: list[dict[str, str]],
        destination: Path,
    ) -> list[dict[str, str]]:
        """Materialize immutable executable inputs under a sandbox's read-only /input mount."""
        snapshot = await self._snapshot(run_id)
        active = {item["qualified_identity"]: item for item in snapshot.activations or []}
        catalog = {
            item["qualified_identity"]: SkillCatalogEntry.model_validate(item)
            for item in snapshot.catalog
        }
        mounted: list[dict[str, str]] = []
        for binding in sorted(bindings, key=lambda item: item.get("qualified_identity", "")):
            identity = binding.get("qualified_identity", "")
            entry = await self._sandbox_entry(binding, active, catalog)
            origin, name = identity.split(":", 1)
            root = destination / "skills" / origin / name
            for resource in entry.resources:
                if resource.kind not in {"script", "asset"}:
                    continue
                mounted.append(await self._mount_resource(identity, root, resource))
        self._make_inputs_read_only(destination / "skills")
        return mounted

    async def _sandbox_entry(
        self,
        binding: dict[str, str],
        active: dict[str, dict[str, Any]],
        catalog: dict[str, SkillCatalogEntry],
    ) -> SkillCatalogEntry:
        identity = binding.get("qualified_identity", "")
        entry, activation = catalog.get(identity), active.get(identity)
        if entry is None or activation is None or not _binding_matches(binding, activation, entry):
            raise ValueError("Skill sandbox binding does not match the frozen snapshot")
        revision = await self.session.get(SkillRevisionRecord, entry.revision_id)
        if revision is None or revision.revoked_at is not None:
            raise ValueError("Skill sandbox input is unavailable or revoked")
        return entry

    async def _mount_resource(
        self, identity: str, root: Path, resource: SkillResource
    ) -> dict[str, str]:
        try:
            content = await self._resource_content(resource)
        except ValueError as exc:
            raise ValueError("Skill sandbox resource failed digest verification") from exc
        target = (root / Path(normalize_skill_path(resource.path))).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError("Skill sandbox resource escaped its input root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o444)
        origin, name = identity.split(":", 1)
        return {
            "qualified_identity": identity,
            "path": resource.path,
            "digest": resource.digest,
            "sandbox_path": f"/input/skills/{origin}/{name}/{resource.path}",
        }

    @staticmethod
    def _make_inputs_read_only(skills_root: Path) -> None:
        if not skills_root.exists():
            return
        directories = (item for item in skills_root.rglob("*") if item.is_dir())
        for directory in sorted(directories, reverse=True):
            directory.chmod(0o555)
        skills_root.chmod(0o555)

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
