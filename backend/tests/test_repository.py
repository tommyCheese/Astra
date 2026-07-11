from app.repositories.runs import RunRepository, run_to_view
from app.runner.reasoning import build_default_contract, build_plan_graph
from app.schemas.agent import AgentState, PlanningStrategy


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


async def test_follow_up_run_reuses_task(session):
    repo = RunRepository(session)
    first = await repo.create_task_run("第一轮问题", {"provider": "mock"})
    follow_up = await repo.create_task_run("继续追问", {"provider": "mock"}, first.task_id)

    assert follow_up.task_id == first.task_id
    assert follow_up.id != first.id
    assert follow_up.model_policy["conversation_goal"] == "继续追问"


async def test_agent_turn_and_memory_persistence(session):
    repo = RunRepository(session)
    run = await repo.create_task_run("记住一个来源", {"provider": "mock", "model": "mock"})

    turn = await repo.create_agent_turn(
        run.id,
        1,
        "call_tool",
        "搜索候选来源",
        selected_tool="web_search",
        decision={"decision_type": "call_tool", "tool_name": "web_search"},
    )
    memory = await repo.create_memory(
        run_id=run.id,
        scope="run",
        kind="source_summary",
        content="本次任务找到一个来源。",
        provenance={"run_id": run.id, "turn_id": turn.id},
        confidence=0.8,
    )
    await repo.update_agent_turn(
        turn.id,
        status="completed",
        observation={"kind": "tool_result", "status": "succeeded"},
        memory_writes=[{"id": memory.id}],
    )

    loaded = await repo.require_run(run.id)
    view = run_to_view(loaded)

    assert view["turns"][0]["selected_tool"] == "web_search"
    assert view["memories"][0]["content"] == "本次任务找到一个来源。"
    assert any(message["role"] == "tool" for message in view["chat_messages"])


async def test_persistent_memory_requires_provenance(session):
    repo = RunRepository(session)
    run = await repo.create_task_run("偏好测试", {"provider": "mock", "model": "mock"})

    try:
        await repo.create_memory(
            run_id=run.id,
            scope="user",
            kind="preference",
            content="始终使用中文。",
            provenance={},
            confidence=0.9,
        )
    except ValueError as exc:
        assert "provenance" in str(exc)
    else:
        raise AssertionError("Expected provenance validation to fail")


async def test_reasoning_state_is_versioned_and_waiting_run_resumes(session):
    repo = RunRepository(session)
    run = await repo.create_task_run("需要选择", {"provider": "mock"}, reasoning_policy={"version": 1})
    contract = build_default_contract("需要选择")
    graph = build_plan_graph(contract, PlanningStrategy.adaptive)
    state = AgentState(task_contract=contract, plan=graph)
    await repo.initialize_reasoning_state(run.id, task_contract=contract.model_dump(mode="json"), plan_graph=graph.model_dump(mode="json"), agent_state=state.model_dump(mode="json"))
    await repo.set_waiting_state(run.id, {"paused_node": "build_contract", "request": "请选择"})
    waiting = await repo.require_run(run.id)
    token = waiting.waiting_state["continuation_token"]
    resumed = await repo.resume_waiting_run(run.id, {"kind": "user_response", "status": "received", "summary": "选 A"}, continuation_token=token)
    assert resumed.status == "executing"
    assert resumed.state_version == 2
    assert resumed.agent_state["observations"][-1]["summary"] == "选 A"


async def test_reasoning_state_rejects_stale_version(session):
    repo = RunRepository(session)
    run = await repo.create_task_run("版本测试", {"provider": "mock"})
    contract = build_default_contract("版本测试")
    graph = build_plan_graph(contract, PlanningStrategy.direct)
    state = AgentState(task_contract=contract, plan=graph)
    await repo.initialize_reasoning_state(run.id, task_contract=contract.model_dump(mode="json"), plan_graph=graph.model_dump(mode="json"), agent_state=state.model_dump(mode="json"))
    try:
        await repo.update_reasoning_state(run.id, expected_version=0, agent_state=state.model_copy(update={"version": 2}).model_dump(mode="json"))
    except ValueError as exc:
        assert "version conflict" in str(exc)
    else:
        raise AssertionError("Expected stale state update rejection")
