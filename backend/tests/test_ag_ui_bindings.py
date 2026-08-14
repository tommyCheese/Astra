import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.core.errors import AstraResourceNotFoundError, AstraStateConflictError
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.metadata import metadata
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.ag_ui_bindings import (
    AgUiBindingRepository,
    InterruptBindingCreate,
    RunBindingCreate,
)


async def records(session):
    task = TaskRecord(title="AG-UI", description="AG-UI", created_by="principal-1")
    session.add(task)
    await session.flush()
    run = RunRecord(task_id=task.id)
    session.add(run)
    await session.flush()
    return task, run


def run_command(task_id: str, run_id: str, *, fingerprint: str = "a" * 64) -> RunBindingCreate:
    return RunBindingCreate(
        principal_id="principal-1",
        thread_id=task_id,
        protocol_run_id="protocol-run-1",
        internal_task_id=task_id,
        internal_run_id=run_id,
        profile_version="astra-ag-ui-v1",
        input_fingerprint=fingerprint,
    )


async def test_run_binding_is_idempotent_and_hides_other_principals(session) -> None:
    task, run = await records(session)
    repository = AgUiBindingRepository(session)
    created, is_new = await repository.create_run_binding(run_command(task.id, run.id))
    duplicate, duplicate_is_new = await repository.create_run_binding(run_command(task.id, run.id))
    assert is_new is True
    assert duplicate_is_new is False
    assert duplicate.id == created.id

    with pytest.raises(AstraStateConflictError) as conflict:
        await repository.create_run_binding(run_command(task.id, run.id, fingerprint="b" * 64))
    assert conflict.value.payload.code == "AG_UI_RUN_CONFLICT"
    with pytest.raises(AstraResourceNotFoundError):
        await repository.require_run_binding("principal-2", task.id, "protocol-run-1")


async def test_interrupt_consumption_is_versioned_expiring_and_idempotent(session) -> None:
    task, run = await records(session)
    repository = AgUiBindingRepository(session)
    binding, _ = await repository.create_run_binding(run_command(task.id, run.id))
    interrupt, is_new = await repository.create_interrupt(
        InterruptBindingCreate(
            interrupt_id="interrupt-1",
            run_binding_id=binding.id,
            internal_run_id=run.id,
            waiting_kind="input_required",
            response_schema={"type": "string"},
            server_binding={"continuation_token": "server-only"},
        )
    )
    duplicate, duplicate_is_new = await repository.create_interrupt(
        InterruptBindingCreate(
            interrupt_id="interrupt-1",
            run_binding_id=binding.id,
            internal_run_id=run.id,
            waiting_kind="input_required",
            response_schema={"type": "string"},
            server_binding={"continuation_token": "server-only"},
        )
    )
    assert is_new is True and duplicate_is_new is False and duplicate.id == interrupt.id

    consumed, changed = await repository.consume_interrupt(
        interrupt_id="interrupt-1",
        run_binding_id=binding.id,
        expected_version=1,
        outcome={"status": "resolved", "payload": "yes"},
    )
    replayed, replay_changed = await repository.consume_interrupt(
        interrupt_id="interrupt-1",
        run_binding_id=binding.id,
        expected_version=1,
        outcome={"status": "resolved", "payload": "yes"},
    )
    assert changed is True and replay_changed is False
    assert replayed.id == consumed.id and consumed.version == 2
    with pytest.raises(AstraStateConflictError):
        await repository.consume_interrupt(
            interrupt_id="interrupt-1",
            run_binding_id=binding.id,
            expected_version=1,
            outcome={"status": "cancelled"},
        )

    expired, _ = await repository.create_interrupt(
        InterruptBindingCreate(
            interrupt_id="interrupt-expired",
            run_binding_id=binding.id,
            internal_run_id=run.id,
            waiting_kind="confirmation",
            response_schema={"type": "boolean"},
            server_binding={},
            expires_at=utc_now() - timedelta(seconds=1),
        )
    )
    with pytest.raises(AstraStateConflictError) as error:
        await repository.consume_interrupt(
            interrupt_id=expired.interrupt_id,
            run_binding_id=binding.id,
            expected_version=1,
            outcome={"status": "resolved", "payload": True},
        )
    assert error.value.payload.code == "AG_UI_INTERRUPT_EXPIRED"


async def test_bindings_survive_restart_and_concurrent_resolution(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ag-ui.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        task, run = await records(session)
        repository = AgUiBindingRepository(session)
        binding, _ = await repository.create_run_binding(run_command(task.id, run.id))
        await repository.create_interrupt(
            InterruptBindingCreate(
                interrupt_id="interrupt-race",
                run_binding_id=binding.id,
                internal_run_id=run.id,
                waiting_kind="confirmation",
                response_schema={"type": "boolean"},
                server_binding={},
            )
        )
        await session.commit()
        binding_id = binding.id
        task_id = task.id

    async with sessions() as restarted:
        recovered = await AgUiBindingRepository(restarted).require_run_binding(
            "principal-1", task_id, "protocol-run-1"
        )
        assert recovered.id == binding_id

    ready = asyncio.Event()
    arrivals = 0

    async def resolve_once():
        nonlocal arrivals
        async with sessions() as session:
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await ready.wait()
            result = await AgUiBindingRepository(session).consume_interrupt(
                interrupt_id="interrupt-race",
                run_binding_id=binding_id,
                expected_version=1,
                outcome={"status": "resolved", "payload": True},
            )
            await session.commit()
            return result[1]

    assert sorted(await asyncio.gather(resolve_once(), resolve_once())) == [False, True]
    await engine.dispose()
