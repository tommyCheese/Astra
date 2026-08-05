from datetime import datetime, timedelta, timezone

import pytest

from app.application.permissions.governance import permission_bundle_digest
from app.application.scheduling.commands import AutomationCommandService
from app.common.core.config import Settings
from app.common.core.errors import ValidationError
from app.common.schemas.permissions import PermissionBundle
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord

UTC = timezone.utc


async def conversation_with_permission_bundle(session, *, secret="test-secret"):
    now = datetime.now(UTC)
    task = TaskRecord(
        title="Automation conversation",
        description="Automation conversation",
        status="created",
        preferred_answer_mode="standard",
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.flush()
    bundle = PermissionBundle(
        id="bundle-1",
        version="1",
        allowed_actions=[],
        allowed_resources=[],
        allowed_effect_kinds=[],
        allowed_tool_identities=[],
        expires_at=now + timedelta(days=1),
        digest="",
    )
    bundle = bundle.model_copy(update={"digest": permission_bundle_digest(bundle, secret)})
    session.add(
        RunRecord(
            task_id=task.id,
            status="completed",
            answer_mode="standard",
            execution_profile={
                "interactive": False,
                "permission_bundle": bundle.model_dump(mode="json"),
            },
            model_policy={"provider": "mock", "model": "mock-model"},
            created_at=now,
            updated_at=now,
        )
    )
    await session.commit()
    return task


@pytest.mark.asyncio
async def test_schedule_commands_create_in_conversation_and_manage_globally(session):
    task = await conversation_with_permission_bundle(session)
    service = AutomationCommandService(
        session,
        Settings(permission_bundle_signing_secret="test-secret"),
    )

    _, created = await service.execute_schedule(
        task,
        'create --every 30m --tz Asia/Shanghai --name "摘要" "生成摘要"',
    )
    job = created["job"]
    assert job["name"] == "摘要"
    assert job["version"] == 1
    stored_job = await service.repo.require(job["id"])
    assert stored_job.target_task_id == task.id

    _, listed = await service.execute_schedule(task, "list")
    assert [item["id"] for item in listed["jobs"]] == [job["id"]]

    other_task = await conversation_with_permission_bundle(session)
    other_service = AutomationCommandService(
        session,
        Settings(permission_bundle_signing_secret="test-secret"),
    )
    _, global_list = await other_service.execute_schedule(other_task, "list")
    assert [item["id"] for item in global_list["jobs"]] == [job["id"]]

    _, paused = await other_service.execute_schedule(
        other_task,
        f"pause {job['id']} --version 1",
    )
    assert paused["job"]["enabled"] is False

    _, resumed = await service.execute_schedule(
        task,
        f"resume {job['id']} --version 2",
    )
    assert resumed["job"]["enabled"] is True


@pytest.mark.asyncio
async def test_heartbeat_commands_upsert_stable_system_schedule(session):
    task = await conversation_with_permission_bundle(session)
    service = AutomationCommandService(
        session,
        Settings(
            permission_bundle_signing_secret="test-secret",
            scheduler_heartbeat_min_interval_seconds=300,
        ),
    )

    _, enabled = await service.execute_heartbeat(
        task,
        "on --every 30m --tz Asia/Shanghai --active 09:00-22:00",
    )
    heartbeat_id = enabled["heartbeat"]["id"]
    assert enabled["heartbeat"]["enabled"] is True

    _, updated = await service.execute_heartbeat(
        task,
        "on --every 1h",
    )
    assert updated["heartbeat"]["id"] == heartbeat_id
    assert updated["heartbeat"]["version"] == 2
    assert updated["heartbeat"]["timezone"] == "Asia/Shanghai"
    assert updated["heartbeat"]["heartbeat"]["active_hours"] == {
        "start": "09:00",
        "end": "22:00",
    }

    other_task = await conversation_with_permission_bundle(session)
    _, moved = await service.execute_heartbeat(other_task, "on --every 2h")
    assert moved["heartbeat"]["id"] == heartbeat_id
    stored = await service.heartbeats.get()
    assert stored is not None
    assert stored.system_key == "heartbeat:global"
    assert stored.target_task_id == other_task.id

    _, disabled = await service.execute_heartbeat(task, "off")
    assert disabled["heartbeat"]["enabled"] is False


@pytest.mark.asyncio
async def test_automation_create_fails_closed_without_signed_bundle(session):
    now = datetime.now(UTC)
    task = TaskRecord(
        title="No permission",
        description="No permission",
        status="created",
        preferred_answer_mode="standard",
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.commit()
    service = AutomationCommandService(
        session,
        Settings(permission_bundle_signing_secret="test-secret"),
    )

    with pytest.raises(ValidationError) as error:
        await service.execute_schedule(task, "create --every 30m work")

    assert error.value.payload.code == "AUTOMATION_PERMISSION_BUNDLE_REQUIRED"


@pytest.mark.asyncio
async def test_manual_command_idempotency_returns_same_schedule_run(session):
    task = await conversation_with_permission_bundle(session)
    service = AutomationCommandService(
        session,
        Settings(permission_bundle_signing_secret="test-secret"),
    )
    _, created = await service.execute_schedule(
        task,
        "create --every 30m work",
    )
    job_id = created["job"]["id"]

    _, first = await service.execute_schedule(
        task,
        f"run {job_id} --idempotency-key command-1",
    )
    _, second = await service.execute_schedule(
        task,
        f"run {job_id} --idempotency-key command-1",
    )
    _, another_job = await service.execute_schedule(
        task,
        'create --every 1h "other work"',
    )
    _, other_run = await service.execute_schedule(
        task,
        f"run {another_job['job']['id']} --idempotency-key command-1",
    )

    assert first["schedule_run"]["id"] == second["schedule_run"]["id"]
    assert first["schedule_run"]["id"] != other_run["schedule_run"]["id"]
