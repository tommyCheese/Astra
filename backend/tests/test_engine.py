import json

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
