from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import event as sqlalchemy_event

from app.application.agent_runtime.policies.reasoning import build_default_contract
from app.common.schemas.agent.api_views import RunView
from app.common.schemas.agent.execution_state import AgentState
from app.domain.agent_profile import load_agent_profile
from app.infrastructure.db.models.executions import ModelInvocationRecord
from app.infrastructure.repositories.conversation_strategy import ConversationStrategyRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.run_view_projection import RunViewProjector
from app.infrastructure.repositories.tool_settings import ToolSettingsRepository
from app.infrastructure.repositories.usage import UsageRepository


async def test_tool_settings_are_created_and_persisted(session):
    repo = ToolSettingsRepository(session)
    defaults = {"web_search": True, "web_fetch": True, "chart.render": False}
    assert await repo.get_or_create(defaults) == defaults
    await session.commit()

    updated = {"web_search": False, "web_fetch": True, "chart.render": True}
    assert await repo.set_all(updated, defaults) == updated
    await session.commit()

    assert await repo.get_or_create(defaults) == updated


async def test_tool_settings_cache_publishes_only_after_commit(session):
    repo = ToolSettingsRepository(session)
    defaults = {"web_search": True, "web_fetch": True, "chart.render": False}
    assert await repo.get_or_create(defaults) == defaults
    await session.commit()

    await repo.set_all({"web_search": False}, defaults)
    await session.rollback()

    assert await ToolSettingsRepository(session).get_or_create(defaults) == defaults


async def test_committed_tool_settings_cache_avoids_database_reads(session):
    defaults = {"web_search": True, "web_fetch": True, "chart.render": False}
    assert await ToolSettingsRepository(session).get_or_create(defaults) == defaults
    await session.commit()
    statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sqlalchemy_event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        assert await ToolSettingsRepository(session).get_or_create(defaults) == defaults
    finally:
        sqlalchemy_event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)

    assert statements == []


async def test_conversation_strategy_is_created_and_persisted(session):
    repo = ConversationStrategyRepository(session)
    assert await repo.get_or_create() == {
        "preferred_answer_mode": "standard",
        "reasoning_effort": "balanced",
        "max_tool_calls": 8,
        "reflection_enabled": True,
        "reflection_trigger": "adaptive",
    }
    await session.commit()

    updated = {
        "preferred_answer_mode": "trusted",
        "reasoning_effort": "deep",
        "max_tool_calls": None,
        "reflection_enabled": False,
        "reflection_trigger": "failure_only",
    }
    assert await repo.set(updated) == updated
    await session.commit()

    reloaded = ConversationStrategyRepository(session)
    assert await reloaded.get_or_create() == updated


async def test_run_lifecycle_persistence(session):
    repo = RunUnitOfWork(session)
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
    loaded.created_at = datetime(2026, 8, 5, 1, 0, tzinfo=UTC)
    loaded.started_at = loaded.created_at
    loaded.completed_at = loaded.created_at + timedelta(minutes=1, seconds=24)
    view = RunViewProjector().payload(loaded)

    assert view["status"] == "completed_with_warnings"
    assert view["processing_duration_ms"] == 84_000
    assert view["answer_mode"] == "standard"
    assert view["execution_profile"]["version"] == 2
    assert view["execution_profile"]["answer_mode"] == "standard"
    assert len(view["steps"]) == 1
    assert len(view["tool_calls"]) == 1
    assert view["tool_calls"][0]["status"] == "succeeded"
    assert len(view["events"]) >= 5
    assert any(
        message["role"] == "assistant" and message["content"] == "done"
        for message in view["chat_messages"]
    )


