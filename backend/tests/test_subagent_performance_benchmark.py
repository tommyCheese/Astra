import json

import httpx
import pytest

from benchmarks.subagent_performance import (
    SubagentBenchmarkCase,
    SubagentSample,
    measure_mode,
    summarize_paired,
)


def _sample(mode, *, tokens, latency, cost, quality, success, children):
    return SubagentSample(
        "case",
        1,
        mode,
        latency,
        3,
        tokens,
        cost,
        quality,
        quality == 1 and success,
        success,
        children,
        1.0,
    )


def test_summary_reports_cost_quality_failure_and_overheads():
    result = summarize_paired(
        [
            _sample("single_agent", tokens=100, latency=20, cost=0.01, quality=0.5, success=True, children=0),
            _sample("concurrent_subagent", tokens=200, latency=10, cost=0.03, quality=1, success=False, children=2),
        ]
    )
    assert result["comparison"] == {
        "concurrent_token_ratio": 2.0,
        "concurrent_latency_ratio": 0.5,
        "concurrent_cost_ratio": 3.0,
        "quality_score_delta": 0.5,
        "failure_rate_delta": 1.0,
    }
    assert result["concurrent_subagent"]["failure_rate"] == 1.0
    assert result["single_agent"]["child_count"]["mean"] == 0


@pytest.mark.parametrize(
    ("mode", "subagent_mode", "swarm", "children"),
    [("single_agent", "auto", False, 0), ("concurrent_subagent", "required", True, 2)],
)
async def test_measure_mode_enforces_paired_execution_boundary(
    mode, subagent_mode, swarm, children
):
    create_payloads = []
    tool_updates = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT" and request.url.path == "/api/tools":
            update = json.loads(request.content)
            tool_updates.append(update)
            return httpx.Response(
                200,
                json={"tools": [{"name": "swarm", "enabled": update["swarm"], "available": True}]},
            )
        if request.method == "POST" and request.url.path == "/api/runs":
            create_payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"run_id": "run-1", "task_id": "task-1"})
        if request.method == "GET" and request.url.path == "/api/runs/run-1/events":
            events = (
                '{"type":"stream.ready"}',
                '{"type":"answer.delta"}',
                '{"type":"answer.completed"}',
            )
            return httpx.Response(
                200, text="".join(f"data: {event}\n\n" for event in events)
            )
        if request.method == "GET" and request.url.path == "/api/runs/run-1":
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "result": {"summary": "ALPHA BETA"},
                    "subagent_summary": {"total": children},
                },
            )
        if request.method == "GET" and request.url.path == "/api/usage/summary":
            return httpx.Response(
                200,
                json={
                    "overview": {"model_invocations": 3},
                    "tokens": {"input": 100, "cached_input": 20, "output": 50, "reasoning": 0, "total": 150},
                    "coverage": {"complete": True, "ratio": 1.0},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    case = SubagentBenchmarkCase("case", "same goal", "paired", ("ALPHA", "BETA"))
    async with httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    ) as client:
        sample = await measure_mode(
            client,
            case=case,
            repetition=1,
            execution_mode=mode,
            keep_run=False,
            allow_incomplete_usage=False,
            input_cost_per_million=10,
            cached_input_cost_per_million=2,
            output_cost_per_million=20,
        )

    assert tool_updates == [{"swarm": swarm}]
    assert create_payloads[0]["subagent_mode"] == subagent_mode
    assert create_payloads[0]["goal"] == case.goal
    assert sample.successful and sample.quality_passed
    assert sample.estimated_cost_usd == 0.00184
