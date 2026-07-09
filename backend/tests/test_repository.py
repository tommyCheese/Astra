from app.repositories.runs import RunRepository, run_to_view


async def test_run_lifecycle_persistence(session):
    repo = RunRepository(session)
    run = await repo.create_task_run("查询一个主题", {"provider": "mock", "model": "mock"})

    await repo.update_run_status(run.id, "planning")
    step = await repo.create_step(run.id, 1, "搜索", "调用 web_search")
    await repo.update_step(step.id, "running")
    call = await repo.start_tool_call(
        run.id,
        step.id,
        "web_search",
        "0.1.0",
        {"query": "Astra"},
        "network_read",
        "read_only",
    )
    await repo.finish_tool_call(call.id, output={"candidates": []})
    await repo.update_step(step.id, "completed", evidence={"candidate_count": 0})
    await repo.update_run_status(run.id, "completed_with_warnings", result={"summary": "done"})

    loaded = await repo.require_run(run.id)
    view = run_to_view(loaded)

    assert view["status"] == "completed_with_warnings"
    assert len(view["steps"]) == 1
    assert len(view["tool_calls"]) == 1
    assert view["tool_calls"][0]["status"] == "succeeded"
    assert len(view["events"]) >= 5
