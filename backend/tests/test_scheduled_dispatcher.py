import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.run_management.contracts import PreparedRunExecution
from app.application.run_management.dispatcher import InProcessRunDispatcher
from app.application.scheduling.dispatcher import ScheduledRunDispatcher
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import AstraInputValidationError
from app.common.schemas.agent.api_views import CreateRunResponse
from app.common.schemas.agent.types import AnswerMode
from app.common.schemas.schedules import ScheduledJobCreate
from app.infrastructure.db.model_base import AstraOrmRecordBase
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.scheduling import ScheduledJobRecord, ScheduledJobRunRecord
from app.infrastructure.repositories.schedules import ScheduleRepository
from app.infrastructure.repositories.workspaces import WorkspaceRepository

UTC = timezone.utc


async def _finished_run(_run_id, _settings):
    await asyncio.sleep(0)


def _run_dispatcher():
    return InProcessRunDispatcher(_finished_run)


@pytest.mark.asyncio
async def test_dispatcher_reuses_target_conversation_workspace(tmp_path, monkeypatch):
    database_path = tmp_path / "scheduled-dispatch.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
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
    captured_answer_modes: list[AnswerMode] = []

    async def fake_prepare(_service, payload, *, commit=True):
        captured_task_ids.append(payload.task_id)
        captured_answer_modes.append(payload.answer_mode)
        run = RunRecord(
            task_id=payload.task_id,
            status="completed",
            answer_mode=payload.answer_mode.value,
            execution_profile={},
            model_policy={},
            summary="Created report.txt",
        )
        _service.session.add(run)
        if commit:
            await _service.session.commit()
        else:
            await _service.session.flush()
        return PreparedRunExecution(
            CreateRunResponse(
                task_id=run.task_id,
                run_id=run.id,
                status=run.status,
                answer_mode=AnswerMode.standard,
            ),
            AstraRuntimeSettings(),
        )

    monkeypatch.setattr("app.application.scheduling.dispatcher.RunApplicationService.prepare", fake_prepare)

    dispatched = await ScheduledRunDispatcher(
        AstraRuntimeSettings(), session_factory, _run_dispatcher()
    ).dispatch(schedule_run.id)
    await asyncio.sleep(0.05)

    async with session_factory() as session:
        stored_schedule_run = await session.get(ScheduledJobRunRecord, dispatched.id)
        assert stored_schedule_run is not None
        run = await session.get(RunRecord, stored_schedule_run.run_id)
        assert run is not None
        reused_workspace = await WorkspaceRepository(session).get_or_create(target.id)

        assert captured_task_ids == [target.id]
        assert captured_answer_modes == [AnswerMode.standard]
        assert run.task_id == target.id
        assert stored_schedule_run.task_id == target.id
        assert reused_workspace.id == target_workspace.id
        assert run.execution_profile["trigger"]["workspace_id"] == target_workspace.id
        assert stored_schedule_run.status == "completed"

    await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_defers_while_target_conversation_is_busy(tmp_path):
    database_path = tmp_path / "heartbeat-busy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        target = TaskRecord(title="Target", description="Target conversation")
        session.add(target)
        await session.commit()
        busy = RunRecord(task_id=target.id, status="executing", model_policy={})
        session.add(busy)
        job = ScheduledJobRecord(
            name="Heartbeat",
            kind="heartbeat",
            system_key="heartbeat:global",
            system_managed=True,
            target_task_id=target.id,
            prompt="Check",
            schedule_type="interval",
            schedule={"type": "interval", "interval_seconds": 600},
            timezone="UTC",
            enabled=True,
            execution={"permission_bundle": {"token": "invalid"}},
            heartbeat={},
        )
        session.add(job)
        await session.commit()
        schedule_run = await ScheduleRepository(session).manual_trigger(job)

    result = await ScheduledRunDispatcher(AstraRuntimeSettings(), session_factory, _run_dispatcher()).dispatch(
        schedule_run.id
    )
    assert result.status == "deferred_busy"
    assert result.run_id is None
    assert result.outcome == {"reason": "target_conversation_busy"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_ok_is_recorded_silently_and_hidden_from_chat(tmp_path):
    database_path = tmp_path / "heartbeat-silent.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        target = TaskRecord(title="Target", description="Target conversation")
        session.add(target)
        await session.commit()
        job = ScheduledJobRecord(
            name="Heartbeat",
            kind="heartbeat",
            system_key="heartbeat:global",
            system_managed=True,
            target_task_id=target.id,
            prompt="Check",
            schedule_type="interval",
            schedule={"type": "interval", "interval_seconds": 600},
            timezone="UTC",
            execution={},
            heartbeat={},
        )
        session.add(job)
        await session.flush()
        run = RunRecord(
            task_id=target.id,
            status="completed",
            model_policy={},
            execution_profile={"trigger": {"type": "heartbeat"}},
            summary="HEARTBEAT_OK",
        )
        session.add(run)
        await session.flush()
        schedule_run = ScheduledJobRunRecord(
            job_id=job.id,
            scheduled_for=datetime.now(UTC),
            idempotency_key="heartbeat:silent",
            status="running",
            run_id=run.id,
            task_id=target.id,
        )
        session.add(schedule_run)
        await session.commit()
        schedule_run_id, run_id = schedule_run.id, run.id

    await ScheduledRunDispatcher(AstraRuntimeSettings(), session_factory, _run_dispatcher())._finalize(
        schedule_run_id, run_id
    )
    async with session_factory() as session:
        stored = await session.get(ScheduledJobRunRecord, schedule_run_id)
        stored_run = await session.get(RunRecord, run_id)
        assert stored.status == "silent_ok"
        assert stored_run.execution_profile["trigger"]["delivery"] == "silent"
        from app.infrastructure.repositories.conversations import ConversationRepository

        conversation = await ConversationRepository(session).get(target.id, detailed=True)
        assert conversation.runs == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatcher_blocks_invalid_unattended_permissions(tmp_path):
    database_path = tmp_path / "scheduled-permission.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
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
        AstraRuntimeSettings(permission_bundle_signing_secret="test-secret"),
        session_factory,
        _run_dispatcher(),
    ).dispatch(schedule_run.id)

    assert dispatched.status == "blocked"
    assert dispatched.run_id is None
    assert dispatched.outcome["error"]["code"] in {
        "PERMISSION_BUNDLE_INVALID",
        "SCHEDULE_RUN_BLOCKED",
    }

    await engine.dispose()


