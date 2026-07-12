import json
import pytest

from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.engine import RunEngine
from app.runner.model_client import MockModelClient
from app.runner.reasoning import PolicyCompiler
from app.schemas.agent import RequestedReasoningPolicy
from fake_web_tools import fake_web_registry


async def test_engine_completes_mock_web_query(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)

    engine = RunEngine(
        settings,
        model_client=MockModelClient(),
        tool_registry=fake_web_registry(),
    )
    await engine._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "completed"
    assert loaded.result["sources"]
    assert loaded.result["source_quality"]
    assert loaded.result["verification_notes"]
    assert all(step.status == "completed" for step in loaded.steps)

    evidence_artifacts = [artifact for artifact in loaded.artifacts if artifact.type == "evidence_pack"]
    assert evidence_artifacts
    evidence_pack = json.loads(evidence_artifacts[0].content_ref)
    assert evidence_pack["fetched_sources"]
    succeeded_fetch_calls = [
        call for call in loaded.tool_calls if call.tool_name == "web_fetch" and call.status == "succeeded"
    ]
    assert len(evidence_pack["fetched_sources"]) == len(succeeded_fetch_calls)


class PlanningSpyClient(MockModelClient):
    def __init__(self):
        self.contract_calls = 0
        self.plan_calls = 0

    async def contract(self, goal):
        self.contract_calls += 1
        return await super().contract(goal)

    async def plan(self, goal):
        self.plan_calls += 1
        return await super().plan(goal)


def engine_policy(planning_strategy, execution_mode="request_approval"):
    return PolicyCompiler().compile(RequestedReasoningPolicy(planning_strategy=planning_strategy, execution_mode=execution_mode)).model_dump(mode="json")


@pytest.mark.parametrize(("strategy", "mode", "contract_calls", "plan_calls"), [
    ("direct", "request_approval", 0, 0),
    ("adaptive", "request_approval", 1, 0),
    ("plan_first", "request_approval", 1, 1),
    ("direct", "plan_only", 1, 1),
])
async def test_engine_planning_strategy_selects_distinct_path(session, strategy, mode, contract_calls, plan_calls):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy, reasoning_policy=engine_policy(strategy, mode))
    client = PlanningSpyClient()

    await RunEngine(settings, model_client=client, tool_registry=fake_web_registry())._run_with_repo(repo, run.id)

    assert client.contract_calls == contract_calls
    assert client.plan_calls == plan_calls
