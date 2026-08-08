from datetime import timedelta

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.application.context_compaction.service import (
    AgentContextCompactionService,
    CompactionResult,
)
from app.application.run_management.conversation_commands import execute_system_command
from app.application.run_management.conversation_context import (
    ConversationContextManager,
    estimate_tokens,
)
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import AstraStateConflictError
from app.common.schemas.context_compaction import CompactionLifecycleStatus
from app.common.schemas.models import RunModelConfig
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.context_windows import resolve_context_window


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
    assert latest.documentation_url == ("https://developers.openai.com/api/docs/models/gpt-5.6-sol")

    deepseek = resolve_context_window("deepseek", "deepseek-v4-pro")
    assert deepseek.tokens == 1_000_000
    assert deepseek.max_output_tokens == 384_000
    assert deepseek.documentation_url == ("https://api-docs.deepseek.com/quick_start/pricing/")

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
    manager = ConversationContextManager(
        session,
        AstraRuntimeSettings(model_provider="mock", context_compaction_recent_tail_tokens=0),
    )

    before = await manager.projection(task)
    assert len(before.runs) == 7

    result = await manager.compact(task, retain_runs=2)
    assert result == {"folded": 5, "retained": 2, "model_used": True}
    compacted = await manager.projection(task)
    assert len(compacted.runs) == 2
    assert compacted.summary
    assert len(await manager.list_runs(task.id)) == 7
    rendered = await manager.render_goal(task, "继续")
    assert "Conversation checkpoint:" in rendered
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
    settings = AstraRuntimeSettings(
        model_provider="mock",
        context_window_fallback_tokens=65_536,
        context_auto_compact_ratio=0.8,
        context_compact_retain_runs=2,
        context_output_reserve_tokens=4_096,
        context_compaction_recent_tail_tokens=0,
        context_compaction_recovery_ratio=0.79,
    )
    manager = ConversationContextManager(session, settings)
    status = await manager.prepare_for_run(
        task,
        provider="unknown",
        model="small-private",
        draft="新问题" * 12_000,
    )
    assert task.context_state["last_action"] == "auto_compact"
    assert len(task.context_state["source_item_ids"]) == 12
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
    with pytest.raises(AstraStateConflictError):
        await manager.clear(task)


@pytest.mark.asyncio
async def test_v2_compaction_installs_semantic_checkpoint_and_preserves_audit_runs(session):
    task = await _conversation_with_runs(session)
    settings = AstraRuntimeSettings(
        model_provider="mock",
        context_compaction_v2_enabled=True,
        context_compaction_conversation_enabled=True,
        context_compaction_shadow_mode=False,
        context_compaction_recent_tail_tokens=0,
    )
    manager = ConversationContextManager(session, settings)

    result = await manager.compact(task, retain_runs=2, direction="保留约束和结论")

    assert result == {"folded": 5, "retained": 2, "model_used": True}
    assert task.context_state["version"] == 2
    assert task.context_state["window_number"] == 1
    assert task.context_state["compaction_implementation"] == "deterministic_emergency"
    assert task.context_state["checkpoint"]["checkpoint_role"] == "conversation"
    assert len(task.context_state["folded_run_ids"]) == 5
    assert len(await manager.list_runs(task.id)) == 7
    rendered = await manager.render_goal(task, "继续")
    assert rendered.count("Conversation checkpoint:") == 1


@pytest.mark.asyncio
async def test_v2_compaction_keeps_token_selected_recent_tail_visible(session):
    task = await _conversation_with_runs(session)
    manager = ConversationContextManager(
        session,
        AstraRuntimeSettings(
            model_provider="mock",
            context_compaction_v2_enabled=True,
            context_compaction_conversation_enabled=True,
            context_compaction_shadow_mode=False,
        ),
    )

    result = await manager.compact(task, retain_runs=2)

    assert result == {"folded": 0, "retained": 7, "model_used": True}
    assert len(task.context_state["retained_tail_ids"]) == 5
    assert task.context_state["folded_run_ids"] == []
    assert len((await manager.projection(task)).runs) == 7


@pytest.mark.asyncio
async def test_manual_compact_forces_checkpoint_for_any_completed_history(session):
    task = await _conversation_with_runs(session, count=1)
    manager = ConversationContextManager(
        session,
        AstraRuntimeSettings(
            model_provider="mock",
            context_auto_compact_ratio=0.95,
            context_compaction_v2_enabled=True,
            context_compaction_conversation_enabled=True,
            context_compaction_shadow_mode=False,
        ),
    )

    message, details, _ = await execute_system_command(manager, task, "compact")

    assert details == {"folded": 1, "retained": 0, "model_used": True, "direction": ""}
    assert message == "当前上下文已完成压缩。"
    assert task.context_state["checkpoint"]["checkpoint_role"] == "conversation"
    assert len((await manager.projection(task)).runs) == 0


@pytest.mark.asyncio
async def test_v2_compaction_discloses_classified_failure_without_changing_projection(
    session, monkeypatch
):
    task = await _conversation_with_runs(session)
    manager = ConversationContextManager(
        session,
        AstraRuntimeSettings(
            model_provider="mock",
            context_compaction_v2_enabled=True,
            context_compaction_conversation_enabled=True,
            context_compaction_shadow_mode=False,
        ),
    )
    visible_before = [run.id for run in (await manager.projection(task)).runs]

    async def fail_compaction(*_args, **_kwargs):
        return CompactionResult(
            status=CompactionLifecycleStatus.failed,
            checkpoint=None,
            retained_tail_ids=(),
            token_before=1_000,
            token_after=None,
            failure_code="checkpoint_schema_invalid",
        )

    monkeypatch.setattr(AgentContextCompactionService, "compact", fail_compaction)
    message, details, _ = await execute_system_command(manager, task, "compact")

    assert details["status"] == "failed"
    assert details["failure_code"] == "checkpoint_schema_invalid"
    assert "checkpoint_schema_invalid" in message
    assert task.context_state["compaction_failure_code"] == "checkpoint_schema_invalid"
    assert [run.id for run in (await manager.projection(task)).runs] == visible_before


@pytest.mark.asyncio
async def test_status_reports_adaptive_context_breakdown(session):
    task = await _conversation_with_runs(session, count=2)
    settings = AstraRuntimeSettings(model_provider="mock")
    status = await ConversationContextManager(session, settings).status(
        task,
        provider="openai",
        model="gpt-5",
        draft="继续分析",
    )

    breakdown = {item["kind"]: item for item in status["breakdown"]}
    assert breakdown["system"]["tokens"] == settings.context_system_reserve_tokens
    assert breakdown["conversation"]["item_count"] == 2
    assert breakdown["conversation"]["tokens"] > 0
    assert breakdown["draft"]["tokens"] > 0
    assert breakdown["output_reserve"]["tokens"] == settings.context_output_reserve_tokens
    assert (
        sum(item["tokens"] for item in status["breakdown"] if item["kind"] != "output_reserve")
        == status["used_tokens"]
    )
