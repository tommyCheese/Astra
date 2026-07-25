import importlib.util
from pathlib import Path

import pytest

from app.db.mode_upgrade import ModeUpgradeRequired, validate_mode_upgrade
from app.repositories.runs import RunRepository
from app.runner.reasoning import PolicyCompiler, RunProfileResolver
from app.schemas.agent import AnswerMode, PlanExecution, RequestedReasoningPolicy

spec = importlib.util.spec_from_file_location(
    "mode_upgrade_migration",
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "0017_simplify_answer_modes_and_planning.py",
)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.parametrize(
    ("answer_mode", "planning_strategy", "execution_mode", "status", "cancelled"),
    [
        ("standard", "adaptive", "request_approval", "completed", False),
        ("trusted", "adaptive", "request_approval", "completed", False),
        ("trusted", "plan_first", "request_approval", "completed", False),
        ("trusted", "plan_first", "plan_only", "completed", False),
        ("standard", "adaptive", "request_approval", "executing", True),
        ("trusted", "plan_first", "plan_only", "waiting_user", True),
    ],
)
def test_mode_upgrade_rewrites_legacy_profiles(
    answer_mode, planning_strategy, execution_mode, status, cancelled
):
    legacy_policy = {
        "requested": {
            "reasoning_effort": "balanced",
            "planning_strategy": planning_strategy,
            "execution_mode": execution_mode,
        },
        "effective": {
            "reasoning_effort": "balanced",
            "planning_strategy": planning_strategy,
            "execution_mode": execution_mode,
            "budgets": {},
        },
        "adjustments": [
            {"field": "planning_strategy", "rule": "legacy"},
            {"field": "verification_level", "rule": "keep"},
        ],
        "version": 1,
    }
    rewritten = migration._rewrite_run(
        {
            "answer_mode": answer_mode,
            "status": status,
            "reasoning_policy": legacy_policy,
            "execution_profile": {"interactive": True},
        }
    )

    assert rewritten["reasoning_policy"]["version"] == 2
    assert "planning_strategy" not in rewritten["reasoning_policy"]["requested"]
    assert "planning_strategy" not in rewritten["reasoning_policy"]["effective"]
    assert rewritten["reasoning_policy"]["effective"]["execution_mode"] != "plan_only"
    assert rewritten["execution_profile"]["version"] == 2
    assert rewritten["execution_profile"]["plan_execution"] == (
        "auto" if answer_mode == "trusted" else None
    )
    assert (rewritten.get("status") == "cancelled") is cancelled
    if cancelled:
        assert rewritten["terminal_reason"]["code"] == "MODE_UPGRADE_CANCELLED"


async def test_startup_validation_accepts_only_current_live_profiles(session):
    repo = RunRepository(session)
    profile = RunProfileResolver().resolve(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.confirm,
    )
    await repo.create_task_run(
        "新契约",
        {"provider": "mock"},
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    await validate_mode_upgrade(session)


async def test_startup_validation_rejects_deleted_live_contract(session):
    repo = RunRepository(session)
    policy = PolicyCompiler().compile(RequestedReasoningPolicy()).model_dump(mode="json")
    run = await repo.create_task_run(
        "旧契约",
        {"provider": "mock"},
        reasoning_policy=policy,
    )
    run.execution_profile = {
        "answer_mode": "trusted",
        "contract_mode": "model",
        "assurance_level": "full",
        "reasoning_policy": {
            **policy,
            "requested": {**policy["requested"], "planning_strategy": "adaptive"},
        },
        "plan_execution": "auto",
        "validators": [],
        "version": 1,
    }
    await session.commit()

    with pytest.raises(ModeUpgradeRequired):
        await validate_mode_upgrade(session)
