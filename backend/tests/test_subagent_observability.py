from datetime import UTC, datetime, timedelta

from app.common.schemas.subagents import DelegationContract, DelegationRequest
from app.infrastructure.db.models.executions import ModelInvocationRecord
from app.infrastructure.db.models.runs import RunEventRecord
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.subagent_telemetry import (
    SubagentTelemetryRepository,
)


async def test_telemetry_is_aggregate_and_does_not_expose_sensitive_content(session):
    run = await RunUnitOfWork(session).create_task_run(
        "secret user prompt",
        {"provider": "mock", "model": "test-model", "api_key": "secret"},
        reasoning_policy={"effective": {"subagents": {"rollout_cohort": "administrator_canary"}}},
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
