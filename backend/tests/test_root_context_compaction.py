import pytest

from app.application.context_compaction import TokenAccountingService
from app.application.context_compaction.root import (
    _protected_prefix,
    _reference_manifest,
    compact_root_context,
)
from app.common.core.config import AstraRuntimeSettings
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


def test_trusted_root_prefix_rebuilds_canonical_governance_inputs():
    prefix = _protected_prefix(
        goal="finish the governed task",
        trusted=True,
        accounting=TokenAccountingService(),
        context={
            "tool_manifests": {
                "write_file": {
                    "permission": "workspace.write",
                    "side_effect_level": "high",
                }
            },
            "tool_selection": {"selected": ["write_file"]},
            "active_skills": [{"qualified_identity": "skill:active@1"}],
            "skill_catalog": [{"qualified_identity": "skill:active@1", "digest": "abc"}],
            "agent_profile_snapshot": {"profile": "trusted"},
            "execution_profile": {"permission_bundle": {"workspace": "write"}},
            "task_contract": {
                "success_criteria": [{"id": "done", "description": "verify"}],
                "verification_requirements": [{"validator": "tests"}],
            },
            "plan_graph": {"version": 4, "nodes": []},
            "state_version": 8,
            "plan_version": 4,
            "agent_state": {
                "version": 7,
                "active_plan_version": 4,
                "active_executions": [{"execution_id": "node-1"}],
                "terminal_intent": "complete",
            },
            "evidence_pack": {"verified": ["evidence:1"]},
            "subagent_active_groups": [{"group_id": "group-1"}],
        },
    )

    sections = {item.kind: item.content for item in prefix}

    assert sections["profile_snapshot"] == {"profile": "trusted"}
    assert sections["skill_snapshot"]["catalog"][0]["digest"] == "abc"
    assert sections["permissions"]["candidate_tools"]["write_file"]["permission"] == "workspace.write"
    assert sections["agent_state_versions"] == {
        "run_state_version": 8,
        "plan_version": 4,
        "agent_state_version": 7,
        "active_plan_version": 4,
        "active_executions": [{"execution_id": "node-1"}],
    }
    assert sections["completion_gate"]["success_criteria"] == [
        {"id": "done", "description": "verify"}
    ]


def test_standard_root_prefix_does_not_create_trusted_governance_sections():
    prefix = _protected_prefix(
        goal="answer directly",
        trusted=False,
        accounting=TokenAccountingService(),
        context={"task_contract": {"secret": "not for standard"}},
    )

    assert {item.kind for item in prefix}.isdisjoint(
        {"task_contract", "profile_snapshot", "permissions", "completion_gate"}
    )


def test_root_reference_manifest_includes_only_consumed_child_results():
    manifest = _reference_manifest(
        [
            {
                "kind": "subagent_join",
                "status": "succeeded",
                "data": {"join_id": "join-1", "source_execution_ids": ["child-1"]},
            },
            {
                "kind": "subagent_join",
                "status": "waiting",
                "data": {"join_id": "join-2", "source_execution_ids": ["child-2"]},
            },
        ]
    )

    assert [(reference.kind, reference.ref) for reference in manifest] == [
        ("child_result", "subagent_join:join-1:child:child-1")
    ]


@pytest.mark.asyncio
async def test_disabled_root_compaction_does_not_change_model_context(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("keep context", {})
    observations = [{"kind": "note", "summary": "unchanged", "data": {}}]
    context = root_context(observations)

    result = await compact_root_context(
        repo=repo,
        settings=AstraRuntimeSettings(),
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
    settings = AstraRuntimeSettings(
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


@pytest.mark.asyncio
async def test_root_compaction_persists_recovery_continuation_manifest(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("preserve continuation", {})
    await repo.create_agent_turn(
        run.id,
        1,
        "call_tool",
        "retry safely",
        idempotency_key="tool-call-key",
    )
    await repo.set_waiting_state(run.id, {"request": "approve the next action"})
    observations = [
        {"kind": "note", "summary": f"old-{index}", "data": {"text": "界" * 2_000}}
        for index in range(6)
    ]
    context = root_context(observations)
    settings = AstraRuntimeSettings(
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

    await compact_root_context(
        repo=repo,
        settings=settings,
        model_client=MockModelClient(),
        run_id=run.id,
        goal="preserve continuation",
        context=context,
        observations=observations,
    )

    root = await AgentExecutionRepository(session).root_for_run(run.id)
    assert root is not None
    continuation = root.checkpoint["context_continuation"]
    assert continuation["action_idempotency_keys"] == ["tool-call-key"]
    assert continuation["waiting_state"]["request"] == "approve the next action"
    assert continuation["cancellation_epoch"] == root.cancellation_epoch
    assert continuation["retained_tail_ids"] == root.checkpoint["context_compaction"][
        "retained_tail_ids"
    ]