async def test_cancel_run_is_idempotent_and_preserves_partial_answer(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("生成一份长回答", {"provider": "mock", "model": "mock"})
    await repo.update_run_status(run.id, "executing")
    step = await repo.create_step(run.id, 1, "检索", "收集证据")
    await repo.update_step(step.id, "running")
    call = await repo.start_tool_call(
        run.id, step.id, "web_search", "0.1.0", {"query": "Astra"}, "network_read", "read_only"
    )
    turn = await repo.create_agent_turn(run.id, 1, "call_tool", "继续检索")
    invocation_id = await UsageRepository(session).create_invocation(
        run_id=run.id, provider="mock", model="mock", operation="answer", attempt=1
    )
    await repo.add_event(run.id, "answer.delta", {"delta": "已经生成"})
    await repo.add_event(run.id, "answer.delta", {"delta": "一部分回答。"})
    await session.commit()

    cancelled = await repo.cancel_run(run.id)
    cancelled_again = await repo.cancel_run(run.id)
    view = RunViewProjector().payload(cancelled_again)

    assert cancelled.status == cancelled_again.status == "cancelled"
    assert cancelled.summary == "已经生成一部分回答。"
    assert cancelled.terminal_reason["category"] == "user_cancelled"
    assert cancelled.terminal_reason["partial_answer"] is True
    assert view["steps"][0]["status"] == "cancelled"
    assert view["tool_calls"][0]["status"] == "cancelled"
    assert view["turns"][0]["status"] == "cancelled"
    invocation = await session.get(ModelInvocationRecord, invocation_id)
    assert invocation.status == "interrupted"
    assert invocation.error_type == "CancelledError"
    assert [event.type for event in cancelled_again.events].count("run.cancelled") == 1
    assert any(message["content"] == "已经生成一部分回答。" for message in view["chat_messages"])
    assert call.id and turn.id


async def test_cancel_run_does_not_overwrite_a_natural_completion(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("快速完成", {"provider": "mock"})
    await repo.update_run_status(
        run.id, "completed", summary="自然完成", result={"summary": "自然完成"}
    )

    unchanged = await repo.cancel_run(run.id)

    assert unchanged.status == "completed"
    assert unchanged.summary == "自然完成"
    assert not any(event.type == "run.cancelled" for event in unchanged.events)


async def test_run_view_rejects_obsolete_persisted_result_contract(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("兼容旧结果", {"provider": "mock"})
    await repo.update_run_status(
        run.id,
        "completed_with_warnings",
        summary="fallback summary",
        result={
            "findings": "legacy finding",
            "caveats": None,
            "verification_report": {
                "status": "completed_with_warnings",
                "notes": ["legacy"],
            },
            "private_runner_state": {"attempt": 4},
        },
    )

    loaded = await repo.require_run(run.id)
    with pytest.raises(ValidationError):
        RunViewProjector().payload(loaded)


async def test_follow_up_run_reuses_task(session):
    repo = RunUnitOfWork(session)
    first = await repo.create_task_run("第一轮问题", {"provider": "mock"})
    follow_up = await repo.create_task_run("继续追问", {"provider": "mock"}, first.task_id)

    assert follow_up.task_id == first.task_id
    assert follow_up.id != first.id
    assert follow_up.model_policy["conversation_goal"] == "继续追问"


async def test_agent_profile_snapshot_is_immutable_and_public_view_is_redacted(session):
    repo = RunUnitOfWork(session)
    snapshot = load_agent_profile().snapshot()
    run = await repo.create_task_run(
        "Profile 测试", {"provider": "mock"}, agent_profile_snapshot=snapshot
    )

    loaded = await repo.require_run(run.id)
    view = RunViewProjector().payload(loaded)

    assert loaded.agent_profile_snapshot["documents"]["identity"]["content"]
    assert view["agent_profile"]["version"] == snapshot["version"]
    assert "content" not in view["agent_profile"]["documents"]["identity"]
    frozen_event = next(event for event in loaded.events if event.type == "agent_profile.frozen")
    assert "content" not in frozen_event.payload["profile"]["documents"]["identity"]
    with pytest.raises(ValueError, match="immutable"):
        await repo.freeze_agent_profile_snapshot(run.id, {"version": "different"})


async def test_repository_freezes_profile_once_for_unversioned_new_run(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("冻结测试", {"provider": "mock"})
    snapshot = load_agent_profile().snapshot()

    await repo.freeze_agent_profile_snapshot(run.id, snapshot)
    await repo.freeze_agent_profile_snapshot(run.id, snapshot)

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    assert loaded.agent_profile_snapshot["version"] == snapshot["version"]
    assert [event.type for event in events].count("agent_profile.frozen") == 1


async def test_list_events_with_status_uses_one_query_for_all_result_shapes(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("流查询测试", {"provider": "mock"})
    first = await repo.add_event(run.id, "test.first", {"index": 1})
    second = await repo.add_event(run.id, "test.second", {"index": 2})
    await repo.update_run_status(run.id, "completed")
    await session.commit()
    terminal = (await repo.list_events(run.id))[-1]

    statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sqlalchemy_event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        events, status = await repo.list_events_with_status(run.id, first.id)
        assert [item.id for item in events] == [second.id, terminal.id]
        assert status == "completed"
        assert len(statements) == 1

        statements.clear()
        events, status = await repo.list_events_with_status(run.id, terminal.id)
        assert events == []
        assert status == "completed"
        assert len(statements) == 1

        statements.clear()
        events, status = await repo.list_events_with_status("missing-run")
        assert events == []
        assert status is None
        assert len(statements) == 1
    finally:
        sqlalchemy_event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)


async def test_initial_run_view_uses_one_query_and_terminal_falls_back_to_full(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "首屏快照测试",
        {"provider": "mock"},
        agent_profile_snapshot=load_agent_profile().snapshot(),
    )
    run_id = run.id
    await session.commit()
    session.expunge_all()
    statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sqlalchemy_event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        loaded, loaded_full = await repo.get_run_initial(run_id)
        assert loaded is not None
        view = RunView.model_validate(RunViewProjector().initial_payload(loaded))
        assert loaded_full is False
        assert view.chat_messages[0].content == "首屏快照测试"
        assert view.events == []
        assert len(statements) == 1
    finally:
        sqlalchemy_event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)

    await repo.update_run_status(
        run_id,
        "completed",
        summary="完成",
        result={"summary": "完整终态"},
    )
    await session.commit()
    session.expunge_all()

    terminal, loaded_full = await repo.get_run_initial(run_id)
    assert terminal is not None
    terminal_view = RunView.model_validate(RunViewProjector().payload(terminal))
    assert loaded_full is True
    assert terminal_view.result is not None
    assert terminal_view.result.summary == "完整终态"


async def test_runtime_resume_context_loads_in_two_queries(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("运行时上下文", {"provider": "mock"})
    run_id = run.id
    await session.commit()
    session.expunge_all()
    statements: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    sqlalchemy_event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        loaded = await repo.require_run_runtime(run_id)
    finally:
        sqlalchemy_event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)

    assert loaded.turns == []
    assert loaded.tool_calls == []
    assert len(statements) == 2


async def test_loading_autodream_protocol_has_no_database_side_effect(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("AutoDream 协议测试", {"provider": "mock"})

    profile = load_agent_profile()
    before = await repo.list_memories(run_id=run.id)
    autodream = profile.document("autodream")
    after = await repo.list_memories(run_id=run.id)

    assert autodream.status == "active"
    assert before == after == []
    assert not any(event.type.startswith("autodream") for event in await repo.list_events(run.id))


async def test_agent_turn_and_memory_persistence(session):
    repo = RunUnitOfWork(session)
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
        kind="episodic_experience",
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
    view = RunViewProjector().payload(loaded)

    assert view["turns"][0]["selected_tool"] == "web_search"
    assert view["memories"][0]["content"] == "本次任务找到一个来源。"
    assert any(message["role"] == "tool" for message in view["chat_messages"])


async def test_persistent_memory_requires_provenance(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("偏好测试", {"provider": "mock", "model": "mock"})

    try:
        await repo.create_memory(
            run_id=run.id,
            scope="user",
            kind="user_preference",
            content="始终使用中文。",
            provenance={},
            confidence=0.9,
        )
    except ValueError as exc:
        assert "provenance" in str(exc)
    else:
        raise AssertionError("Expected provenance validation to fail")


async def test_reasoning_state_is_versioned_and_waiting_run_resumes(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("需要选择", {"provider": "mock"})
    contract = build_default_contract("需要选择")
    state = AgentState(task_contract=contract)
    await repo.initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph={},
        agent_state=state.model_dump(mode="json"),
    )
    turn = await repo.create_agent_turn(
        run.id,
        1,
        "ask_user",
        "内部判断：需要用户选择。",
        decision={
            "decision_type": "ask_user",
            "expected_observation": "请选择 A 或 B。",
        },
    )
    await repo.update_agent_turn(turn.id, status="ask_user")
    await repo.set_waiting_state(run.id, {"paused_node": "build_contract", "request": "请选择"})
    waiting = await repo.require_run(run.id)
    waiting_view = RunViewProjector().payload(waiting)
    assert any(
        message["role"] == "assistant" and message["content"] == "请选择"
        for message in waiting_view["chat_messages"]
    )
    token = waiting.waiting_state["continuation_token"]
    resumed = await repo.resume_waiting_run(
        run.id,
        {"kind": "user_response", "status": "received", "summary": "选 A"},
        continuation_token=token,
    )
    assert resumed.status == "executing"
    assert resumed.state_version == 2
    assert resumed.agent_state["observations"][-1]["summary"] == "选 A"
    resumed_view = RunViewProjector().payload(await repo.require_run(run.id))
    dialogue = [
        (message["role"], message["content"])
        for message in resumed_view["chat_messages"]
        if message["role"] in {"user", "assistant"}
    ]
    assert dialogue == [
        ("user", "需要选择"),
        ("assistant", "请选择 A 或 B。"),
        ("user", "选 A"),
    ]
    assert all("内部判断" not in content for _, content in dialogue)


async def test_reasoning_state_rejects_stale_version(session):
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("版本测试", {"provider": "mock"})
    contract = build_default_contract("版本测试")
    state = AgentState(task_contract=contract)
    await repo.initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph={},
        agent_state=state.model_dump(mode="json"),
    )
    try:
        await repo.update_reasoning_state(
            run.id,
            expected_version=0,
            agent_state=state.model_copy(update={"version": 2}).model_dump(mode="json"),
        )
    except ValueError as exc:
        assert "version conflict" in str(exc)
    else:
        raise AssertionError("Expected stale state update rejection")
