from __future__ import annotations

import base64
import difflib
import html
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_profile import load_agent_profile
from app.agent_runtime.reasoning import RunProfileResolver, compile_subagent_policy
from app.api.skill_diff import skill_git_diff as _git_diff
from app.api.skill_metrics import build_skill_metrics
from app.api.skill_views import (
    skill_detail_view as _detail,
)
from app.api.skill_views import (
    skill_diagnostics as _diagnostics,
)
from app.api.skill_views import (
    skill_file_view as _file_view,
)
from app.api.skill_views import (
    skill_revision_file_view as _revision_file_view,
)
from app.api.skill_views import (
    skill_revision_view as _revision_view,
)
from app.api.skill_views import (
    skill_summary_view as _summary,
)
from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.models.runs import RunEventRecord
from app.db.models.skills import (
    RunSkillSnapshotRecord,
    SkillAuditRecord,
)
from app.db.session import get_session
from app.model_clients.reasoning import normalize_model_thinking
from app.platform.http.dependencies import ApplicationServices, get_application_container
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.api_views import CreateRunResponse
from app.schemas.agent.run_policy import RequestedReasoningPolicy
from app.schemas.agent.types import PlanExecution
from app.schemas.skills import (
    RunSkillsView,
    SkillCatalogView,
    SkillCloneRequest,
    SkillCreateRequest,
    SkillDetailView,
    SkillDiffView,
    SkillDraftFilesView,
    SkillDraftUpdateRequest,
    SkillFileContentView,
    SkillImportRequest,
    SkillPublishRequest,
    SkillRevisionDetailView,
    SkillRevisionDiffView,
    SkillRevisionView,
    SkillRevokeRequest,
    SkillStateRequest,
    SkillSummaryView,
    SkillTestRunRequest,
    SkillValidationView,
)
from app.skills.activation import SkillActivationService
from app.skills.catalog import SkillCatalogBuilder
from app.skills.errors import SkillStorageError
from app.skills.packages import SkillPackageError, normalize_skill_path
from app.skills.storage import SkillService

router = APIRouter(prefix="/api", tags=["skills"])


def _raise_skill_error(exc: Exception) -> None:
    if isinstance(exc, SkillPackageError):
        raise ValidationError(
            "SKILL_PACKAGE_INVALID",
            "Skill 包校验失败。",
            {"diagnostics": [item.model_dump(mode="json") for item in exc.diagnostics]},
        ) from exc
    if isinstance(exc, SkillStorageError):
        if exc.code in {"SKILL_NOT_FOUND", "SKILL_REVISION_NOT_FOUND", "SKILL_FILE_NOT_FOUND"}:
            raise ResourceError(exc.code, str(exc)) from exc
        if exc.code in {
            "SKILL_DRAFT_STALE",
            "SKILL_IDENTITY_CONFLICT",
            "SKILL_FILE_CONFLICT",
            "SKILL_BUILTIN_READONLY",
        }:
            raise StateError(exc.code, str(exc), exc.details) from exc
        raise ValidationError(exc.code, str(exc), exc.details) from exc
    raise exc


