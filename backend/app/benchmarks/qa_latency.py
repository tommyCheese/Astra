from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


@dataclass(frozen=True)
class LatencySample:
    submit_ms: float
    stream_ready_ms: float
    answer_ttft_ms: float
    complete_ms: float


async def iter_sse_payloads(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    data_lines: list[str] = []
    async for line in lines:
        if line == "":
            if data_lines:
                try:
                    payload = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    yield payload
                data_lines.clear()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            yield payload


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def summarize(samples: list[LatencySample]) -> dict[str, dict[str, float]]:
    if not samples:
        raise ValueError("summary requires at least one sample")
    result: dict[str, dict[str, float]] = {}
    for field in LatencySample.__dataclass_fields__:
        values = [getattr(sample, field) for sample in samples]
        result[field] = {
            "min": round(min(values), 2),
            "p50": round(percentile(values, 0.5), 2),
            "p95": round(percentile(values, 0.95), 2),
            "max": round(max(values), 2),
        }
    return result


async def measure_run(
    client: httpx.AsyncClient,
    *,
    goal: str,
    answer_mode: str,
    keep_run: bool,
    cleanup_queue: list[tuple[str, str]] | None = None,
) -> tuple[LatencySample, dict[str, Any]]:
    started = time.perf_counter()
    created = await client.post(
        "/api/runs",
        json={"goal": goal, "answer_mode": answer_mode},
    )
    created.raise_for_status()
    create_payload = created.json()
    run_id = create_payload["run_id"]
    task_id = create_payload["task_id"]
    submit_at = time.perf_counter()
    ready_at: float | None = None
    first_answer_at: float | None = None
    completed_at: float | None = None
    try:
        async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            response.raise_for_status()
            async for payload in iter_sse_payloads(response.aiter_lines()):
                now = time.perf_counter()
                event_type = payload.get("type")
                if event_type == "stream.ready" and ready_at is None:
                    ready_at = now
                elif event_type == "answer.delta" and first_answer_at is None:
                    first_answer_at = now
                elif event_type == "answer.completed":
                    completed_at = now
        ended = completed_at or time.perf_counter()
        if ready_at is None:
            raise RuntimeError(f"Run {run_id} ended without stream.ready")
        if first_answer_at is None:
            raise RuntimeError(f"Run {run_id} ended without answer.delta")
        if completed_at is None:
            raise RuntimeError(f"Run {run_id} ended without answer.completed")
        sample = LatencySample(
            submit_ms=(submit_at - started) * 1000,
            stream_ready_ms=(ready_at - started) * 1000,
            answer_ttft_ms=(first_answer_at - started) * 1000,
            complete_ms=(ended - started) * 1000,
        )
        run_response = await client.get(f"/api/runs/{run_id}")
        run_response.raise_for_status()
        return sample, run_response.json().get("model_policy", {})
    finally:
        if not keep_run:
            if cleanup_queue is not None:
                cleanup_queue.append((run_id, task_id))
            else:
                await cleanup_run(client, run_id, task_id)


async def cleanup_run(
    client: httpx.AsyncClient,
    run_id: str,
    task_id: str,
) -> None:
    cancel = await client.post(f"/api/runs/{run_id}/cancel")
    cancel.raise_for_status()
    cleanup = await client.delete(f"/api/conversations/{task_id}")
    cleanup.raise_for_status()


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=max(4, args.concurrency * 2),
        max_keepalive_connections=max(4, args.concurrency),
    )
    samples: list[LatencySample] = []
    model_policy: dict[str, Any] = {}
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
    ) as client:
        for index in range(args.warmup):
            sample, measured_policy = await measure_run(
                client,
                goal=f"{args.goal}（基准轮次 {index + 1}）",
                answer_mode=args.answer_mode,
                keep_run=args.keep_runs,
            )
            if not model_policy:
                model_policy = measured_policy

        semaphore = asyncio.Semaphore(args.concurrency)
        deferred_cleanups: list[tuple[str, str]] = []

        async def measure_index(index: int):
            async with semaphore:
                return await measure_run(
                    client,
                    goal=f"{args.goal}（基准轮次 {args.warmup + index + 1}）",
                    answer_mode=args.answer_mode,
                    keep_run=args.keep_runs,
                    cleanup_queue=deferred_cleanups,
                )

        measured = await asyncio.gather(
            *(measure_index(index) for index in range(args.runs))
        )
        samples.extend(sample for sample, _ in measured)
        if not model_policy:
            model_policy = next(
                (policy for _, policy in measured if policy),
                {},
            )
        for run_id, task_id in deferred_cleanups:
            await cleanup_run(client, run_id, task_id)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "goal": args.goal,
        "answer_mode": args.answer_mode,
        "model_policy": model_policy,
        "warmup_runs": args.warmup,
        "measured_runs": args.runs,
        "concurrency": args.concurrency,
        "metrics_ms": summarize(samples),
        "samples_ms": [asdict(sample) for sample in samples],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure Astra create-to-stream-ready, answer TTFT, and completion latency."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--goal", default="用三句话解释递归，并给出一个简短示例。")
    parser.add_argument("--answer-mode", choices=("standard", "trusted"), default="standard")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--keep-runs", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.runs < 1 or args.warmup < 0 or args.concurrency < 1:
        raise SystemExit(
            "--runs and --concurrency must be positive and --warmup cannot be negative"
        )
    result = asyncio.run(run_benchmark(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
