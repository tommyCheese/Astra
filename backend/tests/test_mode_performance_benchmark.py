import json

import httpx
import pytest

from benchmarks.mode_performance import (
    BenchmarkCase,
    ModeSample,
    build_parser,
    measure_mode,
    summarize_paired,
)


def sample(case_id: str, mode: str, repetition: int, tokens: int, latency: float):
    return ModeSample(
        case_id=case_id,
        repetition=repetition,
        answer_mode=mode,
        complete_ms=latency,
        model_invocations=1 if mode == "standard" else 3,
        input_tokens=tokens - 20,
        cached_input_tokens=0,
        output_tokens=20,
        reasoning_tokens=0,
        total_tokens=tokens,
        usage_coverage=1.0,
    )


def test_paired_summary_reports_token_and_latency_overhead():
    samples = [
        sample("explain", "standard", 1, 100, 10),
        sample("explain", "trusted", 1, 250, 15),
        sample("checklist", "standard", 1, 200, 20),
        sample("checklist", "trusted", 1, 350, 30),
    ]

    result = summarize_paired(samples)

    assert result["standard"]["total_tokens"]["mean"] == 150
    assert result["trusted"]["model_invocations"]["mean"] == 3
    assert result["comparison"] == {
        "trusted_token_ratio": 2.0,
        "trusted_token_overhead_percent": 100.0,
        "trusted_latency_ratio": 1.5,
        "trusted_latency_overhead_percent": 50.0,
    }
    assert result["cases"]["explain"]["comparison"]["trusted_token_ratio"] == 2.5


def test_paired_summary_rejects_unpaired_modes():
    with pytest.raises(ValueError, match="same number"):
        summarize_paired([sample("explain", "standard", 1, 100, 10)])


def test_paired_summary_rejects_mismatched_repetitions():
    with pytest.raises(ValueError, match="same number"):
        summarize_paired(
            [
                sample("explain", "standard", 1, 100, 10),
                sample("explain", "trusted", 2, 200, 20),
            ]
        )


def test_paired_benchmark_defaults_to_friendly_case_suite():
    args = build_parser().parse_args([])

    assert args.case == "all"
    assert args.runs_per_case == 3
    assert args.warmup == 1
    assert not args.allow_incomplete_usage


@pytest.mark.parametrize("answer_mode", ["standard", "trusted"])
async def test_measure_mode_uses_same_case_and_reads_run_usage(answer_mode):
    create_payloads = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/runs":
            create_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"run_id": "run-1", "task_id": "task-1"})
        if request.method == "GET" and request.url.path == "/api/runs/run-1/events":
            events = (
                '{"type":"stream.ready"}',
                '{"type":"answer.delta"}',
                '{"type":"answer.completed"}',
            )
            return httpx.Response(200, text="".join(f"data: {event}\n\n" for event in events))
        if request.method == "GET" and request.url.path == "/api/runs/run-1":
            return httpx.Response(200, json={"status": "completed"})
        if request.method == "GET" and request.url.path == "/api/usage/summary":
            assert dict(request.url.params) == {"scope": "run", "run_id": "run-1"}
            return httpx.Response(
                200,
                json={
                    "overview": {"model_invocations": 3},
                    "tokens": {
                        "input": 100,
                        "cached_input": 10,
                        "output": 20,
                        "reasoning": 5,
                        "total": 120,
                    },
                    "coverage": {
                        "complete": True,
                        "reported_invocations": 3,
                        "total_invocations": 3,
                        "ratio": 1.0,
                    },
                },
            )
        if request.method == "POST" and request.url.path == "/api/runs/run-1/cancel":
            return httpx.Response(200, json={})
        if request.method == "DELETE" and request.url.path == "/api/conversations/task-1":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    case = BenchmarkCase("same-case", "同一个友好任务", "配对验证")
    async with httpx.AsyncClient(
        base_url="http://test",
        transport=httpx.MockTransport(handler),
    ) as client:
        measured = await measure_mode(
            client,
            case=case,
            repetition=1,
            answer_mode=answer_mode,
            keep_run=False,
            allow_incomplete_usage=False,
        )

    assert create_payloads == [
        {
            "goal": case.goal,
            "answer_mode": answer_mode,
            **({"plan_execution": "auto"} if answer_mode == "trusted" else {}),
        }
    ]
    assert measured.total_tokens == 120
    assert measured.model_invocations == 3
    assert measured.usage_coverage == 1.0
