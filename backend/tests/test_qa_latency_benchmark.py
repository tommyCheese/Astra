import argparse

import pytest

from app.benchmarks.qa_latency import (
    LatencySample,
    iter_sse_payloads,
    percentile,
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
            answer_ttft_ms=float(index + 2),
            complete_ms=float(index + 3),
        )
        for index in range(1, 21)
    ]

    assert percentile([sample.answer_ttft_ms for sample in samples], 0.5) == 12
    assert percentile([sample.answer_ttft_ms for sample in samples], 0.95) == 21
    assert summarize(samples)["answer_ttft_ms"] == {
        "min": 3.0,
        "p50": 12.0,
        "p95": 21.0,
        "max": 22.0,
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
