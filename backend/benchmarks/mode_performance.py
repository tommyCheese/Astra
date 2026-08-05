"""Compare Standard and Trusted Astra execution with paired, tool-free cases."""

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
class BenchmarkCase:
    id: str
    goal: str
    intent: str


@dataclass(frozen=True)
class ModeSample:
    case_id: str
    repetition: int
    answer_mode: str
    complete_ms: float
    model_invocations: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    usage_coverage: float


CASES = (
    BenchmarkCase(
        id="short_explanation",
        goal="用三句话解释什么是递归，并给出一个不超过两行的伪代码示例。",
        intent="短答案；验证固定治理开销在小任务中的占比。",
    ),
    BenchmarkCase(
        id="structured_comparison",
        goal="用一个四行以内的表格比较 Python 列表与元组，并给出一句选择建议。",
        intent="结构化答案；验证格式约束和完成校验的成本。",
    ),
    BenchmarkCase(
        id="bounded_checklist",
        goal="为代码评审前的自查写一份恰好五项的清单，每项不超过十五个字。",
        intent="有明确成功条件；便于可信模式生成并验证计划。",
    ),
)


def _summary(values: list[float | int]) -> dict[str, float]:
    if not values:
        raise ValueError("summary requires at least one value")
    numeric = [float(value) for value in values]
    return {
        "mean": round(sum(numeric) / len(numeric), 2),
        "p50": round(percentile(numeric, 0.5), 2),
        "p95": round(percentile(numeric, 0.95), 2),
    }


def _mode_summary(samples: list[ModeSample]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "model_invocations": _summary([sample.model_invocations for sample in samples]),
        "input_tokens": _summary([sample.input_tokens for sample in samples]),
        "cached_input_tokens": _summary(
            [sample.cached_input_tokens for sample in samples]
        ),
        "output_tokens": _summary([sample.output_tokens for sample in samples]),
        "reasoning_tokens": _summary([sample.reasoning_tokens for sample in samples]),
        "total_tokens": _summary([sample.total_tokens for sample in samples]),
        "complete_ms": _summary([sample.complete_ms for sample in samples]),
        "minimum_usage_coverage": round(
            min(sample.usage_coverage for sample in samples), 4
        ),
    }


def _comparison(standard: list[ModeSample], trusted: list[ModeSample]) -> dict[str, float]:
    standard_tokens = sum(sample.total_tokens for sample in standard)
    trusted_tokens = sum(sample.total_tokens for sample in trusted)
    standard_latency = sum(sample.complete_ms for sample in standard)
    trusted_latency = sum(sample.complete_ms for sample in trusted)
    if standard_tokens <= 0:
        raise ValueError("Standard samples must report positive total token usage")
    if standard_latency <= 0:
        raise ValueError("Standard samples must report positive completion latency")
    return {
        "trusted_token_ratio": round(trusted_tokens / standard_tokens, 4),
        "trusted_token_overhead_percent": round(
            (trusted_tokens - standard_tokens) / standard_tokens * 100, 2
        ),
        "trusted_latency_ratio": round(trusted_latency / standard_latency, 4),
        "trusted_latency_overhead_percent": round(
            (trusted_latency - standard_latency) / standard_latency * 100, 2
        ),
    }


def summarize_paired(samples: list[ModeSample]) -> dict[str, Any]:
    if not samples:
        raise ValueError("paired summary requires samples")
    modes = {
        mode: [sample for sample in samples if sample.answer_mode == mode]
        for mode in ("standard", "trusted")
    }
    standard_keys = {(sample.case_id, sample.repetition) for sample in modes["standard"]}
    trusted_keys = {(sample.case_id, sample.repetition) for sample in modes["trusted"]}
    unique_samples = {
        (sample.case_id, sample.repetition, sample.answer_mode) for sample in samples
    }
    if len(unique_samples) != len(samples):
        raise ValueError("paired summary does not allow duplicate mode samples")
    if not modes["standard"] or standard_keys != trusted_keys:
        raise ValueError("paired summary requires the same number of Standard and Trusted samples")

    case_ids = list(dict.fromkeys(sample.case_id for sample in samples))
    per_case = {}
    for case_id in case_ids:
        standard = [sample for sample in modes["standard"] if sample.case_id == case_id]
        trusted = [sample for sample in modes["trusted"] if sample.case_id == case_id]
        if not standard or len(standard) != len(trusted):
            raise ValueError(f"case {case_id} does not contain complete pairs")
        per_case[case_id] = {
            "standard": _mode_summary(standard),
            "trusted": _mode_summary(trusted),
            "comparison": _comparison(standard, trusted),
        }
    return {
        "standard": _mode_summary(modes["standard"]),
        "trusted": _mode_summary(modes["trusted"]),
        "comparison": _comparison(modes["standard"], modes["trusted"]),
        "cases": per_case,
    }


