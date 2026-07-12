import pytest

from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.agent_loop import AgentLoop, ToolRouter
from app.runner.model_client import MockModelClient
from app.tools.base import ToolExecutionError
from app.tools.web import build_web_registry
from fake_web_tools import fake_web_registry


async def test_agent_loop_completes_mock_web_run(session):
    settings = Settings(model_provider="mock", web_search_provider="mock", agent_max_turns=8)
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)
    client = MockModelClient()
    loop = AgentLoop(settings, model_client=client, tool_registry=fake_web_registry())

    output = await loop.run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)

    assert output["status"] == "completed"
    assert output["result"]["sources"]
    assert output["result"]["verification_report"]["source_count"] >= 1
    assert loaded.turns
    assert any(turn.selected_tool == "web_search" for turn in loaded.turns)
    assert any(turn.selected_tool == "web_fetch" for turn in loaded.turns)
    assert any(artifact.type == "evidence_pack" for artifact in loaded.artifacts)
    assert loaded.memories


async def test_agent_loop_blocks_at_turn_limit(session):
    settings = Settings(
        model_provider="mock",
        web_search_provider="mock",
        agent_max_turns=1,
        agent_max_tool_calls=1,
    )
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)
    loop = AgentLoop(settings, model_client=MockModelClient(), tool_registry=build_web_registry(settings))

    output = await loop.run(repo, run.id, run.task.description)

    assert output["status"] == "blocked"
    assert "没有成功抓取到可用来源" in " ".join(output["result"]["verification_report"]["notes"])


def test_tool_router_rejects_disallowed_tool():
    router = ToolRouter(build_web_registry(Settings()), allowed_tools={"web_search", "web_fetch"})

    with pytest.raises(ToolExecutionError) as exc_info:
        router.resolve("shell.run", {"cmd": "date"})

    assert exc_info.value.category == "tool_not_allowed"
