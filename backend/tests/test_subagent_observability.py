from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import ModelInvocationRecord, RunEventRecord
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.runs import RunRepository
from app.schemas.subagents import DelegationContract, DelegationRequest
from app.subagents.observability import (
    DELEGATION_BEHAVIOR_CASES,
    BenchmarkResult,
    ReleaseThresholds,
    RolloutState,
    SubagentTelemetryRepository,
    evaluate_delegation_behavior,
    evaluate_release_gate,
)


async def test_telemetry_is_aggregate_and_does_not_expose_sensitive_content(session):
    run = await RunRepository(session).create_task_run(
        "secret user prompt",
        {"provider": "mock", "model": "test-model", "api_key": "secret"},
        reasoning_policy={
            "effective": {"subagents": {"rollout_cohort": "administrator_canary"}}
        },
    )
    executions = AgentExecutionRepository(session)
    root = await executions.root_for_run(run.id)
    assert root is not None
    child = await executions.create_child(
        contract=DelegationContract(
            contract_id="telemetry-contract",
            contract_hash="sha256:telemetry",
            task_id=run.task_id,
            run_id=run.id,
            parent_execution_id=root.id,
            depth=1,
            request=DelegationRequest(
                request_id="telemetry-child",
                objective="private objective",
                success_criteria=["return result"],
                scope={"included": ["private scope"]},
                output_schema={"type": "object"},
                dedupe_key="telemetry-child",
            ),
            created_at=datetime.now(UTC),
        )
    )
    child.status = "completed"
    child.claimed_at = datetime.now(UTC) - timedelta(seconds=2)
    child.finished_at = datetime.now(UTC)
    session.add_all(
        [
            ModelInvocationRecord(
                run_id=run.id,
                agent_execution_id=child.id,
                provider="mock",
                model="test-model",
                operation="decide",
                attempt=1,
                status="succeeded",
                total_tokens=120,
                raw_usage={"cost_usd": 0.02, "raw_input": "secret tool input"},
            ),
            RunEventRecord(
                run_id=run.id,
                agent_execution_id=root.id,
                type="subagent.delegation_rejected",
                payload={"reason_code": "not_beneficial", "prompt": "secret prompt"},
            ),
        ]
    )
    await session.commit()

    summary = await SubagentTelemetryRepository(session).summary(run.id)

    assert summary["cohort"] == "administrator_canary"
    assert summary["delegation"]["accepted"] == 1
    assert summary["delegation"]["rejection_reasons"] == {"not_beneficial": 1}
    assert summary["usage"] == {
        "model_calls": 1,
        "tool_calls": 0,
        "tokens": 120,
        "cost_usd": 0.02,
    }
    serialized = str(summary)
    assert "secret user prompt" not in serialized
    assert "secret tool input" not in serialized
    assert "secret prompt" not in serialized
    assert "private objective" not in serialized


def test_behavior_eval_covers_positive_and_negative_delegation_cases():
    predictions = {
        str(case["id"]): bool(case["should_delegate"])
        for case in DELEGATION_BEHAVIOR_CASES
    }
    assert evaluate_delegation_behavior(predictions)["passed"] is True

    predictions["simple_question"] = True
    failed = evaluate_delegation_behavior(predictions)
    assert failed["passed"] is False
    assert failed["incorrect"] == ["simple_question"]


def test_release_gates_control_staged_rollout_and_automatic_kill_switch():
    baseline = BenchmarkResult(
        quality=0.8,
        latency_ms=1_000,
        tokens=1_000,
        cost_usd=0.1,
        recovery_rate=1,
    )
    candidate = BenchmarkResult(
        quality=0.85,
        latency_ms=1_400,
        tokens=1_800,
        cost_usd=0.18,
        failure_rate=0.01,
        recovery_rate=1,
        cancellation_p95_ms=500,
    )
    decision = evaluate_release_gate(baseline=baseline, candidate=candidate)
    assert decision.passed is True
    assert RolloutState().promote(decision).stage == "administrator_canary"

    unsafe = evaluate_release_gate(
        baseline=baseline,
        candidate=BenchmarkResult(
            quality=0.9,
            latency_ms=1_000,
            tokens=1_000,
            cost_usd=0.1,
            failure_rate=0.2,
            recovery_rate=0.5,
            safety_failures=1,
        ),
        thresholds=ReleaseThresholds(),
    )
    assert unsafe.passed is False
    assert unsafe.activate_kill_switch is True
    with pytest.raises(ValueError, match="cannot advance"):
        RolloutState().promote(unsafe)
    assert RolloutState(stage="trusted_read_only").rollback().kill_switch is True
