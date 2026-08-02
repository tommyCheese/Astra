from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentExecutionRecord,
    AgentJoinRecord,
    ModelInvocationRecord,
    RunEventRecord,
    RunRecord,
    ToolCallRecord,
)
from app.subagents.governance import stable_digest


class SubagentTelemetryRepository:
    """Builds content-free aggregate telemetry from durable execution records."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def summary(self, run_id: str) -> dict[str, Any]:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        executions = list(
            (
                await self.session.scalars(
                    select(AgentExecutionRecord).where(
                        AgentExecutionRecord.run_id == run_id
                    )
                )
            ).all()
        )
        children = [item for item in executions if item.parent_execution_id is not None]
        events = list(
            (
                await self.session.scalars(
                    select(RunEventRecord).where(RunEventRecord.run_id == run_id)
                )
            ).all()
        )
        invocations = list(
            (
                await self.session.scalars(
                    select(ModelInvocationRecord).where(
                        ModelInvocationRecord.run_id == run_id,
                        ModelInvocationRecord.agent_execution_id.is_not(None),
                    )
                )
            ).all()
        )
        tools = list(
            (
                await self.session.scalars(
                    select(ToolCallRecord).where(
                        ToolCallRecord.run_id == run_id,
                        ToolCallRecord.agent_execution_id.is_not(None),
                    )
                )
            ).all()
        )
        joins = list(
            (
                await self.session.scalars(
                    select(AgentJoinRecord).where(AgentJoinRecord.run_id == run_id)
                )
            ).all()
        )
        policy = (((run.reasoning_policy or {}).get("effective") or {}).get("subagents") or {})
        siblings = Counter(item.parent_execution_id for item in children)
        durations = [
            max(0, int((item.finished_at - item.claimed_at).total_seconds() * 1000))
            for item in children
            if item.claimed_at is not None and item.finished_at is not None
        ]
        outcome_counts = Counter(item.status for item in children)
        rejection_counts = Counter(
            str(event.payload.get("reason_code") or "unknown")
            for event in events
            if event.type == "subagent.delegation_rejected"
        )
        scopes = [
            stable_digest(
                {
                    "scope": (item.contract or {}).get("request", {}).get("scope"),
                    "tools": (item.contract or {}).get("request", {}).get("requested_tools"),
                }
            )
            for item in children
        ]
        total_tokens = sum(item.total_tokens or 0 for item in invocations)
        total_cost = sum(
            float((item.raw_usage or {}).get("cost_usd") or 0) for item in invocations
        )
        return {
            "schema_version": 1,
            "run_id": run.id,
            "cohort": str(policy.get("rollout_cohort") or "disabled"),
            "profile": str(run.answer_mode or "standard"),
            "policy_digest": stable_digest(policy),
            "model": {
                "provider": str((run.model_policy or {}).get("provider") or "unknown"),
                "name": str((run.model_policy or {}).get("model") or "unknown"),
            },
            "delegation": {
                "accepted": len(children),
                "rejected": sum(rejection_counts.values()),
                "rejection_reasons": dict(sorted(rejection_counts.items())),
                "max_fan_out": max(siblings.values(), default=0),
                "max_depth": max((item.depth for item in children), default=0),
                "duplicate_scope_count": len(scopes) - len(set(scopes)),
            },
            "outcomes": dict(sorted(outcome_counts.items())),
            "joins": {
                "total": len(joins),
                "merge_failures": sum(item.status == "failed" for item in joins),
            },
            "usage": {
                "model_calls": len(invocations),
                "tool_calls": len(tools),
                "tokens": total_tokens,
                "cost_usd": round(total_cost, 6),
            },
            "latency_ms": _percentiles(durations),
            "parallel_overlap_ms": _overlap_ms(children),
            "cancellation_count": sum(item.status == "cancelled" for item in children),
            "recovery_count": sum(
                event.type.startswith("subagent.recover") for event in events
            ),
            "permission_denial_count": sum(
                event.type == "subagent.permission_denied" for event in events
            ),
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


@dataclass(frozen=True)
class BenchmarkResult:
    quality: float
    latency_ms: int
    tokens: int
    cost_usd: float
    failure_rate: float = 0
    recovery_rate: float = 0
    cancellation_p95_ms: int = 0
    safety_failures: int = 0


@dataclass(frozen=True)
class ReleaseThresholds:
    minimum_quality_delta: float = 0
    maximum_latency_ratio: float = 2.0
    maximum_token_ratio: float = 3.0
    maximum_cost_ratio: float = 3.0
    maximum_failure_rate: float = 0.05
    minimum_recovery_rate: float = 0.99
    maximum_cancellation_p95_ms: int = 2_000
    maximum_safety_failures: int = 0


@dataclass(frozen=True)
class ReleaseGateDecision:
    passed: bool
    reasons: tuple[str, ...]
    activate_kill_switch: bool


def evaluate_release_gate(
    *,
    baseline: BenchmarkResult,
    candidate: BenchmarkResult,
    thresholds: ReleaseThresholds = ReleaseThresholds(),
) -> ReleaseGateDecision:
    reasons: list[str] = []
    if candidate.quality - baseline.quality < thresholds.minimum_quality_delta:
        reasons.append("quality_regression")
    if _ratio(candidate.latency_ms, baseline.latency_ms) > thresholds.maximum_latency_ratio:
        reasons.append("latency_regression")
    if _ratio(candidate.tokens, baseline.tokens) > thresholds.maximum_token_ratio:
        reasons.append("token_regression")
    if _ratio(candidate.cost_usd, baseline.cost_usd) > thresholds.maximum_cost_ratio:
        reasons.append("cost_regression")
    if candidate.failure_rate > thresholds.maximum_failure_rate:
        reasons.append("failure_rate_exceeded")
    if candidate.recovery_rate < thresholds.minimum_recovery_rate:
        reasons.append("recovery_rate_below_gate")
    if candidate.cancellation_p95_ms > thresholds.maximum_cancellation_p95_ms:
        reasons.append("cancellation_latency_exceeded")
    if candidate.safety_failures > thresholds.maximum_safety_failures:
        reasons.append("safety_failure")
    return ReleaseGateDecision(
        passed=not reasons,
        reasons=tuple(reasons),
        activate_kill_switch=bool(
            candidate.safety_failures > thresholds.maximum_safety_failures
            or candidate.failure_rate > thresholds.maximum_failure_rate * 2
        ),
    )


def _ratio(candidate: int | float, baseline: int | float) -> float:
    if baseline <= 0:
        return 1.0 if candidate <= 0 else float("inf")
    return float(candidate) / float(baseline)


ROLLOUT_STAGES = (
    "shadow",
    "administrator_canary",
    "trusted_read_only",
    "general",
)


@dataclass(frozen=True)
class RolloutState:
    stage: str = "shadow"
    kill_switch: bool = False

    def promote(self, decision: ReleaseGateDecision) -> RolloutState:
        if self.kill_switch or not decision.passed:
            raise ValueError("Rollout cannot advance without passing release gates")
        index = ROLLOUT_STAGES.index(self.stage)
        return RolloutState(ROLLOUT_STAGES[min(index + 1, len(ROLLOUT_STAGES) - 1)])

    def rollback(self) -> RolloutState:
        return RolloutState(stage="shadow", kill_switch=True)


DELEGATION_BEHAVIOR_CASES: tuple[dict[str, Any], ...] = (
    {"id": "breadth_research", "should_delegate": True, "kind": "positive"},
    {"id": "multi_source_comparison", "should_delegate": True, "kind": "positive"},
    {"id": "independent_file_review", "should_delegate": True, "kind": "positive"},
    {"id": "alternative_analysis", "should_delegate": True, "kind": "positive"},
    {"id": "simple_question", "should_delegate": False, "kind": "negative"},
    {"id": "strong_sequential_workflow", "should_delegate": False, "kind": "negative"},
    {"id": "shared_write_heavy", "should_delegate": False, "kind": "negative"},
    {"id": "low_budget", "should_delegate": False, "kind": "negative"},
    {"id": "high_risk_effect", "should_delegate": False, "kind": "negative"},
)


def evaluate_delegation_behavior(
    predictions: dict[str, bool],
) -> dict[str, Any]:
    cases = {item["id"]: item for item in DELEGATION_BEHAVIOR_CASES}
    missing = sorted(set(cases) - set(predictions))
    incorrect = sorted(
        case_id
        for case_id, prediction in predictions.items()
        if case_id in cases and prediction != cases[case_id]["should_delegate"]
    )
    return {
        "passed": not missing and not incorrect,
        "total": len(cases),
        "correct": len(cases) - len(missing) - len(incorrect),
        "missing": missing,
        "incorrect": incorrect,
    }
