from datetime import datetime, timedelta, timezone

from app.api.usage import _as_utc
from app.db.models import AgentTurnRecord, ToolCallRecord, utc_now
from app.repositories.runs import RunRepository
from app.repositories.usage import UsageRepository, normalize_usage


async def test_usage_summary_persists_exact_provider_tokens(session):
    run = await RunRepository(session).create_task_run("统计测试", {"provider": "openai"})
    usage = UsageRepository(session)
    invocation_id = await usage.create_invocation(
        run_id=run.id, provider="openai", model="gpt-5", operation="decision", attempt=1
    )
    await usage.finish_invocation(
        invocation_id,
        status="succeeded",
        duration_ms=125,
        request_id="request-1",
        usage={
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 20},
            "completion_tokens_details": {"reasoning_tokens": 10},
        },
    )
    session.add_all(
        [
            ToolCallRecord(run_id=run.id, tool_name="web_search", tool_version="1", input={}, status="succeeded", permission="network", side_effect_level="read", completed_at=utc_now()),
            ToolCallRecord(run_id=run.id, tool_name="web_search", tool_version="1", input={}, status="failed", permission="network", side_effect_level="read", completed_at=utc_now()),
            ToolCallRecord(run_id=run.id, tool_name="web_search", tool_version="1", input={}, status="running", permission="network", side_effect_level="read"),
            AgentTurnRecord(run_id=run.id, turn_index=1, decision_type="finalize", reasoning_summary="done"),
        ]
    )
    await session.commit()

    summary = await usage.summary(scope="task", task_id=run.task_id, run_id=None, from_time=utc_now() - timedelta(days=1), to_time=None)

    assert summary.overview.model_invocations == 1
    assert summary.tokens.total == 150
    assert summary.tokens.cached_input == 20
    assert summary.tokens.reasoning == 10
    assert summary.coverage.ratio == 1
    assert summary.overview.tool_success_rate == 0.5


def test_missing_usage_fields_remain_unknown():
    normalized = normalize_usage({"prompt_tokens": 12})
    assert normalized["input_tokens"] == 12
    assert normalized["output_tokens"] is None
    assert normalized["total_tokens"] is None


def test_usage_range_boundaries_are_normalized_to_utc():
    naive = datetime(2026, 7, 15, 8, 30)
    offset = datetime(2026, 7, 15, 16, 30, tzinfo=timezone(timedelta(hours=8)))

    assert _as_utc(naive) == datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)
    assert _as_utc(offset) == datetime(2026, 7, 15, 8, 30, tzinfo=timezone.utc)


async def test_reconcile_interrupted_invocations(session):
    run = await RunRepository(session).create_task_run("重启测试", {})
    usage = UsageRepository(session)
    await usage.create_invocation(run_id=run.id, provider="compatible", model="local", operation="plan", attempt=1)
    assert await usage.reconcile_interrupted() == 1
    summary = await usage.summary(scope="run", task_id=None, run_id=run.id, from_time=None, to_time=None)
    assert summary.overview.interrupted_invocations == 1
