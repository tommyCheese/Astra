from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.subagents.governance import stable_digest
from app.infrastructure.db.models.executions import (
    AgentExecutionRecord,
    AgentJoinRecord,
    ModelInvocationRecord,
)
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import RunEventRecord, RunRecord


@dataclass
class SubagentTelemetryRepository:
    """Builds content-free aggregate telemetry from durable execution records."""

    session: AsyncSession

    async def summary(self, run_id: str) -> dict[str, Any]:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        executions, events, invocations, tools, joins = await self._records(run_id)
        children = [item for item in executions if item.parent_execution_id is not None]
        return _telemetry_summary(run, children, events, invocations, tools, joins)

    async def _records(self, run_id):
        async def rows(model, *conditions):
            return list((await self.session.scalars(select(model).where(*conditions))).all())

        executions = await rows(AgentExecutionRecord, AgentExecutionRecord.run_id == run_id)
        events = await rows(RunEventRecord, RunEventRecord.run_id == run_id)
        invocations = await rows(
            ModelInvocationRecord,
            ModelInvocationRecord.run_id == run_id,
            ModelInvocationRecord.agent_execution_id.is_not(None),
        )
        tools = await rows(
            ToolCallRecord,
            ToolCallRecord.run_id == run_id,
            ToolCallRecord.agent_execution_id.is_not(None),
        )
        joins = await rows(AgentJoinRecord, AgentJoinRecord.run_id == run_id)
        return executions, events, invocations, tools, joins


def _telemetry_summary(run, children, events, invocations, tools, joins):
    policy = ((run.reasoning_policy or {}).get("effective") or {}).get("subagents") or {}
    return {
        "schema_version": 1,
        "run_id": run.id,
        "cohort": str(policy.get("rollout_cohort") or "disabled"),
        "profile": str(run.answer_mode or "standard"),
        "policy_digest": stable_digest(policy),
        "model": _model_identity(run),
        "delegation": _delegation_metrics(children, events),
        "outcomes": dict(sorted(Counter(item.status for item in children).items())),
        "joins": _join_metrics(joins),
        "usage": _usage_metrics(invocations, tools),
        "latency_ms": _percentiles(_durations(children)),
        "parallel_overlap_ms": _overlap_ms(children),
        **_terminal_event_metrics(children, events),
    }


def _model_identity(run):
    policy = run.model_policy or {}
    return {
        "provider": str(policy.get("provider") or "unknown"),
        "name": str(policy.get("model") or "unknown"),
    }


def _delegation_metrics(children, events):
    siblings = Counter(item.parent_execution_id for item in children)
    rejections = Counter(
        str(event.payload.get("reason_code") or "unknown") for event in events if event.type == "subagent.delegation_rejected"
    )
    scopes = [_scope_digest(item) for item in children]
    return {
        "accepted": len(children),
        "rejected": sum(rejections.values()),
        "rejection_reasons": dict(sorted(rejections.items())),
        "max_fan_out": max(siblings.values(), default=0),
        "max_depth": max((item.depth for item in children), default=0),
        "duplicate_scope_count": len(scopes) - len(set(scopes)),
    }


def _scope_digest(execution):
    request = (execution.contract or {}).get("request", {})
    return stable_digest({"scope": request.get("scope"), "tools": request.get("requested_tools")})


def _join_metrics(joins):
    return {"total": len(joins), "merge_failures": sum(item.status == "failed" for item in joins)}


def _usage_metrics(invocations, tools):
    return {
        "model_calls": len(invocations),
        "tool_calls": len(tools),
        "tokens": sum(item.total_tokens or 0 for item in invocations),
        "cost_usd": round(sum(float((item.raw_usage or {}).get("cost_usd") or 0) for item in invocations), 6),
    }


def _durations(children):
    return [
        max(0, int((item.finished_at - item.claimed_at).total_seconds() * 1000))
        for item in children
        if item.claimed_at is not None and item.finished_at is not None
    ]


def _terminal_event_metrics(children, events):
    return {
        "cancellation_count": sum(item.status == "cancelled" for item in children),
        "recovery_count": sum(event.type.startswith("subagent.recover") for event in events),
        "permission_denial_count": sum(event.type == "subagent.permission_denied" for event in events),
    }


def _percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {"p50": 0, "p95": 0, "max": 0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(len(ordered) * 0.95) - 1))
    return {"p50": int(median(ordered)), "p95": ordered[p95_index], "max": ordered[-1]}


def _overlap_ms(executions: list[AgentExecutionRecord]) -> int:
    boundaries: list[tuple[datetime, int]] = []
    for execution in executions:
        if execution.claimed_at is None or execution.finished_at is None:
            continue
        boundaries.append((execution.claimed_at, 1))
        boundaries.append((execution.finished_at, -1))
    boundaries.sort(key=lambda item: (item[0], item[1]))
    active = 0
    previous: datetime | None = None
    overlap = 0
    for timestamp, delta in boundaries:
        if previous is not None and active > 1:
            overlap += max(0, int((timestamp - previous).total_seconds() * 1000))
        active += delta
        previous = timestamp
    return overlap
