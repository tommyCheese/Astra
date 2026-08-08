"""Paired fast-v1 versus legacy-standard-v1 rollout benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from benchmarks.mode_performance import CASES
from benchmarks.qa_latency import cleanup_run, iter_sse_payloads, percentile


@dataclass(frozen=True)
class RuntimeSample:
    case_id: str
    repetition: int
    runtime_kind: str
    first_token_ms: float
    total_ms: float
    model_calls: int
    tool_calls: int
    error: bool
    success: bool


def _stats(values: list[float | int]) -> dict[str, float]:
    numeric = [float(item) for item in values]
    return {
        "mean": round(sum(numeric) / len(numeric), 2),
        "p50": round(percentile(numeric, 0.5), 2),
        "p95": round(percentile(numeric, 0.95), 2),
    }


def _runtime_summary(samples: list[RuntimeSample]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "first_token_ms": _stats([item.first_token_ms for item in samples]),
        "total_ms": _stats([item.total_ms for item in samples]),
        "model_calls": _stats([item.model_calls for item in samples]),
        "tool_calls": _stats([item.tool_calls for item in samples]),
        "error_rate": round(sum(item.error for item in samples) / len(samples), 4),
        "task_success_rate": round(sum(item.success for item in samples) / len(samples), 4),
    }


def summarize(samples: list[RuntimeSample]) -> dict[str, Any]:
    grouped = {
        kind: [item for item in samples if item.runtime_kind == kind]
        for kind in ("fast-v1", "legacy-standard-v1")
    }
    fast_keys = {(item.case_id, item.repetition) for item in grouped["fast-v1"]}
    legacy_keys = {
        (item.case_id, item.repetition) for item in grouped["legacy-standard-v1"]
    }
    if not fast_keys or fast_keys != legacy_keys:
        raise ValueError("benchmark requires complete fast-v1 and legacy-standard-v1 pairs")
    fast = _runtime_summary(grouped["fast-v1"])
    legacy = _runtime_summary(grouped["legacy-standard-v1"])
    return {
        "fast-v1": fast,
        "legacy-standard-v1": legacy,
        "comparison": {
            "first_token_ratio": round(
                fast["first_token_ms"]["mean"] / legacy["first_token_ms"]["mean"], 4
            ),
            "total_latency_ratio": round(
                fast["total_ms"]["mean"] / legacy["total_ms"]["mean"], 4
            ),
            "model_call_delta": round(
                fast["model_calls"]["mean"] - legacy["model_calls"]["mean"], 2
            ),
            "tool_call_delta": round(
                fast["tool_calls"]["mean"] - legacy["tool_calls"]["mean"], 2
            ),
            "error_rate_delta": round(fast["error_rate"] - legacy["error_rate"], 4),
            "task_success_delta": round(
                fast["task_success_rate"] - legacy["task_success_rate"], 4
            ),
        },
    }


async def measure(
    client: httpx.AsyncClient,
    *,
    case_id: str,
    goal: str,
    repetition: int,
    expected_runtime: str,
    keep_run: bool,
) -> RuntimeSample:
    started = time.perf_counter()
    created = await client.post("/api/runs", json={"goal": goal, "answer_mode": "standard"})
    created.raise_for_status()
    identity = created.json()
    run_id, task_id = identity["run_id"], identity["task_id"]
    first_token: float | None = None
    try:
        async with client.stream("GET", f"/api/runs/{run_id}/events") as response:
            response.raise_for_status()
            async for event in iter_sse_payloads(response.aiter_lines()):
                if event.get("type") == "answer.delta" and first_token is None:
                    first_token = time.perf_counter()
        finished = time.perf_counter()
        view_response = await client.get(f"/api/runs/{run_id}")
        view_response.raise_for_status()
        view = view_response.json()
        usage_response = await client.get(
            "/api/usage/summary", params={"scope": "run", "run_id": run_id}
        )
        usage_response.raise_for_status()
        usage = usage_response.json()
        if view.get("runtime_kind") != expected_runtime:
            raise RuntimeError(
                f"expected {expected_runtime}, received {view.get('runtime_kind')}"
            )
        status = str(view.get("status"))
        return RuntimeSample(
            case_id=case_id,
            repetition=repetition,
            runtime_kind=expected_runtime,
        first_token_ms=((first_token if first_token is not None else finished) - started) * 1000,
            total_ms=(finished - started) * 1000,
            model_calls=int((usage.get("overview") or {}).get("model_invocations", 0)),
            tool_calls=len(view.get("tool_calls") or []),
            error=status in {"failed", "blocked"},
            success=status in {"completed", "completed_with_warnings"},
        )
    finally:
        if not keep_run:
            await cleanup_run(client, run_id, task_id)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    samples: list[RuntimeSample] = []
    deployments = (
        (args.fast_base_url, "fast-v1"),
        (args.legacy_base_url, "legacy-standard-v1"),
    )
    clients = {
        kind: httpx.AsyncClient(base_url=url.rstrip("/"), timeout=args.timeout)
        for url, kind in deployments
    }
    try:
        for repetition in range(1, args.runs_per_case + 1):
            for case in CASES:
                order = deployments if repetition % 2 else tuple(reversed(deployments))
                for _, kind in order:
                    samples.append(
                        await measure(
                            clients[kind],
                            case_id=case.id,
                            goal=case.goal,
                            repetition=repetition,
                            expected_runtime=kind,
                            keep_run=args.keep_runs,
                        )
                    )
    finally:
        for client in clients.values():
            await client.aclose()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "paired-alternating-two-deployment-shadow",
        "summary": summarize(samples),
        "samples": [asdict(item) for item in samples],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--legacy-base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--runs-per-case", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--keep-runs", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.runs_per_case < 1:
        raise SystemExit("--runs-per-case must be positive")
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
