import argparse
import asyncio

import pytest

from app.benchmarks.qa_latency import (
    LatencySample,
    iter_sse_payloads,
    percentile,
    run_benchmark,
    summarize,
)


async def test_sse_parser_handles_multiline_and_trailing_payloads():
    async def lines():
        for line in (
            'data: {"type":"stream.ready",',
            'data: "payload":{}}',
            "",
            'id: 3',
            'data: {"type":"answer.delta","payload":{"delta":"ok"}}',
        ):
            yield line

    payloads = [payload async for payload in iter_sse_payloads(lines())]

    assert [payload["type"] for payload in payloads] == [
        "stream.ready",
        "answer.delta",
    ]


def test_latency_summary_uses_nearest_rank_percentiles():
    samples = [
        LatencySample(
            submit_ms=float(index),
            stream_ready_ms=float(index + 1),
            visible_ttft_ms=float(index + 2),
            answer_ttft_ms=float(index + 3),
            complete_ms=float(index + 4),
        )
        for index in range(1, 21)
    ]

    assert percentile([sample.answer_ttft_ms for sample in samples], 0.5) == 13
    assert percentile([sample.answer_ttft_ms for sample in samples], 0.95) == 22
    assert summarize(samples)["answer_ttft_ms"] == {
        "min": 4.0,
        "p50": 13.0,
        "p95": 22.0,
        "max": 23.0,
    }


@pytest.mark.parametrize("values", [[], None])
def test_latency_summary_rejects_missing_samples(values):
    with pytest.raises((TypeError, ValueError)):
        summarize(values)


def test_benchmark_arguments_default_to_standard_mode():
    from app.benchmarks.qa_latency import build_parser

    args = build_parser().parse_args([])

    assert isinstance(args, argparse.Namespace)
    assert args.answer_mode == "standard"
    assert args.runs == 10
    assert args.concurrency == 1
    assert args.transport == "single"
    assert args.client_rtt_ms == 0


async def test_benchmark_bounds_parallel_runs(monkeypatch):
    from app.benchmarks import qa_latency

    active = 0
    peak = 0

    async def fake_measure(*_args, **_kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return LatencySample(1, 2, 3, 4, 5), {}

    monkeypatch.setattr(qa_latency, "measure_run", fake_measure)
    args = qa_latency.build_parser().parse_args(
        ["--runs", "5", "--warmup", "0", "--concurrency", "2"]
    )

    result = await run_benchmark(args)

    assert peak == 2
    assert result["concurrency"] == 2
    assert result["measured_runs"] == 5


async def test_benchmark_defers_cleanup_until_measurements_finish(monkeypatch):
    from app.benchmarks import qa_latency

    timeline = []

    async def fake_measure(*_args, cleanup_queue=None, **_kwargs):
        index = len(cleanup_queue)
        cleanup_queue.append((f"run-{index}", f"task-{index}"))
        timeline.append(f"measure-{index}")
        await asyncio.sleep(0)
        return LatencySample(1, 2, 3, 4, 5), {}

    async def fake_cleanup(_client, run_id, _task_id):
        timeline.append(f"cleanup-{run_id}")

    monkeypatch.setattr(qa_latency, "measure_run", fake_measure)
    monkeypatch.setattr(qa_latency, "cleanup_run", fake_cleanup)
    args = qa_latency.build_parser().parse_args(
        ["--runs", "3", "--warmup", "0", "--concurrency", "3"]
    )

    await run_benchmark(args)

    first_cleanup = next(index for index, item in enumerate(timeline) if item.startswith("cleanup"))
    assert all(item.startswith("measure") for item in timeline[:first_cleanup])
