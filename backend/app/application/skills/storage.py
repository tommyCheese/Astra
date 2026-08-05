from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Mapping
from copy import deepcopy
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.skills.archive import read_skill_archive, write_skill_archive
from app.application.skills.contracts import SkillOrigin, SkillPackage
from app.application.skills.errors import SkillStorageError
from app.application.skills.packages import (
    SkillPackageError,
    normalize_skill_path,
    parse_skill_package,
)
from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.skills import (
    SkillAuditRecord,
    SkillBlobRecord,
    SkillDraftRecord,
    SkillRecord,
    SkillRevisionRecord,
)


def _blob_digest(files: dict[str, dict[str, Any]], path: str) -> str:
    item = files.get(path)
    if not item:
        raise SkillStorageError("SKILL_FILE_NOT_FOUND", "找不到 Skill 文件。")
    return str(item["digest"])


class SkillService:
    def __init__(self, session: AsyncSession, settings: AstraRuntimeSettings):
        self.session = session
        self.settings = settings

    def _require_custom_authoring(self) -> None:
        if not self.settings.skills_enabled or not self.settings.skills_custom_authoring_enabled:
            raise SkillStorageError(
                "SKILL_AUTHORING_DISABLED",
                "自定义 Skill 创作功能当前未启用。",
            )

    async def list_skills(self, *, include_deleted: bool = False) -> list[SkillRecord]:
        statement = (
            select(SkillRecord)
            .options(
                selectinload(SkillRecord.draft),
                selectinload(SkillRecord.revisions),
            )
            .order_by(SkillRecord.origin, SkillRecord.name)
        )
        if not include_deleted:
            statement = statement.where(SkillRecord.deleted_at.is_(None))
        return list((await self.session.scalars(statement)).all())

    async def require_skill(self, skill_id: str) -> SkillRecord:
        skill = await self.session.get(
            SkillRecord,
            skill_id,
            options=[
                selectinload(SkillRecord.draft),
                selectinload(SkillRecord.revisions),
            ],
        )
        if skill is None or skill.deleted_at is not None:
            raise SkillStorageError("SKILL_NOT_FOUND", "找不到指定 Skill。")
        return skill

    async def create_custom(
        self,
        name: str,
        description: str,
        *,
        files: Mapping[str, bytes | str] | None = None,
    ) -> SkillRecord:
        self._require_custom_authoring()
        if await self._by_name(name) is not None:
            raise SkillStorageError("SKILL_IDENTITY_CONFLICT", "Skill name 已存在。")
        if files is None:
            files = {
                "SKILL.md": (
                    f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}"
                    "\n---\n\n# Workflow\n\nDescribe the repeatable workflow here.\n"
                )
            }
        package, normalized = self._parse(files, SkillOrigin.custom, directory_name=name)
        stored = await self._store_files(normalized, package)
        now = utc_now()
        skill = SkillRecord(
            name=package.frontmatter.name,
            origin=SkillOrigin.custom.value,
            description=package.frontmatter.description,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        skill.draft = SkillDraftRecord(
            revision_token=str(uuid.uuid4()),
            files=stored,
            validation_report=self._report(package),
            created_at=now,
            updated_at=now,
        )
        self.session.add(skill)
        await self.session.flush()
        self._audit(
            skill.id,
            "skill.created",
            {"name": skill.name, "origin": skill.origin},
        )
        return skill

    async def import_zip(self, archive: bytes, *, filename: str = "skill.zip") -> SkillRecord:
        files = read_skill_archive(archive, self.settings)
        skill_text = files.get("SKILL.md", b"").decode("utf-8", errors="ignore")
        provisional_name = _frontmatter_name(skill_text)
        skill = await self.create_custom(provisional_name, provisional_name, files=files)
        self._audit(
            skill.id,
            "skill.imported",
            {"filename": filename, "archive_bytes": len(archive)},
        )
        return skill

    async def clone_builtin(self, skill_id: str, name: str) -> SkillRecord:
        source = await self.require_skill(skill_id)
        revision = await self.require_active_revision(source)
        files = await self.materialize_manifest(revision.manifest)
        skill_text = files["SKILL.md"].decode("utf-8")
        files["SKILL.md"] = _replace_frontmatter_name(skill_text, name).encode("utf-8")
        return await self.create_custom(name, source.description, files=files)

    async def draft_files(self, skill: SkillRecord) -> dict[str, dict[str, Any]]:
        if skill.origin == SkillOrigin.builtin.value:
            revision = await self.require_active_revision(skill)
            return deepcopy(revision.manifest.get("files", {}))
        if skill.draft is None:
            raise SkillStorageError("SKILL_DRAFT_NOT_FOUND", "Skill 没有可编辑草稿。")
        return deepcopy(skill.draft.files or {})

    async def read_file(
        self, skill: SkillRecord, path: str, *, revision_id: str | None = None
    ) -> bytes:
        normalized = normalize_skill_path(path)
        if revision_id:
            revision = await self.require_revision(skill.id, revision_id)
            manifest = revision.manifest.get("files", {})
        else:
            manifest = await self.draft_files(skill)
        digest = _blob_digest(manifest, normalized)
        blob = await self.session.get(SkillBlobRecord, digest)
        if blob is None:
            raise SkillStorageError("SKILL_BLOB_MISSING", "Skill 文件内容已不可用。")
        return bytes(blob.content)

    async def update_draft(
        self,
        skill_id: str,
        expected_token: str,
        operations: list[dict[str, Any]],
    ) -> SkillDraftRecord:
        self._require_custom_authoring()
        skill = await self.require_skill(skill_id)
        if skill.origin != SkillOrigin.custom.value or skill.draft is None:
            raise SkillStorageError("SKILL_BUILTIN_READONLY", "Astra 内建 Skill 不可修改。")
        if skill.draft.revision_token != expected_token:
            raise SkillStorageError(
                "SKILL_DRAFT_STALE",
                "Skill 草稿已发生变化。",
                {"current_revision_token": skill.draft.revision_token},
            )
        current = await self.materialize_manifest({"files": skill.draft.files})
        next_files = self._apply_draft_operations(current, operations)
        package, normalized = self._parse(
            next_files,
            SkillOrigin.custom,
            directory_name=skill.name,
            tolerate_safety=True,
        )
        skill.draft.files = await self._store_files(normalized, package)
        skill.draft.validation_report = self._report(package)
        skill.draft.revision_token = str(uuid.uuid4())
        skill.draft.updated_at = utc_now()
        skill.description = package.frontmatter.description
        skill.updated_at = utc_now()
        await self.session.flush()
        self._audit(
            skill.id,
            "skill.draft_edited",
            {
                "operation_count": len(operations),
                "revision_token": skill.draft.revision_token,
                "diagnostic_count": len(package.diagnostics),
            },
        )
        return skill.draft

    @staticmethod
    def _apply_draft_operations(
        current: dict[str, bytes], operations: list[dict[str, Any]]
    ) -> dict[str, bytes]:
        next_files = dict(current)
        for operation in operations:
            action = str(operation.get("action", "write"))
            path = normalize_skill_path(str(operation.get("path", "")))
            if action == "write":
                if "content_base64" in operation:
                    try:
                        content = base64.b64decode(operation["content_base64"], validate=True)
                    except ValueError as exc:
                        raise SkillStorageError(
                            "SKILL_FILE_ENCODING_INVALID", "文件编码无效。"
                        ) from exc
                else:
                    content = str(operation.get("content", "")).encode("utf-8")
                next_files[path] = content
            elif action == "delete":
                if path == "SKILL.md":
                    raise SkillStorageError("SKILL_INSTRUCTIONS_REQUIRED", "不能删除 SKILL.md。")
                next_files.pop(path, None)
            elif action == "move":
                target = normalize_skill_path(str(operation.get("target", "")))
                if path not in next_files:
                    raise SkillStorageError("SKILL_FILE_NOT_FOUND", "找不到需要移动的文件。")
                if target in next_files:
                    raise SkillStorageError("SKILL_FILE_CONFLICT", "目标文件已存在。")
                next_files[target] = next_files.pop(path)
            else:
                raise SkillStorageError("SKILL_FILE_OPERATION_INVALID", "不支持的文件操作。")
        return next_files

    async def validate_draft(self, skill: SkillRecord) -> dict[str, Any]:
        files = await self.materialize_manifest({"files": await self.draft_files(skill)})
        try:
            package, _ = self._parse(
                files,
                SkillOrigin(skill.origin),
                directory_name=skill.name,
                tolerate_safety=True,
            )
            report = self._report(package)
        except SkillPackageError as exc:
            report = {
                "valid": False,
                "publishable": False,
                "diagnostics": [item.model_dump(mode="json") for item in exc.diagnostics],
            }
        if skill.draft is not None:
            skill.draft.validation_report = report
        self._audit(
            skill.id,
            "skill.validated",
            {
                "valid": report["valid"],
                "publishable": report["publishable"],
                "diagnostic_codes": [item["code"] for item in report.get("diagnostics", [])],
            },
        )
        return report

    async def publish(self, skill_id: str, expected_token: str) -> SkillRevisionRecord:
        self._require_custom_authoring()
        skill = await self.require_skill(skill_id)
        if skill.origin != SkillOrigin.custom.value or skill.draft is None:
            raise SkillStorageError("SKILL_BUILTIN_READONLY", "Astra 内建 Skill 不可发布。")
        if skill.draft.revision_token != expected_token:
            raise SkillStorageError(
                "SKILL_DRAFT_STALE",
                "Skill 草稿已发生变化，请重新校验后发布。",
                {"current_revision_token": skill.draft.revision_token},
            )
        files = await self.materialize_manifest({"files": skill.draft.files})
        package, normalized = self._parse(files, SkillOrigin.custom, directory_name=skill.name)
        manifest_files = await self._store_files(normalized, package)
        maximum = await self.session.scalar(
            select(func.max(SkillRevisionRecord.version)).where(
                SkillRevisionRecord.skill_id == skill.id,
                SkillRevisionRecord.test_only.is_(False),
            )
        )
        predecessor_id = skill.active_revision_id
        now = utc_now()
        revision = SkillRevisionRecord(
            skill_id=skill.id,
            version=int(maximum or 0) + 1,
            digest=package.digest,
            frontmatter=package.frontmatter.model_dump(by_alias=True, mode="json"),
            manifest={
                "files": manifest_files,
                "resources": [item.model_dump(mode="json") for item in package.resources],
            },
            validation_report=self._report(package),
            predecessor_id=predecessor_id,
            test_only=False,
            created_at=now,
            published_at=now,
        )
        self.session.add(revision)
        await self.session.flush()
        skill.active_revision_id = revision.id
        skill.description = package.frontmatter.description
        skill.updated_at = now
        skill.draft.validation_report = self._report(package)
        skill.draft.revision_token = str(uuid.uuid4())
        skill.draft.updated_at = now
        self._audit(
            skill.id,
            "skill.published",
            {
                "revision_id": revision.id,
                "version": revision.version,
                "digest": revision.digest,
            },
        )
        return revision

    async def create_test_revision(self, skill_id: str, expected_token: str) -> SkillRevisionRecord:
        self._require_custom_authoring()
        skill = await self.require_skill(skill_id)
        if skill.draft is None or skill.draft.revision_token != expected_token:
            raise SkillStorageError("SKILL_DRAFT_STALE", "Skill 草稿已发生变化。")
        recent_tests = int(
            await self.session.scalar(
                select(func.count())
                .select_from(SkillRevisionRecord)
                .where(
                    SkillRevisionRecord.test_only.is_(True),
                    SkillRevisionRecord.created_at >= utc_now() - timedelta(hours=1),
                )
            )
            or 0
        )
        if recent_tests >= self.settings.skills_max_draft_tests_per_hour:
            raise SkillStorageError(
                "SKILL_DRAFT_TEST_RATE_LIMITED",
                "Draft 测试已达到每小时上限，请稍后重试。",
                {"limit": self.settings.skills_max_draft_tests_per_hour},
            )
        files = await self.materialize_manifest({"files": skill.draft.files})
        package, normalized = self._parse(files, SkillOrigin.custom, directory_name=skill.name)
        manifest_files = await self._store_files(normalized, package)
        minimum = await self.session.scalar(
            select(func.min(SkillRevisionRecord.version)).where(
                SkillRevisionRecord.skill_id == skill.id,
                SkillRevisionRecord.test_only.is_(True),
            )
        )
        revision = SkillRevisionRecord(
            skill_id=skill.id,
            version=min(int(minimum or 0) - 1, -1),
            digest=package.digest,
            frontmatter=package.frontmatter.model_dump(by_alias=True, mode="json"),
            manifest={
                "files": manifest_files,
                "resources": [item.model_dump(mode="json") for item in package.resources],
            },
            validation_report=self._report(package),
            test_only=True,
            created_at=utc_now(),
        )
        self.session.add(revision)
        await self.session.flush()
        self._audit(
            skill.id,
            "skill.draft_test_frozen",
            {"revision_id": revision.id, "digest": revision.digest},
        )
        return revision

    async def revisions(self, skill_id: str) -> list[SkillRevisionRecord]:
        return list(
            (
                await self.session.scalars(
                    select(SkillRevisionRecord)
                    .where(
                        SkillRevisionRecord.skill_id == skill_id,
                        SkillRevisionRecord.test_only.is_(False),
                    )
                    .order_by(SkillRevisionRecord.version.desc())
                )
            ).all()
        )

    async def restore(self, skill_id: str, revision_id: str) -> SkillDraftRecord:
        self._require_custom_authoring()
        skill = await self.require_skill(skill_id)
        if skill.origin != SkillOrigin.custom.value or skill.draft is None:
            raise SkillStorageError("SKILL_BUILTIN_READONLY", "Astra 内建 Skill 不可恢复为草稿。")
        revision = await self.require_revision(skill_id, revision_id)
        skill.draft.files = deepcopy(revision.manifest.get("files", {}))
        skill.draft.validation_report = deepcopy(revision.validation_report or {})
        skill.draft.revision_token = str(uuid.uuid4())
        skill.draft.updated_at = utc_now()
        self._audit(
            skill.id,
            "skill.revision_restored",
            {"revision_id": revision.id, "revision_token": skill.draft.revision_token},
        )
        return skill.draft

    async def set_enabled(self, skill_id: str, enabled: bool) -> SkillRecord:
        skill = await self.require_skill(skill_id)
        skill.enabled = enabled
        skill.updated_at = utc_now()
        self._audit(skill.id, "skill.state_changed", {"enabled": enabled})
        return skill

    async def remove(self, skill_id: str) -> None:
        self._require_custom_authoring()
        skill = await self.require_skill(skill_id)
        if skill.origin == SkillOrigin.builtin.value:
            raise SkillStorageError("SKILL_BUILTIN_READONLY", "Astra 内建 Skill 不可删除。")
        skill.enabled = False
        skill.deleted_at = utc_now()
        skill.updated_at = utc_now()
        self._audit(skill.id, "skill.removed", {"recoverable": True})

    async def require_revision(self, skill_id: str, revision_id: str) -> SkillRevisionRecord:
        revision = await self.session.get(SkillRevisionRecord, revision_id)
        if revision is None or revision.skill_id != skill_id:
            raise SkillStorageError("SKILL_REVISION_NOT_FOUND", "找不到 Skill revision。")
        return revision

    async def require_active_revision(self, skill: SkillRecord) -> SkillRevisionRecord:
        if not skill.active_revision_id:
            raise SkillStorageError("SKILL_NOT_PUBLISHED", "Skill 尚未发布。")
        return await self.require_revision(skill.id, skill.active_revision_id)

    async def materialize_manifest(self, manifest: dict[str, Any]) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for path, item in (manifest.get("files", {}) or {}).items():
            blob = await self.session.get(SkillBlobRecord, item["digest"])
            if blob is None:
                raise SkillStorageError("SKILL_BLOB_MISSING", "Skill 内容已不可用。")
            result[path] = bytes(blob.content)
        return result

    async def export_zip(self, skill: SkillRecord, *, revision_id: str | None = None) -> bytes:
        if revision_id:
            revision = await self.require_revision(skill.id, revision_id)
            files = await self.materialize_manifest(revision.manifest)
        elif skill.origin == SkillOrigin.custom.value and skill.draft is not None:
            files = await self.materialize_manifest({"files": skill.draft.files})
        else:
            files = await self.materialize_manifest(
                (await self.require_active_revision(skill)).manifest
            )
        return write_skill_archive(skill.name, files)

    async def _by_name(self, name: str) -> SkillRecord | None:
        return await self.session.scalar(
            select(SkillRecord).where(SkillRecord.name == name, SkillRecord.deleted_at.is_(None))
        )

    def _audit(self, skill_id: str | None, type_: str, payload: dict[str, Any]) -> None:
        self.session.add(SkillAuditRecord(skill_id=skill_id, type=type_, payload=payload))

    def _parse(
        self,
        files: Mapping[str, bytes | str],
        origin: SkillOrigin,
        *,
        directory_name: str | None = None,
        tolerate_safety: bool = False,
    ) -> tuple[SkillPackage, dict[str, bytes]]:
        try:
            return parse_skill_package(
                files,
                origin=origin,
                directory_name=directory_name,
                max_files=self.settings.skills_max_files,
                max_file_bytes=self.settings.skills_max_file_bytes,
                max_package_bytes=self.settings.skills_max_package_bytes,
                max_instruction_chars=self.settings.skills_max_instruction_chars,
                reject_reserved_custom_identity=True,
                raise_on_safety=not tolerate_safety,
            )
        except SkillPackageError:
            raise

    async def _store_files(
        self, files: Mapping[str, bytes], package: SkillPackage
    ) -> dict[str, dict[str, Any]]:
        resources = {item.path: item for item in package.resources}
        result: dict[str, dict[str, Any]] = {}
        for path, content in files.items():
            resource = resources[path]
            blob = await self.session.get(SkillBlobRecord, resource.digest)
            if blob is None:
                self.session.add(
                    SkillBlobRecord(
                        digest=resource.digest,
                        content=content,
                        size_bytes=len(content),
                        media_type=resource.media_type,
                    )
                )
            result[path] = resource.model_dump(mode="json")
        await self.session.flush()
        return result

    @staticmethod
    def _report(package: SkillPackage) -> dict[str, Any]:
        return {
            "valid": not any(
                item.severity in {"error", "critical"} for item in package.diagnostics
            ),
            "publishable": package.publishable,
            "digest": package.digest,
            "diagnostics": [item.model_dump(mode="json") for item in package.diagnostics],
        }


def _frontmatter_name(skill_text: str) -> str:
    import re

    import yaml

    match = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---", skill_text, re.S)
    if not match:
        raise SkillStorageError("SKILL_FRONTMATTER_INVALID", "SKILL.md 缺少 frontmatter。")
    payload = yaml.safe_load(match.group(1))
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        raise SkillStorageError("SKILL_NAME_INVALID", "Skill name 无效。")
    return payload["name"]


def _replace_frontmatter_name(skill_text: str, name: str) -> str:
    import re

    return re.sub(
        r"(?m)^(name:\s*).+$",
        lambda match: f"{match.group(1)}{name}",
        skill_text,
        count=1,
    )
