from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.executions import ModelInvocationRecord
from app.db.models.memory import MemoryRecord
from app.db.models.permissions import ToolCallRecord
from app.db.models.runs import AgentTurnRecord, RunRecord
from app.db.models.workspaces import ArtifactRecord, SandboxJobRecord
from app.schemas.usage import (
    TokenTotals,
    UsageCoverage,
    UsageModelBreakdown,
    UsageOverview,
    UsageSummary,
    UsageToolBreakdown,
    UsageTrendPoint,
)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_scope_query(scope, task_id, run_id):
    query = select(RunRecord.id)
    if scope == "task":
        return query.where(RunRecord.task_id == task_id)
    if scope == "run":
        return query.where(RunRecord.id == run_id)
    return query


async def _usage_rows(session, model, time_column, run_ids, from_time, to_time):
    query = select(model).where(model.run_id.in_(run_ids))
    if from_time is not None:
        query = query.where(time_column >= from_time)
    if to_time is not None:
        query = query.where(time_column < to_time)
    return list((await session.scalars(query)).all())


def _usage_overview(invocations, turns, tools, memories, jobs, artifacts):
    terminal_tools = [item for item in tools if item.status in {"succeeded", "failed"}]
    succeeded_tools = sum(item.status == "succeeded" for item in terminal_tools)
    return UsageOverview(
        model_invocations=len(invocations),
        successful_invocations=sum(item.status == "succeeded" for item in invocations),
        failed_invocations=sum(item.status == "failed" for item in invocations),
        interrupted_invocations=sum(item.status == "interrupted" for item in invocations),
        agent_turns=len(turns),
        tool_calls=len(tools),
        successful_tool_calls=succeeded_tools,
        failed_tool_calls=sum(item.status == "failed" for item in terminal_tools),
        tool_success_rate=succeeded_tools / len(terminal_tools) if terminal_tools else None,
        memories=len(memories),
        sandbox_jobs=len(jobs),
        artifacts=len(artifacts),
        artifact_bytes=sum(item.size_bytes or 0 for item in artifacts),
    )


def _usage_trend(invocations, tools):
    days = defaultdict(lambda: {"invocations": 0, "tokens": 0, "tool_calls": 0})
    for item in invocations:
        day = _utc(item.created_at).date().isoformat()
        days[day]["invocations"] += 1
        days[day]["tokens"] += item.total_tokens or 0
    for item in tools:
        days[_utc(item.started_at).date().isoformat()]["tool_calls"] += 1
    return days


class UsageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_invocation(
        self,
        *,
        run_id: str,
        provider: str,
        model: str,
        operation: str,
        attempt: int,
        agent_execution_id: str | None = None,
    ) -> str:
        record = ModelInvocationRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            provider=provider,
            model=model,
            operation=operation,
            attempt=attempt,
            status="running",
        )
        self.session.add(record)
        await self.session.commit()
        return record.id

    async def finish_invocation(
        self,
        invocation_id: str,
        *,
        status: str,
        duration_ms: int,
        request_id: str | None = None,
        usage: dict[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        record = await self.session.get(ModelInvocationRecord, invocation_id)
        if record is None:
            return
        normalized = normalize_usage(usage)
        record.status = status
        record.completed_at = utc_now()
        record.duration_ms = duration_ms
        record.provider_request_id = request_id
        record.raw_usage = usage
        record.input_tokens = normalized["input_tokens"]
        record.cached_input_tokens = normalized["cached_input_tokens"]
        record.output_tokens = normalized["output_tokens"]
        record.reasoning_tokens = normalized["reasoning_tokens"]
        record.total_tokens = normalized["total_tokens"]
        if error is not None:
            record.error_type = type(error).__name__
            record.error_code = getattr(error, "code", None)
        await self.session.commit()

    async def reconcile_interrupted(self) -> int:
        result = await self.session.execute(
            update(ModelInvocationRecord)
            .where(ModelInvocationRecord.status == "running")
            .values(status="interrupted", completed_at=utc_now())
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def summary(
        self,
        *,
        scope: str,
        task_id: str | None,
        run_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
    ) -> UsageSummary:
        run_query = _run_scope_query(scope, task_id, run_id)
        run_ids = list((await self.session.scalars(run_query)).all())

        async def rows(model, time_column):
            return await _usage_rows(self.session, model, time_column, run_ids, from_time, to_time)

        invocations = await rows(ModelInvocationRecord, ModelInvocationRecord.created_at)
        turns = await rows(AgentTurnRecord, AgentTurnRecord.created_at)
        tools = await rows(ToolCallRecord, ToolCallRecord.started_at)
        memories = await rows(MemoryRecord, MemoryRecord.created_at)
        jobs = await rows(SandboxJobRecord, SandboxJobRecord.created_at)
        artifacts = await rows(ArtifactRecord, ArtifactRecord.created_at)

        token_totals = sum_tokens(invocations)
        reported = sum(1 for item in invocations if item.total_tokens is not None)
        overview = _usage_overview(invocations, turns, tools, memories, jobs, artifacts)
        model_groups: dict[tuple[str, str], list[ModelInvocationRecord]] = defaultdict(list)
        for item in invocations:
            model_groups[(item.provider, item.model)].append(item)
        tool_groups: dict[str, list[ToolCallRecord]] = defaultdict(list)
        for item in tools:
            tool_groups[item.tool_name].append(item)
        days = _usage_trend(invocations, tools)
        return UsageSummary(
            scope=scope,
            from_time=from_time,
            to_time=to_time,
            overview=overview,
            tokens=token_totals,
            coverage=UsageCoverage(
                reported_invocations=reported,
                total_invocations=len(invocations),
                ratio=reported / len(invocations) if invocations else 0,
                complete=bool(invocations) and reported == len(invocations),
            ),
            trend=[UsageTrendPoint(date=day, **values) for day, values in sorted(days.items())],
            models=[
                UsageModelBreakdown(
                    provider=key[0],
                    model=key[1],
                    invocations=len(items),
                    reported_invocations=sum(i.total_tokens is not None for i in items),
                    tokens=sum_tokens(items),
                )
                for key, items in sorted(model_groups.items())
            ],
            tools=[tool_breakdown(name, items) for name, items in sorted(tool_groups.items())],
        )


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, int | None]:
    usage = usage or {}
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = (
        usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
    )
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    cached = prompt_details.get("cached_tokens")
    reasoning = completion_details.get("reasoning_tokens")
    total = usage.get("total_tokens")
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
    }


def sum_tokens(items: list[ModelInvocationRecord]) -> TokenTotals:
    return TokenTotals(
        input=sum(item.input_tokens or 0 for item in items),
        cached_input=sum(item.cached_input_tokens or 0 for item in items),
        output=sum(item.output_tokens or 0 for item in items),
        reasoning=sum(item.reasoning_tokens or 0 for item in items),
        total=sum(item.total_tokens or 0 for item in items),
    )


def tool_breakdown(name: str, items: list[ToolCallRecord]) -> UsageToolBreakdown:
    succeeded = sum(item.status == "succeeded" for item in items)
    failed = sum(item.status == "failed" for item in items)
    terminal = succeeded + failed
    return UsageToolBreakdown(
        tool_name=name,
        calls=len(items),
        succeeded=succeeded,
        failed=failed,
        success_rate=succeeded / terminal if terminal else None,
    )
