from dataclasses import replace

import pytest

from app.application.memory.tool_service import MemoryToolService
from app.application.permissions.effects import DefaultEffectAnalyzer
from app.common.schemas.permissions import EffectKind
from app.domain.memory import MemoryStatus
from app.infrastructure.repositories.memories import MemoryRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolExecutionContext, ToolExecutionError
from app.infrastructure.tools.memory import ForgetTool, RememberTool


async def _context(session, goal: str = "Manage memory"):
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run(goal, {"provider": "mock", "model": "mock"})
    call = await repository.start_tool_call(
        run.id,
        None,
        "remember",
        "1.0.0",
        {"content": "Astra uses governed tools"},
        "memory_write",
        "memory_write",
    )
    await repository.commit()
    context = ToolExecutionContext(
        run_id=run.id,
        tool_call_id=call.id,
        step_id=None,
        trace_id=f"{run.id}:{call.id}",
        artifact_service=None,
        sandbox_service=None,
        task_id=run.task_id,
        runtime_identity_id="agent-test",
        memory_service=MemoryToolService(repository, writes_enabled=True),
    )
    return repository, run, context


async def test_remember_creates_a_sourced_candidate_and_deduplicates(session):
    repository, run, context = await _context(session)
    tool_input = {
        "content": "Astra uses governed tools",
        "scope": "task",
        "kind": "semantic_fact",
        "memory_key": "astra:governed-tools",
        "confidence": 0.9,
        "importance": 0.7,
    }

    created = await RememberTool().run(tool_input, context=context)
    repeated = await RememberTool().run(tool_input, context=context)

    assert created["data"]["status"] == MemoryStatus.candidate.value
    assert created["data"]["deduplicated"] is False
    assert repeated["data"]["memory_id"] == created["data"]["memory_id"]
    assert repeated["data"]["deduplicated"] is True
    memory = await MemoryRepository(session).require(
        created["data"]["memory_id"], include_sources=True
    )
    assert {(source.source_kind, source.source_ref) for source in memory.sources} == {
        ("run", run.id),
        ("tool_call", context.tool_call_id),
    }
    events = await repository.list_events(run.id)
    assert [event.type for event in events][-2:] == [
        "memory.remembered",
        "memory.remember_deduplicated",
    ]


async def test_forget_revokes_accessible_memory_without_deleting_audit_data(session):
    _, _, context = await _context(session)
    created = await RememberTool().run(
        {"content": "Prefer concise answers", "kind": "user_preference"},
        context=context,
    )
    memory_id = created["data"]["memory_id"]

    forgotten = await ForgetTool().run(
        {"memory_id": memory_id, "reason": "User withdrew this preference"},
        context=context,
    )
    repeated = await ForgetTool().run(
        {"memory_id": memory_id, "reason": "Confirm revocation"},
        context=context,
    )

    assert forgotten["data"]["status"] == MemoryStatus.revoked.value
    assert forgotten["data"]["forgotten"] is True
    assert repeated["data"]["forgotten"] is False
    persisted = await MemoryRepository(session).require(memory_id, include_sources=True)
    assert persisted.content == "Prefer concise answers"
    assert persisted.revoke_reason == "User withdrew this preference"
    assert persisted.sources


async def test_memory_tools_reject_disabled_writes_authority_fields_and_cross_task_forget(
    session,
):
    repository, _, context = await _context(session, "First task")
    context = replace(
        context,
        memory_service=MemoryToolService(repository, writes_enabled=False),
    )
    with pytest.raises(ToolExecutionError) as disabled:
        await RememberTool().run({"content": "blocked"}, context=context)
    assert disabled.value.category == "invalid_memory"

    context = replace(
        context,
        memory_service=MemoryToolService(repository, writes_enabled=True),
    )
    with pytest.raises(ToolExecutionError) as protected:
        await RememberTool().run(
            {"content": "unsafe", "structured_data": {"permission": "allow"}},
            context=context,
        )
    assert protected.value.category == "invalid_memory"

    _, _, other_context = await _context(session, "Second task")
    other = await RememberTool().run({"content": "private to task two"}, context=other_context)
    with pytest.raises(ToolExecutionError) as boundary:
        await ForgetTool().run(
            {"memory_id": other["data"]["memory_id"], "reason": "Wrong task"},
            context=context,
        )
    assert boundary.value.category == "invalid_memory"


def test_memory_tools_have_explicit_approval_effects():
    analyzer = DefaultEffectAnalyzer()

    remember = analyzer.analyze(
        RememberTool.spec,
        {"content": "Keep this", "scope": "user", "memory_key": "preference:one"},
        task_id="task-1",
    )
    forget = analyzer.analyze(
        ForgetTool.spec,
        {"memory_id": "memory-1", "reason": "No longer wanted"},
        task_id="task-1",
    )

    assert remember.approval_required is True
    assert remember.effects[0].kind is EffectKind.memory_write
    assert remember.effects[0].resource == "memory://user/preference:one"
    assert forget.approval_required is True
    assert forget.effects[0].kind is EffectKind.memory_delete
    assert forget.effects[0].risk == "high"