@pytest.mark.asyncio
async def test_dispatcher_rolls_back_partial_run_when_creation_is_blocked(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        target = TaskRecord(title="Target", description="Target conversation")
        session.add(target)
        await session.commit()
        job = await ScheduleRepository(session).create(
            ScheduledJobCreate.model_validate(
                {
                    "name": "Atomic failure",
                    "target_task_id": target.id,
                    "prompt": "Fail after flush",
                    "schedule": {"type": "interval", "interval_seconds": 600},
                    "timezone": "UTC",
                    "execution": {"permission_bundle": {"token": "test"}},
                }
            )
        )
        schedule_run = await ScheduleRepository(session).manual_trigger(job)

    async def fail_after_flush(service, payload, *, commit=True):
        service.session.add(RunRecord(task_id=payload.task_id, status="created", model_policy={}))
        await service.session.flush()
        raise AstraInputValidationError("TEST_BLOCKED", "blocked")

    monkeypatch.setattr("app.application.scheduling.dispatcher.RunApplicationService.prepare", fail_after_flush)
    result = await ScheduledRunDispatcher(AstraRuntimeSettings(), session_factory, _run_dispatcher()).dispatch(
        schedule_run.id
    )

    async with session_factory() as session:
        assert list((await session.scalars(select(RunRecord))).all()) == []
        stored = await session.get(ScheduledJobRunRecord, result.id)
        assert stored.status == "blocked"
        assert stored.outcome["error"]["code"] == "TEST_BLOCKED"
    await engine.dispose()