async def measure_mode(
    client: httpx.AsyncClient,
    *,
    case: BenchmarkCase,
    repetition: int,
    answer_mode: str,
    keep_run: bool,
    allow_incomplete_usage: bool,
) -> ModeSample:
    timing = RunTiming(started=time.perf_counter())
    create_request = {"goal": case.goal, "answer_mode": answer_mode}
    if answer_mode == "trusted":
        # A benchmark must run to completion without an interactive Plan confirmation pause.
        create_request["plan_execution"] = "auto"
    created = await client.post(
        "/api/runs",
        json=create_request,
    )
    created.raise_for_status()
    payload = created.json()
    run_id = payload["run_id"]
    task_id = payload["task_id"]
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
        if run.get("status") not in {"completed", "completed_with_warnings"}:
            raise RuntimeError(
                f"case {case.id} in {answer_mode} mode ended as {run.get('status')}"
            )

        usage_response = await client.get(
            "/api/usage/summary",
            params={"scope": "run", "run_id": run_id},
        )
        usage_response.raise_for_status()
        usage = usage_response.json()
        coverage = usage["coverage"]
        if not coverage["complete"] and not allow_incomplete_usage:
            raise RuntimeError(
                f"case {case.id} in {answer_mode} mode has incomplete provider token "
                f"coverage ({coverage['reported_invocations']}/{coverage['total_invocations']}); "
                "use a provider that reports usage or pass --allow-incomplete-usage"
            )
        tokens = usage["tokens"]
        return ModeSample(
            case_id=case.id,
            repetition=repetition,
            answer_mode=answer_mode,
            complete_ms=latency.complete_ms,
            model_invocations=usage["overview"]["model_invocations"],
            input_tokens=tokens["input"],
            cached_input_tokens=tokens["cached_input"],
            output_tokens=tokens["output"],
            reasoning_tokens=tokens["reasoning"],
            total_tokens=tokens["total"],
            usage_coverage=coverage["ratio"],
        )
    finally:
        if not keep_run:
            await cleanup_run(client, run_id, task_id)


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    selected = [case for case in CASES if args.case in {"all", case.id}]
    timeout = httpx.Timeout(args.timeout)
    samples: list[ModeSample] = []
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
    ) as client:
        for warmup in range(args.warmup):
            for mode in ("standard", "trusted"):
                await measure_mode(
                    client,
                    case=selected[warmup % len(selected)],
                    repetition=-(warmup + 1),
                    answer_mode=mode,
                    keep_run=args.keep_runs,
                    allow_incomplete_usage=args.allow_incomplete_usage,
                )
        for repetition in range(1, args.runs_per_case + 1):
            # Reverse every other pair so provider drift does not always favor one mode.
            mode_order = ("standard", "trusted") if repetition % 2 else ("trusted", "standard")
            for case in selected:
                for mode in mode_order:
                    samples.append(
                        await measure_mode(
                            client,
                            case=case,
                            repetition=repetition,
                            answer_mode=mode,
                            keep_run=args.keep_runs,
                            allow_incomplete_usage=args.allow_incomplete_usage,
                        )
                    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "method": "paired-sequential-alternating-order",
        "warmup_pairs": args.warmup,
        "runs_per_case": args.runs_per_case,
        "cases": [asdict(case) for case in selected],
        "summary": summarize_paired(samples),
        "samples": [asdict(sample) for sample in samples],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare Astra Standard and Trusted token usage and completion latency."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs-per-case", type=int, default=3)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--case", choices=("all", *(case.id for case in CASES)), default="all")
    parser.add_argument("--keep-runs", action="store_true")
    parser.add_argument("--allow-incomplete-usage", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.runs_per_case < 1 or args.warmup < 0:
        raise SystemExit("--runs-per-case must be positive and --warmup cannot be negative")
    result = asyncio.run(run_benchmark(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
