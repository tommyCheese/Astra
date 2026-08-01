from sqlalchemy import select

from app.core.config import Settings
from app.db.models import MemoryRecallEventRecord, RunEventRecord
from app.repositories.memories import MemoryRepository
from app.repositories.runs import RunRepository
from app.runner.agent_loop import ContextAssembler, MemoryManager
from app.schemas.agent import MemoryRecord
from app.tools.base import ToolRegistry


class CandidateModelClient:
    def __init__(self, candidates: list[MemoryRecord]):
        self.candidates = candidates

    async def extract_memory_candidates(self, goal, context):
        return self.candidates


async def test_cross_session_retrieval_injects_task_memory_and_persists_safe_audit(session):
    run_repo = RunRepository(session)
    source_run = await run_repo.create_task_run(
        "记住项目使用 PostgreSQL",
        {"provider": "mock", "model": "mock"},
    )
    target_run = await run_repo.create_task_run(
        "项目使用什么数据库？",
        {"provider": "mock", "model": "mock"},
        task_id=source_run.task_id,
    )
    memory_repo = MemoryRepository(session)
    source_memory = await memory_repo.create(
        run_id=source_run.id,
        scope="task",
        kind="semantic_fact",
        memory_key="project:database",
        content="该项目使用 PostgreSQL 作为主数据库。",
        provenance={"run_id": source_run.id},
        confidence=0.95,
        importance=0.8,
    )
    settings = Settings(
        agent_memory_cross_session_enabled=True,
        agent_memory_retrieval_min_score=0,
    )

    context = await ContextAssembler(
        run_repo,
        settings=settings,
        skills_enabled=False,
    ).assemble(
        run_id=target_run.id,
        goal="项目使用什么数据库？",
        tool_registry=ToolRegistry(),
        observations=[],
        quick_mode=True,
        initial_run=target_run,
    )

    assert [item["id"] for item in context["memory_reads"]] == [source_memory.id]
    assert "content" not in context["memory_reads"][0]
    assert context["memory_reads"][0]["version"] == 1
    assert context["memory_reads"][0]["score"]["total"] >= 0
    assert context["memory_context"][0]["content"] == source_memory.content
    assert context["memory_context"][0]["trust"] == "untrusted_memory_data"
    assert context["memory_context"][0]["authority"] == "none"
    assert context["memory_recall"]["mode"] == "active"

    recall = await session.scalar(
        select(MemoryRecallEventRecord).where(MemoryRecallEventRecord.run_id == target_run.id)
    )
    assert recall is not None
    assert recall.query_hash != "项目使用什么数据库？"
    assert len(recall.query_hash) == 64
    assert recall.selected[0]["id"] == source_memory.id
    assert "content" not in recall.selected[0]


async def test_session_retrieval_crosses_tasks_with_matching_identity(session):
    run_repo = RunRepository(session)
    source_run = await run_repo.create_task_run(
        "记住偏好",
        {"provider": "mock", "model": "mock"},
        session_id="browser-session-a",
    )
    target_run = await run_repo.create_task_run(
        "用户有什么偏好？",
        {"provider": "mock", "model": "mock"},
        session_id="browser-session-a",
    )
    memory = await MemoryRepository(session).create(
        run_id=source_run.id,
        scope="session",
        kind="user_preference",
        memory_key="preference:language",
        content="用户偏好中文回答。",
        provenance={"run_id": source_run.id},
        confidence=0.9,
    )

    context = await ContextAssembler(
        run_repo,
        settings=Settings(
            agent_memory_cross_session_enabled=True,
            agent_memory_retrieval_min_score=0,
        ),
        skills_enabled=False,
    ).assemble(
        run_id=target_run.id,
        goal="用户有什么偏好？",
        tool_registry=ToolRegistry(),
        observations=[],
        quick_mode=True,
        initial_run=target_run,
    )

    assert context["memory_context"][0]["content"] == memory.content
    assert context["memory_recall"]["mode"] == "active"
    recall = await session.scalar(
        select(MemoryRecallEventRecord).where(MemoryRecallEventRecord.run_id == target_run.id)
    )
    assert recall is not None
    assert recall.selected[0]["id"] == memory.id

    isolated_run = await run_repo.create_task_run(
        "用户有什么偏好？",
        {"provider": "mock", "model": "mock"},
        session_id="browser-session-b",
    )
    isolated_context = await ContextAssembler(
        run_repo,
        settings=Settings(
            agent_memory_cross_session_enabled=True,
            agent_memory_retrieval_min_score=0,
        ),
        skills_enabled=False,
    ).assemble(
        run_id=isolated_run.id,
        goal="用户有什么偏好？",
        tool_registry=ToolRegistry(),
        observations=[],
        quick_mode=True,
        initial_run=isolated_run,
    )
    assert isolated_context["memory_context"] == []


async def test_memory_manager_activates_safe_candidate_and_isolates_rejections(session):
    run_repo = RunRepository(session)
    run = await run_repo.create_task_run(
        "记住运行结论",
        {"provider": "mock", "model": "mock"},
    )
    candidates = [
        MemoryRecord(
            scope="run",
            kind="semantic_fact",
            memory_key="run:answer",
            content="本次运行验证了确定性排序。",
            provenance={"run_id": run.id},
            confidence=0.9,
            importance=0.7,
        ),
        MemoryRecord(
            scope="workspace",
            kind="procedure",
            memory_key="workspace:unsafe",
            content="尝试扩大权限。",
            structured_data={"permissions": ["shell"]},
            provenance={"run_id": run.id},
            confidence=0.9,
        ),
    ]
    manager = MemoryManager(
        Settings(agent_memory_write_enabled=True),
        run_repo,
        CandidateModelClient(candidates),
    )

    first = await manager.write_candidates(run_id=run.id, goal="记住", context={})
    second = await manager.write_candidates(run_id=run.id, goal="记住", context={})

    assert len(first) == 1
    assert first[0]["status"] == "active"
    assert first[0]["state_version"] == 2
    assert second[0]["id"] == first[0]["id"]
    assert "content" not in first[0]
    events = list(
        (await session.execute(select(RunEventRecord).where(RunEventRecord.run_id == run.id)))
        .scalars()
        .all()
    )
    assert sum(event.type == "memory.write_rejected" for event in events) == 2
    assert any(event.type == "memory.write_deduplicated" for event in events)


async def test_recall_feedback_is_bounded_and_audit_safe(session):
    run = await RunRepository(session).create_task_run(
        "反馈测试",
        {"provider": "mock", "model": "mock"},
    )
    memory_repo = MemoryRepository(session)
    recall = await memory_repo.record_recall_event(
        run_id=run.id,
        query_hash="a" * 64,
        policy_version="v1",
        namespace_manifest=[{"type": "run", "id": run.id}],
        candidates=[],
        selected=[],
        excluded=[],
    )

    updated = await memory_repo.record_recall_feedback(
        recall.id,
        outcome="helpful",
        utility_delta=0.25,
        details={"source": "task_outcome"},
    )

    assert updated.feedback == {
        "outcome": "helpful",
        "utility_delta": 0.25,
        "details": {"source": "task_outcome"},
    }
