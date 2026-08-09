from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.application.workspaces.artifacts import LocalArtifactStore
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.schemas.schedules import ScheduledJobCreate
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.workspaces import ArtifactRecord
from app.infrastructure.repositories.schedules import ScheduleRepository
from app.infrastructure.repositories.workspaces import WorkspaceRepository
from app.interfaces.api.permissions import library_deliverables
from app.interfaces.api.schedules import list_schedule_deliverables

UTC = timezone.utc


async def _scheduled_run(session, *, summary: str):
    task = TaskRecord(title="Result conversation", description="Scheduled output")
    session.add(task)
    await session.commit()
    job = await ScheduleRepository(session).create(
        ScheduledJobCreate.model_validate(
            {
                "name": "Daily output",
                "target_task_id": task.id,
                "prompt": "Produce output",
                "schedule": {"type": "interval", "interval_seconds": 600},
                "timezone": "UTC",
                "execution": {"permission_bundle": {"token": "test"}},
            }
        )
    )
    run = RunRecord(
        task_id=task.id,
        status="completed",
        answer_mode="standard",
        execution_profile={},
        model_policy={},
        summary=summary,
        result={"summary": summary},
        completed_at=datetime(2026, 8, 2, 1, 0, tzinfo=UTC),
    )
    session.add(run)
    await session.commit()
    schedule_run = await ScheduleRepository(session).manual_trigger(job)
    schedule_run.task_id = task.id
    schedule_run.run_id = run.id
    schedule_run.status = "completed"
    await session.commit()
    return task, job, run, schedule_run


@pytest.mark.asyncio
async def test_simple_output_becomes_a_result_deliverable(session):
    _task, job, run, schedule_run = await _scheduled_run(
        session,
        summary="hello from the scheduled task",
    )

    deliverables = await list_schedule_deliverables(job.id, 100, session)

    assert len(deliverables) == 1
    assert deliverables[0].id == f"result:{schedule_run.id}"
    assert deliverables[0].kind == "result"
    assert deliverables[0].summary == "hello from the scheduled task"
    assert deliverables[0].run_id == run.id


@pytest.mark.asyncio
async def test_generated_workspace_file_becomes_a_file_deliverable(session):
    task, job, run, _schedule_run = await _scheduled_run(
        session,
        summary="Report created",
    )
    workspace_repo = WorkspaceRepository(session)
    workspace = await workspace_repo.get_or_create(task.id)
    file = await workspace_repo.upsert_file(
        workspace.id,
        "reports/daily.txt",
        mime_type="text/plain",
        size_bytes=12,
        security_status="verified",
        deliverable_candidate=True,
    )
    await workspace_repo.record_change(
        workspace_id=workspace.id,
        run_id=run.id,
        relative_path="reports/daily.txt",
        change_kind="created",
        mime_type="text/plain",
        size_bytes=12,
        security_status="verified",
        deliverable_candidate=True,
    )

    deliverables = await list_schedule_deliverables(job.id, 100, session)
    generated = next(item for item in deliverables if item.kind == "file")

    assert generated.title == "daily.txt"
    assert generated.summary == "reports/daily.txt"
    assert generated.content_url == (f"/api/tasks/{task.id}/workspace/files/{file.id}/content")


