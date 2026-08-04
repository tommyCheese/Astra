"""Measure end-to-end Astra streaming latency against a running backend."""

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
    visible_ttft_ms: float
    answer_ttft_ms: float
    complete_ms: float


@dataclass
class RunTiming:
    started: float
    submit_at: float | None = None
    ready_at: float | None = None
    first_visible_at: float | None = None
    first_answer_at: float | None = None
    completed_at: float | None = None

    def observe(self, event_type: str | None, now: float) -> None:
        if event_type == "stream.ready" and self.ready_at is None:
            self.ready_at = now
        if event_type in {"reasoning.summary.delta", "answer.delta"}:
            self.first_visible_at = self.first_visible_at or now
        if event_type == "answer.delta" and self.first_answer_at is None:
            self.first_answer_at = now
        if event_type == "answer.completed":
            self.completed_at = now

    def sample(self, run_id: str) -> LatencySample:
        required = {
            "stream.ready": self.ready_at,
            "visible output": self.first_visible_at,
            "answer.delta": self.first_answer_at,
            "answer.completed": self.completed_at,
        }
        missing = next((name for name, timestamp in required.items() if timestamp is None), None)
        if missing:
            raise RuntimeError(f"Run {run_id} ended without {missing}")
        if self.submit_at is None:
            raise RuntimeError(f"Run {run_id} ended without submission timestamp")
        return LatencySample(
            submit_ms=(self.submit_at - self.started) * 1000,
            stream_ready_ms=(self.ready_at - self.started) * 1000,
            visible_ttft_ms=(self.first_visible_at - self.started) * 1000,
            answer_ttft_ms=(self.first_answer_at - self.started) * 1000,
            complete_ms=(self.completed_at - self.started) * 1000,
        )


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
    transport: str = "split",
) -> tuple[LatencySample, dict[str, Any]]:
    if transport == "single":
        return await measure_streaming_run(
            client,
            goal=goal,
            answer_mode=answer_mode,
            keep_run=keep_run,
            cleanup_queue=cleanup_queue,
        )
    timing = RunTiming(started=time.perf_counter())
    created = await client.post(
        "/api/runs",
        json={"goal": goal, "answer_mode": answer_mode},
    )
    created.raise_for_status()
    create_payload = created.json()
    run_id = create_payload["run_id"]
    task_id = create_payload["task_id"]
    timing.submit_at = time.perf_counter()
    try:
        async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            response.raise_for_status()
            async for payload in iter_sse_payloads(response.aiter_lines()):
                timing.observe(payload.get("type"), time.perf_counter())
        sample = timing.sample(run_id)
        run_response = await client.get(f"/api/runs/{run_id}")
        run_response.raise_for_status()
        return sample, run_response.json().get("model_policy", {})
    finally:
        if not keep_run:
            if cleanup_queue is not None:
                cleanup_queue.append((run_id, task_id))
            else:
                await cleanup_run(client, run_id, task_id)


async def measure_streaming_run(
    client: httpx.AsyncClient,
    *,
    goal: str,
    answer_mode: str,
    keep_run: bool,
    cleanup_queue: list[tuple[str, str]] | None = None,
) -> tuple[LatencySample, dict[str, Any]]:
    timing = RunTiming(started=time.perf_counter())
    run_id = ""
    task_id = ""
    try:
        async with client.stream(
            "POST",
            "/api/runs/stream",
            json={"goal": goal, "answer_mode": answer_mode},
        ) as response:
            response.raise_for_status()
            timing.submit_at = time.perf_counter()
            async for payload in iter_sse_payloads(response.aiter_lines()):
                event_type = payload.get("type")
                timing.observe(event_type, time.perf_counter())
                if event_type == "stream.ready":
                    ready_payload = payload.get("payload", {})
                    if isinstance(ready_payload, dict):
                        run_id = str(ready_payload.get("run_id", ""))
                        task_id = str(ready_payload.get("task_id", ""))
        if not run_id or not task_id:
            raise RuntimeError("Single-stream run ended without creation metadata")
        sample = timing.sample(run_id)
        run_response = await client.get(f"/api/runs/{run_id}")
        run_response.raise_for_status()
        return sample, run_response.json().get("model_policy", {})
    finally:
        if run_id and task_id and not keep_run:
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

    async def simulate_client_rtt(_request: httpx.Request) -> None:
        await asyncio.sleep(args.client_rtt_ms / 1000)

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
        event_hooks=({"request": [simulate_client_rtt]} if args.client_rtt_ms else None),
    ) as client:
        for index in range(args.warmup):
            sample, measured_policy = await measure_run(
                client,
                goal=f"{args.goal}（基准轮次 {index + 1}）",
                answer_mode=args.answer_mode,
                keep_run=args.keep_runs,
                transport=args.transport,
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
                    transport=args.transport,
                )

        measured = await asyncio.gather(*(measure_index(index) for index in range(args.runs)))
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
        "transport": args.transport,
        "client_rtt_ms": args.client_rtt_ms,
        "model_policy": model_policy,
        "warmup_runs": args.warmup,
        "measured_runs": args.runs,
        "concurrency": args.concurrency,
        "metrics_ms": summarize(samples),
        "samples_ms": [asdict(sample) for sample in samples],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure Astra stream-ready, first visible output, answer TTFT, and completion latency."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--goal", default="用三句话解释递归，并给出一个简短示例。")
    parser.add_argument("--answer-mode", choices=("standard", "trusted"), default="standard")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--transport", choices=("split", "single"), default="single")
    parser.add_argument("--client-rtt-ms", type=float, default=0)
    parser.add_argument("--keep-runs", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.runs < 1 or args.warmup < 0 or args.concurrency < 1 or args.client_rtt_ms < 0:
        raise SystemExit(
            "--runs and --concurrency must be positive; warmup and client RTT cannot be negative"
        )
    result = asyncio.run(run_benchmark(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