@router.get("/skills", response_model=list[SkillSummaryView])
async def list_skills(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[SkillSummaryView]:
    records = await SkillService(session, settings).list_skills()
    return [await _summary(session, item) for item in records]


@router.post("/skills", response_model=SkillDetailView)
async def create_skill(
    payload: SkillCreateRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDetailView:
    try:
        skill = await SkillService(session, settings).create_custom(
            payload.name, payload.description
        )
        await session.commit()
        return await _detail(session, settings, skill)
    except (SkillPackageError, SkillStorageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.post("/skills/import", response_model=SkillDetailView)
async def import_skill(
    payload: SkillImportRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDetailView:
    try:
        archive = base64.b64decode(payload.content_base64, validate=True)
    except ValueError as exc:
        raise ValidationError("SKILL_ARCHIVE_ENCODING_INVALID", "Skill 压缩包编码无效。") from exc
    try:
        skill = await SkillService(session, settings).import_zip(archive, filename=payload.filename)
        await session.commit()
        return await _detail(session, settings, skill)
    except (SkillPackageError, SkillStorageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.get("/skills/catalog", response_model=SkillCatalogView)
async def get_skill_catalog(
    goal: str = Query(default="", max_length=4000),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillCatalogView:
    catalog = await SkillCatalogBuilder(
        session, metadata_chars=settings.skills_catalog_metadata_chars
    ).build(goal=goal)
    return SkillCatalogView(
        digest=catalog.digest, truncated=catalog.truncated, skills=catalog.metadata()
    )


@router.get("/skills/{skill_id}", response_model=SkillDetailView)
async def get_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDetailView:
    try:
        skill = await SkillService(session, settings).require_skill(skill_id)
        return await _detail(session, settings, skill)
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.post("/skills/{skill_id}/clone", response_model=SkillDetailView)
async def clone_skill(
    skill_id: str,
    payload: SkillCloneRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDetailView:
    try:
        skill = await SkillService(session, settings).clone_builtin(skill_id, payload.name)
        await session.commit()
        return await _detail(session, settings, skill)
    except (SkillPackageError, SkillStorageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.get("/skills/{skill_id}/draft/files", response_model=SkillDraftFilesView)
async def list_skill_files(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDraftFilesView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        files = await service.draft_files(skill)
        return SkillDraftFilesView(
            skill_id=skill.id,
            revision_token=(
                skill.draft.revision_token if skill.draft else str(skill.active_revision_id or "")
            ),
            readonly=skill.origin == "builtin",
            files=[_file_view(skill, item) for _, item in sorted(files.items())],
            diagnostics=_diagnostics(skill.draft.validation_report if skill.draft else {}),
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.get("/skills/{skill_id}/draft/file", response_model=SkillFileContentView)
async def read_skill_file(
    skill_id: str,
    path: Annotated[str, Query(min_length=1)],
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillFileContentView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        normalized = normalize_skill_path(path)
        files = await service.draft_files(skill)
        item = files.get(normalized)
        if item is None:
            raise SkillStorageError("SKILL_FILE_NOT_FOUND", "找不到 Skill 文件。")
        content = await service.read_file(skill, normalized)
        return SkillFileContentView(
            path=normalized,
            uri=_file_view(skill, item).uri,
            media_type=item["media_type"],
            digest=item["digest"],
            text=item["text"],
            content=content.decode("utf-8") if item["text"] else None,
            content_base64=None if item["text"] else base64.b64encode(content).decode(),
            readonly=skill.origin == "builtin",
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.put("/skills/{skill_id}/draft/files", response_model=SkillDraftFilesView)
async def update_skill_files(
    skill_id: str,
    payload: SkillDraftUpdateRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDraftFilesView:
    try:
        service = SkillService(session, settings)
        draft = await service.update_draft(
            skill_id,
            payload.revision_token,
            [item.model_dump(exclude_none=True) for item in payload.operations],
        )
        await session.commit()
        skill = await service.require_skill(skill_id)
        files = await service.draft_files(skill)
        return SkillDraftFilesView(
            skill_id=skill.id,
            revision_token=draft.revision_token,
            readonly=False,
            files=[_file_view(skill, item) for _, item in sorted(files.items())],
            diagnostics=_diagnostics(draft.validation_report),
        )
    except (SkillPackageError, SkillStorageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.post("/skills/{skill_id}/validate", response_model=SkillValidationView)
async def validate_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillValidationView:
    try:
        service = SkillService(session, settings)
        report = await service.validate_draft(await service.require_skill(skill_id))
        await session.commit()
        return SkillValidationView.model_validate(report)
    except (SkillPackageError, SkillStorageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.post("/skills/{skill_id}/publish", response_model=SkillRevisionView)
async def publish_skill(
    skill_id: str,
    payload: SkillPublishRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillRevisionView:
    try:
        revision = await SkillService(session, settings).publish(skill_id, payload.revision_token)
        await session.commit()
        result = await _revision_view(session, revision.id)
        assert result is not None
        return result
    except (SkillPackageError, SkillStorageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.get("/skills/{skill_id}/revisions", response_model=list[SkillRevisionView])
async def list_skill_revisions(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[SkillRevisionView]:
    try:
        service = SkillService(session, settings)
        await service.require_skill(skill_id)
        revisions = await service.revisions(skill_id)
        return [
            SkillRevisionView(
                id=item.id,
                version=item.version,
                digest=item.digest,
                published_at=item.published_at.isoformat() if item.published_at else None,
                revoked_at=item.revoked_at.isoformat() if item.revoked_at else None,
                test_only=item.test_only,
                diagnostics=_diagnostics(item.validation_report),
            )
            for item in revisions
        ]
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.get(
    "/skills/{skill_id}/revisions/{revision_id}",
    response_model=SkillRevisionDetailView,
)
async def get_skill_revision(
    skill_id: str,
    revision_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillRevisionDetailView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        revision = await service.require_revision(skill_id, revision_id)
        files = revision.manifest.get("files", {}) or {}
        return SkillRevisionDetailView(
            id=revision.id,
            version=revision.version,
            digest=revision.digest,
            published_at=revision.published_at.isoformat() if revision.published_at else None,
            revoked_at=revision.revoked_at.isoformat() if revision.revoked_at else None,
            test_only=revision.test_only,
            diagnostics=_diagnostics(revision.validation_report),
            files=[_revision_file_view(skill, revision, item) for _, item in sorted(files.items())],
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.get(
    "/skills/{skill_id}/revisions/{revision_id}/file",
    response_model=SkillFileContentView,
)
async def read_skill_revision_file(
    skill_id: str,
    revision_id: str,
    path: Annotated[str, Query(min_length=1)],
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillFileContentView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        revision = await service.require_revision(skill_id, revision_id)
        normalized = normalize_skill_path(path)
        item = (revision.manifest.get("files", {}) or {}).get(normalized)
        if item is None:
            raise SkillStorageError("SKILL_FILE_NOT_FOUND", "找不到 Skill 历史文件。")
        content = (
            await service.materialize_manifest(
                {
                    "files": {normalized: item},
                }
            )
        )[normalized]
        view = _revision_file_view(skill, revision, item)
        return SkillFileContentView(
            path=normalized,
            uri=view.uri,
            media_type=item["media_type"],
            digest=item["digest"],
            text=item["text"],
            content=content.decode("utf-8") if item["text"] else None,
            content_base64=None if item["text"] else base64.b64encode(content).decode(),
            readonly=True,
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.get(
    "/skills/{skill_id}/revisions/{revision_id}/diff",
    response_model=SkillRevisionDiffView,
)
async def diff_skill_revision(
    skill_id: str,
    revision_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillRevisionDiffView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        base_revision = await service.require_revision(skill_id, revision_id)
        target_revision = await service.require_active_revision(skill)
        before_files = await service.materialize_manifest(base_revision.manifest)
        after_files = await service.materialize_manifest(target_revision.manifest)
        patch, files = _git_diff(before_files, after_files)
        return SkillRevisionDiffView(
            skill_id=skill.id,
            base_revision_id=base_revision.id,
            target_revision_id=target_revision.id,
            base_version=base_revision.version,
            target_version=target_revision.version,
            patch=patch,
            files=files,
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.post(
    "/skills/{skill_id}/revisions/{revision_id}/revoke",
    response_model=SkillRevisionView,
)
async def revoke_skill_revision(
    skill_id: str,
    revision_id: str,
    payload: SkillRevokeRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillRevisionView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        revision = await service.require_revision(skill_id, revision_id)
        if revision.revoked_at is None:
            from app.db.model_base import utc_now

            revision.revoked_at = utc_now()
            if skill.active_revision_id == revision.id:
                skill.enabled = False
        revision.validation_report = {
            **(revision.validation_report or {}),
            "revocation_reason": payload.reason,
        }
        service._audit(
            skill.id,
            "skill.revision_revoked",
            {"revision_id": revision.id, "digest": revision.digest, "reason": payload.reason},
        )
        await session.commit()
        result = await _revision_view(session, revision.id)
        assert result is not None
        return result
    except SkillStorageError as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.post(
    "/skills/{skill_id}/revisions/{revision_id}/restore",
    response_model=SkillDraftFilesView,
)
async def restore_skill_revision(
    skill_id: str,
    revision_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDraftFilesView:
    try:
        service = SkillService(session, settings)
        draft = await service.restore(skill_id, revision_id)
        await session.commit()
        skill = await service.require_skill(skill_id)
        files = await service.draft_files(skill)
        return SkillDraftFilesView(
            skill_id=skill.id,
            revision_token=draft.revision_token,
            readonly=False,
            files=[_file_view(skill, item) for _, item in sorted(files.items())],
            diagnostics=_diagnostics(draft.validation_report),
        )
    except SkillStorageError as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.get("/skills/{skill_id}/diff", response_model=SkillDiffView)
async def diff_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillDiffView:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        draft_files = (
            await service.materialize_manifest({"files": await service.draft_files(skill)})
            if skill.draft
            else {}
        )
        active_files: dict[str, bytes] = {}
        if skill.active_revision_id:
            active_files = await service.materialize_manifest(
                (await service.require_active_revision(skill)).manifest
            )
        paths = sorted(set(draft_files) | set(active_files))
        changes = []
        for path in paths:
            before = active_files.get(path)
            after = draft_files.get(path)
            status = (
                "added"
                if before is None
                else "removed"
                if after is None
                else ("unchanged" if before == after else "modified")
            )
            patch = None
            if status == "modified":
                try:
                    patch = "".join(
                        difflib.unified_diff(
                            before.decode("utf-8").splitlines(keepends=True),
                            after.decode("utf-8").splitlines(keepends=True),
                            fromfile=f"published/{path}",
                            tofile=f"draft/{path}",
                        )
                    )
                except UnicodeDecodeError:
                    patch = None
            changes.append({"path": path, "status": status, "patch": patch})
        return SkillDiffView(
            skill_id=skill.id,
            draft_revision_token=skill.draft.revision_token if skill.draft else None,
            active_revision_id=skill.active_revision_id,
            files=changes,
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.get("/skills/{skill_id}/preview")
async def preview_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        content = (await service.read_file(skill, "SKILL.md")).decode("utf-8")
        body = content.split("---", 2)[-1].strip()
        sanitized = re.sub(r"<[^>]+>", "", body)
        sanitized = re.sub(r"!\[[^\]]*]\([^)]*\)", "[remote image omitted]", sanitized)
        return {"markdown": sanitized, "text": html.unescape(sanitized)}
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.get("/skills/{skill_id}/export")
async def export_skill(
    skill_id: str,
    revision_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        service = SkillService(session, settings)
        skill = await service.require_skill(skill_id)
        archive = await service.export_zip(skill, revision_id=revision_id)
        return Response(
            archive,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{skill.name}.zip"'},
        )
    except SkillStorageError as exc:
        _raise_skill_error(exc)


@router.put("/skills/{skill_id}/state", response_model=SkillSummaryView)
async def update_skill_state(
    skill_id: str,
    payload: SkillStateRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SkillSummaryView:
    try:
        skill = await SkillService(session, settings).set_enabled(skill_id, payload.enabled)
        await session.commit()
        return await _summary(session, skill)
    except SkillStorageError as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.delete("/skills/{skill_id}", status_code=204)
async def delete_skill(
    skill_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        await SkillService(session, settings).remove(skill_id)
        await session.commit()
        return Response(status_code=204)
    except SkillStorageError as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.post(
    "/skills/{skill_id}/test-runs",
    response_model=CreateRunResponse,
)
async def create_skill_test_run(
    skill_id: str,
    payload: SkillTestRunRequest,
    container: ApplicationServices = Depends(get_application_container),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CreateRunResponse:
    service = SkillService(session, settings)
    try:
        test_revision = await service.create_test_revision(skill_id, payload.revision_token)
        profile = RunProfileResolver().resolve(
            payload.answer_mode,
            RequestedReasoningPolicy(),
            plan_execution=(PlanExecution.auto if payload.answer_mode.value == "trusted" else None),
            subagent_policy=compile_subagent_policy(settings),
        )
        thinking = normalize_model_thinking(
            provider=settings.model_provider,
            model=settings.model_name,
            selection=None,
        )
        run = await RunUnitOfWork(session).create_task_run(
            payload.goal.strip(),
            {
                **settings.model_policy,
                "thinking": thinking.model_dump(mode="json"),
            },
            reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
            answer_mode=profile.answer_mode.value,
            execution_profile=profile.model_dump(mode="json"),
            agent_profile_snapshot=load_agent_profile().snapshot(),
            commit=False,
        )
        catalog_builder = SkillCatalogBuilder(
            session, metadata_chars=settings.skills_catalog_metadata_chars
        )
        catalog = await catalog_builder.build(
            goal=payload.goal,
            explicit_identities=[],
            revision_overrides=[test_revision],
            runtime_capabilities={
                name
                for name, enabled in {
                    "web_search": settings.tool_web_search_enabled,
                    "web_fetch": settings.tool_web_fetch_enabled,
                    "chart_render": settings.tool_chart_render_enabled,
                    "bash_execute": settings.tool_bash_execute_enabled,
                    "sandbox": settings.sandbox_enabled,
                }.items()
                if enabled
            },
        )
        await catalog_builder.freeze(
            run.id,
            profile.answer_mode.value,
            catalog,
            draft_test=True,
        )
        skill = await service.require_skill(skill_id)
        await SkillActivationService(
            session,
            max_active=settings.skills_max_active,
            max_resource_bytes=settings.skills_max_resource_bytes_per_run,
        ).activate(
            run.id,
            f"{skill.origin}:{skill.name}",
            initiator="draft_test",
            reason="Draft test",
        )
        await session.commit()

        container.run_dispatcher.start(run.id, settings)
        return CreateRunResponse(
            task_id=run.task_id,
            run_id=run.id,
            status=run.status,
            answer_mode=profile.answer_mode,
        )
    except (SkillStorageError, SkillPackageError) as exc:
        await session.rollback()
        _raise_skill_error(exc)


@router.get("/runs/{run_id}/skills", response_model=RunSkillsView)
async def get_run_skills(
    run_id: str,
    session: AsyncSession = Depends(get_session),
) -> RunSkillsView:
    snapshot = await session.scalar(
        select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run_id)
    )
    if snapshot is None:
        raise ResourceError("RUN_SKILLS_NOT_FOUND", "该 Run 没有 Skill 快照。")
    events = list(
        (
            await session.scalars(
                select(RunEventRecord)
                .where(
                    RunEventRecord.run_id == run_id,
                    RunEventRecord.type.in_(["skill.attributed_action", "skill.plan_bound"]),
                )
                .order_by(RunEventRecord.id)
            )
        ).all()
    )
    return RunSkillsView(
        run_id=run_id,
        catalog_digest=snapshot.catalog_digest,
        answer_mode=snapshot.answer_mode,
        draft_test=snapshot.draft_test,
        catalog=snapshot.catalog,
        activations=snapshot.activations,
        resource_reads=snapshot.resource_reads,
        attributed_actions=[
            event.payload for event in events if event.type == "skill.attributed_action"
        ],
        plan_bindings=[event.payload for event in events if event.type == "skill.plan_bound"],
    )


@router.get("/skills/{skill_id}/audit")
async def get_skill_audit(
    skill_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, Any]]:
    await SkillService(session, settings).require_skill(skill_id)
    events = list(
        (
            await session.scalars(
                select(SkillAuditRecord)
                .where(SkillAuditRecord.skill_id == skill_id)
                .order_by(SkillAuditRecord.id.desc())
                .limit(limit)
            )
        ).all()
    )
    return [
        {
            "id": event.id,
            "type": event.type,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]


@router.get("/skills/metrics/summary")
async def get_skill_metrics(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await build_skill_metrics(session)