@pytest.mark.asyncio
async def test_structured_data_and_external_write_become_typed_deliverables(session):
    task, job, run, _schedule_run = await _scheduled_run(
        session,
        summary="Data published",
    )
    workspace_repo = WorkspaceRepository(session)
    workspace = await workspace_repo.get_or_create(task.id)
    await workspace_repo.upsert_file(
        workspace.id,
        "exports/result.json",
        mime_type="application/json",
        size_bytes=24,
        security_status="verified",
        deliverable_candidate=True,
    )
    await workspace_repo.record_change(
        workspace_id=workspace.id,
        run_id=run.id,
        relative_path="exports/result.json",
        change_kind="created",
        mime_type="application/json",
        size_bytes=24,
        security_status="verified",
        deliverable_candidate=True,
    )
    session.add_all(
        [
            ToolCallRecord(
                run_id=run.id,
                tool_name="publish_report",
                tool_version="1",
                input={"destination": "team-dashboard"},
                output={
                    "status": "published",
                    "message": "Dashboard updated",
                    "object_id": "report-42",
                    "url": "https://example.test/reports/42",
                    "secret": "must-not-leak",
                },
                status="succeeded",
                permission="external_write",
                side_effect_level="external_write",
                completed_at=datetime(2026, 8, 2, 1, 1, tzinfo=UTC),
            ),
            ToolCallRecord(
                run_id=run.id,
                tool_name="debug_query",
                tool_version="1",
                input={},
                output={"stdout": "internal debug output"},
                status="succeeded",
                permission="network_read",
                side_effect_level="read_only",
            ),
            ToolCallRecord(
                run_id=run.id,
                tool_name="bash_execute",
                tool_version="1",
                input={"command": "printf hello"},
                output={"stdout": "hello", "exit_code": 0},
                status="succeeded",
                permission="process_execute",
                side_effect_level="external_side_effect",
            ),
        ]
    )
    await session.commit()

    deliverables = await list_schedule_deliverables(job.id, 100, session)
    data = next(item for item in deliverables if item.kind == "data")
    receipt = next(item for item in deliverables if item.kind == "receipt")

    assert data.title == "result.json"
    assert receipt.summary == "Dashboard updated"
    assert receipt.external_url == "https://example.test/reports/42"
    assert receipt.metadata == {
        "tool_name": "publish_report",
        "status": "published",
        "target": "team-dashboard",
        "object_id": "report-42",
    }
    assert "must-not-leak" not in str(receipt.model_dump())
    assert "internal debug output" not in str([item.model_dump() for item in deliverables])
    assert "hello" not in str([item.model_dump() for item in deliverables])


@pytest.mark.asyncio
async def test_library_and_schedule_share_immutable_workspace_deliverables(session, tmp_path):
    task, job, first_run, _schedule_run = await _scheduled_run(
        session,
        summary="First report ready",
    )
    workspace_root = tmp_path / "workspaces"
    artifact_root = tmp_path / "artifacts"
    runtime = WorkspaceRuntimeService(
        WorkspaceRepository(session),
        str(workspace_root),
        max_files=100,
        max_bytes=1024 * 1024,
        max_file_bytes=1024 * 1024,
        artifact_store_path=str(artifact_root),
    )
    workspace_dir = await runtime.prepare(task.id)
    before = runtime.scan(workspace_dir)
    report = workspace_dir / "reports" / "daily.txt"
    report.parent.mkdir(parents=True)
    report.write_text("first version", encoding="utf-8")
    await runtime.capture_changes(
        run_id=first_run.id,
        tool_call_id=None,
        workspace_dir=workspace_dir,
        before=before,
    )

    second_run = RunRecord(
        task_id=task.id,
        status="completed",
        answer_mode="standard",
        execution_profile={},
        model_policy={},
        summary="Second report ready",
        result={"summary": "Second report ready"},
    )
    session.add(second_run)
    await session.commit()
    before = runtime.scan(workspace_dir)
    report.write_text("second version", encoding="utf-8")
    await runtime.capture_changes(
        run_id=second_run.id,
        tool_call_id=None,
        workspace_dir=workspace_dir,
        before=before,
    )

    first_snapshot = await session.scalar(
        select(ArtifactRecord).where(
            ArtifactRecord.run_id == first_run.id,
            ArtifactRecord.type == "workspace_snapshot",
        )
    )
    assert first_snapshot is not None
    assert (
        LocalArtifactStore(str(artifact_root)).resolve(first_snapshot.storage_key).read_text(encoding="utf-8")
        == "first version"
    )

    scheduled = await list_schedule_deliverables(job.id, 100, session)
    library = await library_deliverables(500, session)
    scheduled_by_id = {item.id: item.model_dump() for item in scheduled}
    library_by_id = {item.id: item.model_dump() for item in library}
    assert scheduled_by_id.keys() <= library_by_id.keys()
    assert all(library_by_id[item_id] == item for item_id, item in scheduled_by_id.items())
    snapshot_item = scheduled_by_id[f"artifact:{first_snapshot.id}"]
    assert snapshot_item["metadata"]["source"] == "workspace"
    assert snapshot_item["content_url"] == (f"/api/deliverables/artifacts/{first_snapshot.id}/content")
