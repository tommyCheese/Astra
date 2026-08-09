import pytest
from pydantic import ValidationError

from app.application.agent_runtime.policies.reasoning import resolve_run_profile
from app.application.fast_agent_runtime.recovery import FastRecovery
from app.common.schemas.agent.run_policy import (
    FastPendingAction,
    FastRuntimeSnapshot,
    RequestedReasoningPolicy,
)
from app.common.schemas.agent.types import AnswerMode, PlanExecution, RuntimeKind
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.run_view_projection import RunViewProjector


def test_run_profiles_freeze_distinct_runtime_identities():
    fast = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy())
    trusted = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.confirm,
    )

    assert fast.runtime_kind == RuntimeKind.fast_v1
    assert fast.runtime_version == 1
    assert fast.fast_runtime_policy is not None
    assert trusted.runtime_kind == RuntimeKind.trusted_v1
    assert trusted.runtime_version == 1
    assert trusted.fast_runtime_policy is None


def test_fast_snapshot_forbids_trusted_lifecycle_fields():
    with pytest.raises(ValidationError):
        FastRuntimeSnapshot.model_validate(
            {
                "snapshot_version": 1,
                "task_contract": {"goal": "must not be accepted"},
            }
        )


async def test_fast_snapshot_persists_with_optimistic_versioning(session):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run("fast state", {"provider": "mock"})

    assert run.runtime_kind == "fast-v1"
    assert run.fast_state_version == 0
    await repository.update_fast_runtime_snapshot(
        run.id,
        expected_version=0,
        snapshot=FastRuntimeSnapshot(
            snapshot_version=1,
            turn_index=1,
            recent_observations=[{"kind": "tool_result", "status": "ok"}],
            terminal_intent="answer",
        ),
    )
    await repository.commit()

    loaded = await repository.require_run(run.id)
    assert loaded.fast_state_version == 1
    assert loaded.fast_runtime_snapshot["terminal_intent"] == "answer"
    assert any(event.type == "fast.snapshot.updated" for event in loaded.events)
    with pytest.raises(ValueError, match="Fast state version conflict"):
        await repository.update_fast_runtime_snapshot(
            run.id,
            expected_version=0,
            snapshot=FastRuntimeSnapshot(snapshot_version=1),
        )


async def test_runtime_identity_survives_task_preference_changes_and_reload(session):
    repository = RunUnitOfWork(session)
    fast = await repository.create_task_run("first", {"provider": "mock"})
    trusted_profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
    )
    trusted = await repository.create_task_run(
        "second",
        {"provider": "mock"},
        task_id=fast.task_id,
        answer_mode="trusted",
        reasoning_policy=trusted_profile.reasoning_policy.model_dump(mode="json"),
        execution_profile=trusted_profile.model_dump(mode="json"),
    )
    await repository.commit()

    reloaded_fast = await repository.require_run(fast.id)
    reloaded_trusted = await repository.require_run(trusted.id)
    fast_view = RunViewProjector().payload(reloaded_fast)

    assert reloaded_fast.runtime_kind == "fast-v1"
    assert reloaded_fast.runtime_version == 1
    assert reloaded_trusted.runtime_kind == "trusted-v1"
    assert fast_view["runtime_kind"] == "fast-v1"
    assert fast_view["fast_runtime_snapshot"]["protocol_version"] == 1


async def test_fast_recovery_retries_interrupted_model_and_idempotent_tool(session):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run("recover", {"provider": "mock"})
    recovery = FastRecovery()

    model_result = await recovery.recover(
        repository,
        run.id,
        FastRuntimeSnapshot(
            snapshot_version=1,
            pending_action=FastPendingAction(action_id="model-1", kind="model"),
        ),
    )
    assert model_result.snapshot.pending_action is None
    assert model_result.observations[-1].data["category"] == "interrupted_model_call"

    call = await repository.start_tool_call(
        run.id,
        None,
        "read_value",
        "1",
        {"key": "a"},
        "network_read",
        "read_only",
    )
    tool_result = await recovery.recover(
        repository,
        run.id,
        FastRuntimeSnapshot(
            snapshot_version=2,
            pending_action=FastPendingAction(
                action_id=call.id,
                kind="tool",
                phase="executing",
                tool_name="read_value",
                tool_input={"key": "a"},
                idempotent=True,
            ),
        ),
    )
    assert tool_result.replay_action is not None
    assert tool_result.replay_action.tool_name == "read_value"


async def test_fast_recovery_reuses_recorded_result_and_never_replays_unknown_write(session):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run("recover", {"provider": "mock"})
    recovery = FastRecovery()
    completed = await repository.start_tool_call(
        run.id, None, "read_value", "1", {"key": "a"}, "network_read", "read_only"
    )
    await repository.finish_tool_call(
        completed.id,
        output={"status": "succeeded", "data": {"value": "a"}},
    )
    recorded = await recovery.recover(
        repository,
        run.id,
        FastRuntimeSnapshot(
            snapshot_version=1,
            pending_action=FastPendingAction(
                action_id=completed.id,
                kind="tool",
                phase="executing",
                tool_name="read_value",
                tool_input={"key": "a"},
                idempotent=True,
            ),
        ),
    )
    assert recorded.replay_action is None
    assert recorded.observations[-1].data == {"value": "a"}

    uncertain = await repository.start_tool_call(
        run.id, None, "write_value", "1", {"value": "x"}, "workspace_write", "write"
    )
    unknown = await recovery.recover(
        repository,
        run.id,
        FastRuntimeSnapshot(
            snapshot_version=2,
            pending_action=FastPendingAction(
                action_id=uncertain.id,
                kind="tool",
                phase="executing",
                tool_name="write_value",
                tool_input={"value": "x"},
                idempotent=False,
            ),
        ),
    )
    assert unknown.result_unknown is True
    assert unknown.replay_action is None
    assert uncertain.status == "result_unknown"
    assert unknown.observations[-1].data["category"] == "non_idempotent_result_unknown"
