import pytest

from app.application.context_compaction.root import compact_root_context
from app.common.core.config import Settings
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


def root_context(observations):
    return {
        "answer_mode": "standard",
        "tool_manifests": {},
        "active_skills": [],
        "state_version": 0,
        "plan_version": 1,
        "active_node": None,
        "observations": observations,
    }


@pytest.mark.asyncio
async def test_disabled_root_compaction_does_not_change_model_context(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("keep context", {})
    observations = [{"kind": "note", "summary": "unchanged", "data": {}}]
    context = root_context(observations)

    result = await compact_root_context(
        repo=repo,
        settings=Settings(),
        model_client=MockModelClient(),
        run_id=run.id,
        goal="keep context",
        context=context,
        observations=observations,
    )

    assert result is context
    assert result["observations"] == observations
    assert "context_checkpoint" not in result


@pytest.mark.asyncio
async def test_root_compaction_installs_checkpoint_and_bounds_model_projection(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("compact root", {})
    observations = [
        {"kind": "note", "summary": f"old-{index}", "data": {"text": "界" * 2_000}}
        for index in range(6)
    ]
    context = root_context(observations)
    settings = Settings(
        model_provider="unknown",
        model_name="small",
        context_window_fallback_tokens=16_384,
        context_output_reserve_tokens=1_024,
        context_compaction_output_reserve_tokens=512,
        context_auto_compact_ratio=0.5,
        context_compaction_recovery_ratio=0.3,
        context_compaction_recent_tail_tokens=3_000,
        context_compaction_v2_enabled=True,
        context_compaction_root_enabled=True,
        context_compaction_shadow_mode=False,
    )

    result = await compact_root_context(
        repo=repo,
        settings=settings,
        model_client=MockModelClient(),
        run_id=run.id,
        goal="compact root",
        context=context,
        observations=observations,
    )

    root = await AgentExecutionRepository(session).root_for_run(run.id)
    assert root is not None
    assert result["context_checkpoint"]["checkpoint_role"] == "root_execution"
    assert len(result["observations"]) < len(observations)
    assert root.checkpoint["context_compaction"]["source_item_ids"]
    assert root.checkpoint["context_compaction"]["retained_tail_ids"]
