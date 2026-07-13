import json

import pytest
from fake_web_tools import fake_web_registry

from app.agent_profile import AgentProfileLoader, load_agent_profile
from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.engine import RunEngine
from app.runner.model_client import MockModelClient
from app.runner.reasoning import PolicyCompiler
from app.schemas.agent import RequestedReasoningPolicy


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

    evidence_artifacts = [
        artifact for artifact in loaded.artifacts if artifact.type == "evidence_pack"
    ]
    assert evidence_artifacts
    evidence_pack = json.loads(evidence_artifacts[0].content_ref)
    assert evidence_pack["fetched_sources"]
    succeeded_fetch_calls = [
        call
        for call in loaded.tool_calls
        if call.tool_name == "web_fetch" and call.status == "succeeded"
    ]
    assert len(evidence_pack["fetched_sources"]) == len(succeeded_fetch_calls)

    events = await repo.list_events(run.id)
    event_types = [event.type for event in events]
    planning_index = event_types.index("reasoning.phase.started")
    selecting_index = next(
        index
        for index, event in enumerate(events)
        if event.type == "reasoning.phase.started"
        and event.payload.get("phase") == "selecting_action"
    )
    summary_index = event_types.index("reasoning.summary.completed")
    turn_index = event_types.index("agent_turn.created")
    tool_index = event_types.index("tool_call.started")
    assert planning_index < selecting_index < summary_index < turn_index < tool_index
    process_events = [event for event in events if event.type.startswith("reasoning.")]
    assert all("reasoning_content" not in event.payload for event in process_events)
    assert all("tool_input" not in event.payload for event in process_events)


async def test_answer_delta_batching_flushes_first_and_final_content(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("流式批处理", settings.model_policy)
    engine = RunEngine(settings, model_client=MockModelClient(), tool_registry=fake_web_registry())

    await engine._start_answer_stream(repo, run.id)
    await engine._handle_answer_delta(repo, run.id, "首")
    await engine._handle_answer_delta(repo, run.id, "尾")
    await engine._handle_answer_delta(repo, run.id, "\1")
    await engine._complete_answer_stream(repo, run.id, "首尾")

    events = await repo.list_events(run.id)
    assert [event.type for event in events] == [
        "run.created",
        "answer.started",
        "answer.delta",
        "answer.delta",
        "answer.settling",
        "answer.completed",
    ]
    assert events[-1].payload == {"content": "首尾", "status": "answer_complete"}


async def test_engine_resumes_with_frozen_profile_when_packaged_default_changes(
    session, monkeypatch
):
    repo = RunRepository(session)
    frozen = load_agent_profile()
    run = await repo.create_task_run(
        "恢复 Profile", {"provider": "mock"}, agent_profile_snapshot=frozen.snapshot()
    )
    changed_contents = {document.name: document.content for document in frozen.manifest.documents}
    changed_contents["soul"] = changed_contents["soul"].replace("Astra 真诚", "Astra 始终真诚", 1)
    changed = AgentProfileLoader().load(changed_contents)
    monkeypatch.setattr("app.runner.engine.load_agent_profile", lambda: changed)

    selected = await RunEngine(Settings(model_provider="mock"))._profile_for_run(
        repo, run.id, run.agent_profile_snapshot
    )

    assert selected.manifest.version == frozen.manifest.version
    assert selected.manifest.version != changed.manifest.version


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
    return (
        PolicyCompiler()
        .compile(
            RequestedReasoningPolicy(
                planning_strategy=planning_strategy, execution_mode=execution_mode
            )
        )
        .model_dump(mode="json")
    )


@pytest.mark.parametrize(
    ("strategy", "mode", "contract_calls", "plan_calls"),
    [
        ("direct", "request_approval", 0, 0),
        ("adaptive", "request_approval", 1, 0),
        ("plan_first", "request_approval", 1, 1),
        ("direct", "plan_only", 1, 1),
    ],
)
async def test_engine_planning_strategy_selects_distinct_path(
    session, strategy, mode, contract_calls, plan_calls
):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "查询 mock 数据", settings.model_policy, reasoning_policy=engine_policy(strategy, mode)
    )
    client = PlanningSpyClient()

    await RunEngine(
        settings, model_client=client, tool_registry=fake_web_registry()
    )._run_with_repo(repo, run.id)

    assert client.contract_calls == contract_calls
    assert client.plan_calls == plan_calls


async def test_plan_only_result_does_not_expose_internal_conversation_wrapper(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    previous = await repo.create_task_run("第一轮问题", settings.model_policy)
    await repo.update_run_status(previous.id, "completed", summary="第一轮回答")
    current = await repo.create_task_run(
        "第二轮规划",
        settings.model_policy,
        previous.task_id,
        reasoning_policy=engine_policy("plan_first", "plan_only"),
    )

    await RunEngine(
        settings, model_client=MockModelClient(), tool_registry=fake_web_registry()
    )._run_with_repo(repo, current.id)

    loaded = await repo.require_run(current.id)
    result_text = "\n".join(item["text"] for item in loaded.result["findings"])
    assert "Conversation context" not in result_text
    assert "Current user request" not in result_text
    assert "第二轮规划" in result_text
