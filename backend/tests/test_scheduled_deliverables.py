from datetime import datetime, timezone

import pytest

from app.api.schedules import list_schedule_deliverables
from app.db.models import RunRecord, TaskRecord
from app.repositories.schedules import ScheduleRepository
from app.repositories.workspaces import WorkspaceRepository
from app.schemas.schedules import ScheduledJobCreate

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
    assert generated.content_url == (
        f"/api/tasks/{task.id}/workspace/files/{file.id}/content"
    )
