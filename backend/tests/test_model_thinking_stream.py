from app.application.agent_runtime.policies.reasoning import resolve_run_profile
from app.application.runner.model_thinking_stream import ModelThinkingEventWriter
from app.common.core.config import Settings
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.types import AnswerMode
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


async def _create_run(repo: RunUnitOfWork):
    settings = Settings(model_provider="mock")
    profile = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy())
    return await repo.create_task_run(
        "展示模型思考",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="standard",
        execution_profile=profile.model_dump(mode="json"),
    )


async def test_model_thinking_writer_persists_ordered_text_without_summary_contamination(session):
    repo = RunUnitOfWork(session)
    run = await _create_run(repo)
    writer = ModelThinkingEventWriter(repo, run.id)
    base = {
        "stream_id": "stream-1",
        "provider": "qwen",
        "model": "qwen3.7-plus",
        "operation": "decision_with_answer",
        "attempt": 1,
        "content_level": "reasoning",
    }

    await writer.accept({**base, "phase": "started"})
    await writer.accept({**base, "phase": "delta", "delta": "第一行\n"})
    await writer.accept({**base, "phase": "delta", "delta": "第二行"})
    await writer.accept({**base, "phase": "completed", "status": "completed"})

    events = await repo.list_events(run.id)
    thinking_events = [event for event in events if event.type.startswith("model_thinking.")]
    assert [event.type for event in thinking_events] == [
        "model_thinking.started",
        "model_thinking.delta",
        "model_thinking.delta",
        "model_thinking.completed",
    ]
    assert "".join(
        event.payload.get("delta", "") for event in thinking_events
    ) == "第一行\n第二行"
    assert thinking_events[-1].payload["char_count"] == 7
    assert all(event.type != "reasoning.summary.delta" for event in events)


async def test_model_thinking_writer_records_unavailable_without_sensitive_fields(session):
    repo = RunUnitOfWork(session)
    run = await _create_run(repo)
    writer = ModelThinkingEventWriter(repo, run.id)

    await writer.accept(
        {
            "phase": "unavailable",
            "stream_id": "stream-2",
            "provider": "openai",
            "model": "gpt-5.6",
            "operation": "synthesis",
            "attempt": 1,
            "content_level": "unavailable",
            "reason": "provider_did_not_return_visible_thinking",
            "signature": "must-not-persist",
            "prompt": "must-not-persist",
        }
    )

    event = (await repo.list_events(run.id))[-1]
    assert event.type == "model_thinking.unavailable"
    assert event.payload["reason"] == "provider_did_not_return_visible_thinking"
    assert "must-not-persist" not in str(event.payload)


async def test_model_thinking_writer_marks_truncation_explicitly(session, monkeypatch):
    monkeypatch.setattr(
        "app.application.runner.model_thinking_stream.MODEL_THINKING_MAX_CHARS_PER_INVOCATION", 5
    )
    monkeypatch.setattr("app.application.runner.model_thinking_stream.MODEL_THINKING_MAX_CHARS_PER_RUN", 5)
    repo = RunUnitOfWork(session)
    run = await _create_run(repo)
    writer = ModelThinkingEventWriter(repo, run.id)
    base = {
        "stream_id": "stream-3",
        "provider": "deepseek",
        "model": "deepseek-reasoner",
        "operation": "synthesis",
        "attempt": 1,
        "content_level": "reasoning",
    }

    await writer.accept({**base, "phase": "started"})
    await writer.accept({**base, "phase": "delta", "delta": "123456789"})
    await writer.accept({**base, "phase": "completed"})

    events = await repo.list_events(run.id)
    assert "".join(event.payload.get("delta", "") for event in events) == "12345"
    completed = next(event for event in events if event.type == "model_thinking.completed")
    assert completed.payload["truncated"] is True
    assert completed.payload["char_count"] == 5
