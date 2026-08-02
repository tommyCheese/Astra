import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.models import (
    Base,
    RunRecord,
    ScheduledJobRunRecord,
    TaskRecord,
)
from app.repositories.schedules import ScheduleRepository
from app.repositories.workspaces import WorkspaceRepository
from app.scheduling.dispatcher import ScheduledRunDispatcher
from app.schemas.agent import AnswerMode, CreateRunResponse
from app.schemas.schedules import ScheduledJobCreate

UTC = timezone.utc


@pytest.mark.asyncio
async def test_dispatcher_reuses_target_conversation_workspace(tmp_path, monkeypatch):
    database_path = tmp_path / "scheduled-dispatch.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        target = TaskRecord(title="Target", description="Target conversation")
        session.add(target)
        await session.commit()
        target_workspace = await WorkspaceRepository(session).get_or_create(target.id)
        job = await ScheduleRepository(session).create(
            ScheduledJobCreate.model_validate(
                {
                    "name": "Workspace reuse",
                    "target_task_id": target.id,
                    "prompt": "Create a report file",
                    "schedule": {"type": "interval", "interval_seconds": 600},
                    "timezone": "UTC",
                    "execution": {"permission_bundle": {"token": "test"}},
                }
            ),
            now=datetime(2026, 8, 2, 0, 0, tzinfo=UTC),
        )
        schedule_run = await ScheduleRepository(session).manual_trigger(
            job,
            now=datetime(2026, 8, 2, 0, 1, tzinfo=UTC),
        )

    captured_task_ids: list[str | None] = []

    async def fake_create_run(payload, session, settings):
        captured_task_ids.append(payload.task_id)
        run = RunRecord(
            task_id=payload.task_id,
            status="completed",
            answer_mode=payload.answer_mode.value,
            execution_profile={},
            model_policy={},
            summary="Created report.txt",
        )
        session.add(run)
        await session.commit()
        return (
            CreateRunResponse(
                task_id=run.task_id,
                run_id=run.id,
                status=run.status,
                answer_mode=AnswerMode.standard,
            ),
            settings,
        )

    async def finished_engine():
        await asyncio.sleep(0)

    def fake_schedule_run(run_id, settings):
        return asyncio.create_task(finished_engine())

    monkeypatch.setattr("app.scheduling.dispatcher._create_run", fake_create_run)
    monkeypatch.setattr("app.scheduling.dispatcher._schedule_run", fake_schedule_run)

    dispatched = await ScheduledRunDispatcher(
        Settings(), session_factory
    ).dispatch(schedule_run.id)
    await asyncio.sleep(0.05)

    async with session_factory() as session:
        stored_schedule_run = await session.get(ScheduledJobRunRecord, dispatched.id)
        assert stored_schedule_run is not None
        run = await session.get(RunRecord, stored_schedule_run.run_id)
        assert run is not None
        reused_workspace = await WorkspaceRepository(session).get_or_create(target.id)

        assert captured_task_ids == [target.id]
        assert run.task_id == target.id
        assert stored_schedule_run.task_id == target.id
        assert reused_workspace.id == target_workspace.id
        assert run.execution_profile["trigger"]["workspace_id"] == target_workspace.id
        assert stored_schedule_run.status == "completed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatcher_blocks_invalid_unattended_permissions(tmp_path):
    database_path = tmp_path / "scheduled-permission.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        target = TaskRecord(title="Target", description="Target conversation")
        session.add(target)
        await session.commit()
        job = await ScheduleRepository(session).create(
            ScheduledJobCreate.model_validate(
                {
                    "name": "Invalid permission",
                    "target_task_id": target.id,
                    "prompt": "Create a report file",
                    "schedule": {"type": "interval", "interval_seconds": 600},
                    "timezone": "UTC",
                    "execution": {"permission_bundle": {"token": "invalid"}},
                }
            )
        )
        schedule_run = await ScheduleRepository(session).manual_trigger(job)

    dispatched = await ScheduledRunDispatcher(
        Settings(permission_bundle_signing_secret="test-secret"),
        session_factory,
    ).dispatch(schedule_run.id)

    assert dispatched.status == "blocked"
    assert dispatched.run_id is None
    assert dispatched.outcome["error"]["code"] in {
        "PERMISSION_BUNDLE_INVALID",
        "SCHEDULE_RUN_BLOCKED",
    }

    await engine.dispose()
