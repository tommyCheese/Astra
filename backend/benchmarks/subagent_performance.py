"""Paired single-Agent versus concurrent-subagent performance benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from benchmarks.qa_latency import RunTiming, cleanup_run, iter_sse_payloads, percentile


@dataclass(frozen=True)
class SubagentBenchmarkCase:
    id: str
    goal: str
    intent: str
    required_markers: tuple[str, ...]


@dataclass(frozen=True)
class SubagentSample:
    case_id: str
    repetition: int
    execution_mode: str
    complete_ms: float
    model_invocations: int
    total_tokens: int
    estimated_cost_usd: float
    quality_score: float
    quality_passed: bool
    successful: bool
    child_count: int
    usage_coverage: float
    failure_reason: str | None = None


CASES = (
    SubagentBenchmarkCase(
        id="breadth_research",
        goal=(
            "Assess a fictional developer tool launch from three independent lenses. "
            "When subagents are available, delegate MARKET, TECHNICAL, and OPERATIONS "
            "to separate children concurrently. Return exactly these headings: MARKET, "
            "TECHNICAL, OPERATIONS, SYNTHESIS. Keep the answer under 500 words."
        ),
        intent="Breadth research with three independent workstreams.",
        required_markers=("MARKET", "TECHNICAL", "OPERATIONS", "SYNTHESIS"),
    ),
    SubagentBenchmarkCase(
        id="independent_review",
        goal=(
            "Review a proposed password-reset service independently from security and "
            "reliability perspectives. When subagents are available, use separate children "
            "for the two reviews. Return exactly these headings: SECURITY REVIEW, "
            "RELIABILITY REVIEW, DISAGREEMENT, VERDICT. Keep the answer under 500 words."
        ),
        intent="Independent review that rewards diverse analysis and synthesis.",
        required_markers=(
            "SECURITY REVIEW",
            "RELIABILITY REVIEW",
            "DISAGREEMENT",
            "VERDICT",
        ),
    ),
)


def _summary(values: list[float | int]) -> dict[str, float]:
    numeric = [float(value) for value in values]
    if not numeric:
        raise ValueError("summary requires samples")
    return {
        "mean": round(sum(numeric) / len(numeric), 4),
        "p50": round(percentile(numeric, 0.5), 4),
        "p95": round(percentile(numeric, 0.95), 4),
    }


def _mode_summary(samples: list[SubagentSample]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "complete_ms": _summary([sample.complete_ms for sample in samples]),
        "model_invocations": _summary([sample.model_invocations for sample in samples]),
        "total_tokens": _summary([sample.total_tokens for sample in samples]),
        "estimated_cost_usd": _summary(
            [sample.estimated_cost_usd for sample in samples]
        ),
        "quality_score": _summary([sample.quality_score for sample in samples]),
        "quality_pass_rate": round(
            sum(sample.quality_passed for sample in samples) / len(samples), 4
        ),
        "failure_rate": round(
            sum(not sample.successful for sample in samples) / len(samples), 4
        ),
        "child_count": _summary([sample.child_count for sample in samples]),
        "minimum_usage_coverage": round(
            min(sample.usage_coverage for sample in samples), 4
        ),
    }


def _ratio(concurrent: float, single: float) -> float | None:
    return round(concurrent / single, 4) if single > 0 else None


def _comparison(single: list[SubagentSample], concurrent: list[SubagentSample]) -> dict[str, Any]:
    single_tokens = sum(sample.total_tokens for sample in single)
    concurrent_tokens = sum(sample.total_tokens for sample in concurrent)
    single_latency = sum(sample.complete_ms for sample in single)
    concurrent_latency = sum(sample.complete_ms for sample in concurrent)
    single_cost = sum(sample.estimated_cost_usd for sample in single)
    concurrent_cost = sum(sample.estimated_cost_usd for sample in concurrent)
    return {
        "concurrent_token_ratio": _ratio(concurrent_tokens, single_tokens),
        "concurrent_latency_ratio": _ratio(concurrent_latency, single_latency),
        "concurrent_cost_ratio": _ratio(concurrent_cost, single_cost),
        "quality_score_delta": round(
            sum(sample.quality_score for sample in concurrent) / len(concurrent)
            - sum(sample.quality_score for sample in single) / len(single),
            4,
        ),
        "failure_rate_delta": round(
            sum(not sample.successful for sample in concurrent) / len(concurrent)
            - sum(not sample.successful for sample in single) / len(single),
            4,
        ),
    }


def summarize_paired(samples: list[SubagentSample]) -> dict[str, Any]:
    modes = {
        mode: [sample for sample in samples if sample.execution_mode == mode]
        for mode in ("single_agent", "concurrent_subagent")
    }
    keys = {
        mode: {(sample.case_id, sample.repetition) for sample in values}
        for mode, values in modes.items()
    }
    if not modes["single_agent"] or keys["single_agent"] != keys["concurrent_subagent"]:
        raise ValueError("paired summary requires identical single and concurrent samples")
    if len({(s.case_id, s.repetition, s.execution_mode) for s in samples}) != len(samples):
        raise ValueError("paired summary does not allow duplicate samples")
    return {
        "single_agent": _mode_summary(modes["single_agent"]),
        "concurrent_subagent": _mode_summary(modes["concurrent_subagent"]),
        "comparison": _comparison(modes["single_agent"], modes["concurrent_subagent"]),
    }


async def _set_swarm(client: httpx.AsyncClient, enabled: bool) -> None:
    response = await client.put("/api/tools", json={"swarm": enabled})
    response.raise_for_status()
    state = next(item for item in response.json()["tools"] if item["name"] == "swarm")
    if state["enabled"] is not enabled or (enabled and not state["available"]):
        raise RuntimeError(state.get("unavailable_reason") or "Swarm setting did not apply")


async def _current_swarm(client: httpx.AsyncClient) -> bool:
    response = await client.get("/api/tools")
    response.raise_for_status()
    return bool(next(item for item in response.json()["tools"] if item["name"] == "swarm")["enabled"])


def _quality(case: SubagentBenchmarkCase, run: dict[str, Any]) -> float:
    text = json.dumps(run.get("result") or {}, ensure_ascii=False).upper()
    return round(sum(marker in text for marker in case.required_markers) / len(case.required_markers), 4)


async def measure_mode(
    client: httpx.AsyncClient,
    *,
    case: SubagentBenchmarkCase,
    repetition: int,
    execution_mode: str,
    keep_run: bool,
    allow_incomplete_usage: bool,
    input_cost_per_million: float,
    cached_input_cost_per_million: float,
    output_cost_per_million: float,
) -> SubagentSample:
    concurrent = execution_mode == "concurrent_subagent"
    await _set_swarm(client, concurrent)
    timing = RunTiming(started=time.perf_counter())
    created = await client.post(
        "/api/runs",
        json={
            "goal": case.goal,
            "answer_mode": "trusted",
            "plan_execution": "auto",
            "subagent_mode": "required" if concurrent else "auto",
        },
    )
    created.raise_for_status()
    run_id, task_id = created.json()["run_id"], created.json()["task_id"]
    timing.submit_at = time.perf_counter()
    try:
        async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            response.raise_for_status()
            async for event in iter_sse_payloads(response.aiter_lines()):
                timing.observe(event.get("type"), time.perf_counter())
        latency = timing.sample(run_id)
        run_response = await client.get(f"/api/runs/{run_id}")
        run_response.raise_for_status()
        run = run_response.json()
        usage_response = await client.get(
            "/api/usage/summary", params={"scope": "run", "run_id": run_id}
        )
        usage_response.raise_for_status()
        usage = usage_response.json()
        coverage = usage["coverage"]
        if not coverage["complete"] and not allow_incomplete_usage:
            raise RuntimeError("provider token usage coverage is incomplete")
        tokens = usage["tokens"]
        child_count = int((run.get("subagent_summary") or {}).get("total", 0))
        mode_compliant = child_count >= 2 if concurrent else child_count == 0
        terminal_success = run.get("status") in {"completed", "completed_with_warnings"}
        score = _quality(case, run)
        cost = (
            max(0, tokens["input"] - tokens["cached_input"]) * input_cost_per_million
            + tokens["cached_input"] * cached_input_cost_per_million
            + tokens["output"] * output_cost_per_million
        ) / 1_000_000
        successful = terminal_success and mode_compliant
        reason = None
        if not terminal_success:
            reason = f"terminal_status:{run.get('status')}"
        elif not mode_compliant:
            reason = f"mode_noncompliance:child_count={child_count}"
        return SubagentSample(
            case.id,
            repetition,
            execution_mode,
            latency.complete_ms,
            usage["overview"]["model_invocations"],
            tokens["total"],
            round(cost, 8),
            score,
            successful and score == 1.0,
            successful,
            child_count,
            coverage["ratio"],
            reason,
        )
    finally:
        if not keep_run:
            await cleanup_run(client, run_id, task_id)


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    selected = [case for case in CASES if args.case in {"all", case.id}]
    samples: list[SubagentSample] = []
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(args.timeout)
    ) as client:
        original_swarm = await _current_swarm(client)
        try:
            for repetition in range(1, args.runs_per_case + 1):
                order = (
                    ("single_agent", "concurrent_subagent")
                    if repetition % 2
                    else ("concurrent_subagent", "single_agent")
                )
                for case in selected:
                    for mode in order:
                        samples.append(
                            await measure_mode(
                                client,
                                case=case,
                                repetition=repetition,
                                execution_mode=mode,
                                keep_run=args.keep_runs,
                                allow_incomplete_usage=args.allow_incomplete_usage,
                                input_cost_per_million=args.input_cost_per_million,
                                cached_input_cost_per_million=args.cached_input_cost_per_million,
                                output_cost_per_million=args.output_cost_per_million,
                            )
                        )
        finally:
            await _set_swarm(client, original_swarm)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "paired-sequential-alternating-order",
        "cases": [asdict(case) for case in selected],
        "pricing_usd_per_million_tokens": {
            "input": args.input_cost_per_million,
            "cached_input": args.cached_input_cost_per_million,
            "output": args.output_cost_per_million,
        },
        "summary": summarize_paired(samples),
        "samples": [asdict(sample) for sample in samples],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs-per-case", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--case", choices=("all", *(case.id for case in CASES)), default="all")
    parser.add_argument("--keep-runs", action="store_true")
    parser.add_argument("--allow-incomplete-usage", action="store_true")
    parser.add_argument("--input-cost-per-million", type=float, required=True)
    parser.add_argument("--cached-input-cost-per-million", type=float, required=True)
    parser.add_argument("--output-cost-per-million", type=float, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.runs_per_case < 1 or min(
        args.input_cost_per_million,
        args.cached_input_cost_per_million,
        args.output_cost_per_million,
    ) < 0:
        raise SystemExit("runs and pricing must be non-negative; runs must be positive")
    print(json.dumps(asyncio.run(run_benchmark(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
