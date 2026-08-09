import pytest
from pydantic import ValidationError

from app.application.agent_runtime.contracts import (
    LoopAction,
    LoopState,
    PendingAction,
)
from app.application.agent_runtime.policies.reasoning import resolve_run_profile
from app.application.run_management.projections.run_view import run_payload
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.types import AnswerMode, PlanExecution, RuntimeKind
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.runtime.standard_checkpoint import (
    StandardStatePort,
    _checkpoint_payload,
)


def checkpoint(
    version: int,
    *,
    pending: PendingAction | None = None,
    turn_index: int = 0,
) -> dict:
    return _checkpoint_payload(
        LoopState(
            run_id="placeholder",
            task_id="placeholder",
            goal="recover",
            max_turns=10,
            checkpoint_version=version,
            turn_index=turn_index,
            pending_action=pending,
        )
    )


def state_port(repository, run) -> StandardStatePort:
    return StandardStatePort(
        repository,
        run,
        run.id,
        "recover",
        12,
    )


def test_run_profiles_freeze_distinct_runtime_identities():
    standard = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy())
    trusted = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.confirm,
    )

    assert standard.runtime_kind == RuntimeKind.fast_v1
    assert standard.runtime_version == 1
    assert standard.fast_runtime_policy is not None
    assert trusted.runtime_kind == RuntimeKind.trusted_v1
    assert trusted.runtime_version == 1
    assert trusted.fast_runtime_policy is None


def test_canonical_checkpoint_forbids_trusted_lifecycle_fields():
    with pytest.raises(ValidationError):
        LoopState.model_validate(
            {
                "run_id": "run-1",
                "task_id": "task-1",
                "goal": "test",
                "max_turns": 2,
                "task_contract": {"goal": "must not be accepted"},
            }
        )


async def test_standard_checkpoint_persists_with_optimistic_versioning(session):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run("fast state", {"provider": "mock"})

    assert run.runtime_kind == "fast-v1"
    assert run.fast_state_version == 0
    await repository.update_runtime_checkpoint(
        run.id,
        expected_version=0,
        checkpoint=checkpoint(1, turn_index=1),
    )
    await repository.commit()

    loaded = await repository.require_run(run.id)
    assert loaded.fast_state_version == 1
    assert any(event.type == "fast.snapshot.updated" for event in loaded.events)
    with pytest.raises(ValueError, match="Fast state version conflict"):
        await repository.update_runtime_checkpoint(
            run.id,
            expected_version=0,
            checkpoint=checkpoint(1),
        )


async def test_runtime_identity_survives_task_preference_changes_and_reload(session):
    repository = RunUnitOfWork(session)
    standard = await repository.create_task_run("first", {"provider": "mock"})
    trusted_profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
    )
    trusted = await repository.create_task_run(
        "second",
        {"provider": "mock"},
        task_id=standard.task_id,
        answer_mode="trusted",
        reasoning_policy=trusted_profile.reasoning_policy.model_dump(mode="json"),
        execution_profile=trusted_profile.model_dump(mode="json"),
    )
    await repository.commit()

    reloaded_standard = await repository.require_run(standard.id)
    reloaded_trusted = await repository.require_run(trusted.id)
    standard_view = run_payload(reloaded_standard)

    assert reloaded_standard.runtime_kind == "fast-v1"
    assert reloaded_standard.runtime_version == 1
    assert reloaded_trusted.runtime_kind == "trusted-v1"
    assert standard_view["runtime_kind"] == "fast-v1"
    assert standard_view["fast_runtime_snapshot"]["protocol_version"] == 1


async def test_recovery_retries_interrupted_model_and_idempotent_tool(session):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run("recover", {"provider": "mock"})
    run.fast_runtime_snapshot = checkpoint(
        0,
        pending=PendingAction(action_id="model-1", kind="model"),
    )
    port = state_port(repository, run)
    state = await port.load()

    recovered, outcome = await port.recover(state)

    assert outcome is None
    assert recovered.pending_action is None
    assert recovered.observations[-1].data["category"] == "interrupted_model_call"

    call = await repository.start_tool_call(
        run.id,
        None,
        "read_value",
        "1",
        {"key": "a"},
        "network_read",
        "read_only",
    )
    pending = PendingAction(
        action_id=call.id,
        kind="tool",
        phase="executing",
        action=LoopAction(kind="tool", name="read_value", input={"key": "a"}),
        idempotent=True,
    )
    run.fast_runtime_snapshot = checkpoint(run.fast_state_version, pending=pending)
    port = state_port(repository, run)
    recovered, outcome = await port.recover(await port.load())

    assert outcome is None
    assert recovered.pending_action is None
    assert port.take_resume_action().name == "read_value"


async def test_recovery_reuses_result_and_never_replays_unknown_write(session):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run("recover", {"provider": "mock"})
    completed = await repository.start_tool_call(run.id, None, "read_value", "1", {"key": "a"}, "network_read", "read_only")
    await repository.finish_tool_call(
        completed.id,
        output={"status": "succeeded", "data": {"value": "a"}},
    )
    run.fast_runtime_snapshot = checkpoint(
        0,
        pending=PendingAction(
            action_id=completed.id,
            kind="tool",
            phase="executing",
            action=LoopAction(kind="tool", name="read_value", input={"key": "a"}),
            idempotent=True,
        ),
    )
    port = state_port(repository, run)
    recorded, outcome = await port.recover(await port.load())

    assert outcome is None
    assert recorded.observations[-1].data["value"] == "a"

    uncertain = await repository.start_tool_call(run.id, None, "write_value", "1", {"value": "x"}, "workspace_write", "write")
    run.fast_runtime_snapshot = checkpoint(
        run.fast_state_version,
        pending=PendingAction(
            action_id=uncertain.id,
            kind="tool",
            phase="executing",
            action=LoopAction(kind="tool", name="write_value", input={"value": "x"}),
            idempotent=False,
        ),
    )
    port = state_port(repository, run)
    unknown, outcome = await port.recover(await port.load())

    assert outcome.kind == "waiting"
    assert port.take_resume_action() is None
    assert uncertain.status == "result_unknown"
    assert unknown.observations[-1].data["category"] == "non_idempotent_result_unknown"
