from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.engine import RunEngine
from app.runner.model_client import MockModelClient
from app.tools.web import build_web_registry


async def test_engine_completes_mock_web_query(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)

    engine = RunEngine(
        settings,
        model_client=MockModelClient(),
        tool_registry=build_web_registry(settings),
    )
    await engine._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "completed"
    assert loaded.result["sources"]
    assert loaded.result["verification_notes"]
    assert all(step.status == "completed" for step in loaded.steps)
