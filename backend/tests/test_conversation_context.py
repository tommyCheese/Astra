from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.conversation_context import (
    ConversationContextManager,
    estimate_tokens,
    resolve_context_window,
)
from app.core.config import Settings
from app.core.errors import StateError
from app.db.models import RunRecord, TaskRecord, utc_now
from app.schemas.models import RunModelConfig


def test_context_window_resolution_and_estimation_are_model_aware():
    assert resolve_context_window("openai", "gpt-5").tokens == 400_000
    assert resolve_context_window("anthropic", "claude-sonnet-4").tokens == 200_000
    assert resolve_context_window("custom", "private-model").tokens == 131_072
    assert estimate_tokens("这是一段中文上下文") >= 8
    assert estimate_tokens("a" * 320) == 100


def test_context_window_resolution_tracks_official_catalog_and_fallback_metadata():
    latest = resolve_context_window("openai", "gpt-5.6-sol")
    assert latest.tokens == 1_050_000
    assert latest.max_output_tokens == 128_000
    assert latest.source == "catalog"
    assert latest.verified is True
    assert latest.documentation_url == (
        "https://developers.openai.com/api/docs/models/gpt-5.6-sol"
    )

    deepseek = resolve_context_window("deepseek", "deepseek-v4-pro")
    assert deepseek.tokens == 1_000_000
    assert deepseek.max_output_tokens == 384_000
    assert deepseek.documentation_url == (
        "https://api-docs.deepseek.com/quick_start/pricing/"
    )

    grok = resolve_context_window("xai", "grok-4.5")
    assert grok.tokens == 500_000
    assert grok.source == "catalog"

    fallback = resolve_context_window("compatible", "private-model")
    assert fallback.tokens == 131_072
    assert fallback.source == "fallback"
    assert fallback.verified is False
    assert fallback.documentation_url is None


def test_run_model_config_rejects_client_context_overrides():
    with pytest.raises(PydanticValidationError):
        RunModelConfig.model_validate(
            {
                "provider": "openai",
                "name": "gpt-5",
                "context": {
                    "mode": "manual",
                    "window_tokens": 65_536,
                    "max_output_tokens": 4_096,
                },
            }
        )


async def _conversation_with_runs(session, count: int = 7) -> TaskRecord:
    now = utc_now()
    task = TaskRecord(
        title="上下文测试",
        description="第一条",
        status="created",
        preferred_answer_mode="standard",
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    await session.flush()
    for index in range(count):
        session.add(
            RunRecord(
                task_id=task.id,
                status="completed",
                mode="web_agent",
                answer_mode="standard",
                model_policy={"conversation_goal": f"问题 {index} " + "内容" * 300},
                summary=f"回答 {index} " + "结论" * 300,
                created_at=now + timedelta(seconds=index),
                updated_at=now + timedelta(seconds=index),
            )
        )
    await session.commit()
    return task


@pytest.mark.asyncio
async def test_compact_and_clear_change_projection_without_deleting_runs(session):
    task = await _conversation_with_runs(session)
    manager = ConversationContextManager(session, Settings(model_provider="mock"))

    before = await manager.projection(task)
    assert len(before.runs) == 7

    result = await manager.compact(task, retain_runs=2)
    assert result == {"folded": 5, "retained": 2}
    compacted = await manager.projection(task)
    assert len(compacted.runs) == 2
    assert compacted.summary
    assert len(await manager.list_runs(task.id)) == 7
    rendered = await manager.render_goal(task, "继续")
    assert "Earlier conversation summary:" in rendered
    assert "Current user request: 继续" in rendered

    cleared = await manager.clear(task)
    assert cleared == {"cleared": 7}
    projection = await manager.projection(task)
    assert not projection.runs
    assert projection.summary == ""
    assert len(await manager.list_runs(task.id)) == 7


@pytest.mark.asyncio
async def test_automatic_compaction_and_active_run_guard(session):
    task = await _conversation_with_runs(session, count=12)
    settings = Settings(
        model_provider="mock",
        context_window_fallback_tokens=16_384,
        context_auto_compact_ratio=0.5,
        context_compact_retain_runs=2,
        context_summary_max_chars=2_000,
        context_output_reserve_tokens=4_096,
    )
    manager = ConversationContextManager(session, settings)
    status = await manager.prepare_for_run(
        task,
        provider="unknown",
        model="small-private",
        draft="新问题" * 300,
    )
    assert task.context_state["last_action"] == "auto_compact"
    assert status["usage_ratio"] < 1
    assert status["summary_active"] is True

    now = utc_now()
    session.add(
        RunRecord(
            task_id=task.id,
            status="executing",
            mode="web_agent",
            answer_mode="standard",
            model_policy={"conversation_goal": "执行中"},
            created_at=now,
            updated_at=now,
        )
    )
    await session.commit()
    with pytest.raises(StateError):
        await manager.clear(task)
