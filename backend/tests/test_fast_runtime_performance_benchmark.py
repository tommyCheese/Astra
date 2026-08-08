import pytest

from benchmarks.fast_runtime_performance import RuntimeSample, summarize


def sample(kind, case, first, total, model, tool, error=False, success=True):
    return RuntimeSample(case, 1, kind, first, total, model, tool, error, success)


def test_fast_legacy_summary_reports_rollout_dimensions():
    result = summarize(
        [
            sample("fast-v1", "direct", 40, 100, 1, 0),
            sample("legacy-standard-v1", "direct", 80, 160, 2, 0),
            sample("fast-v1", "tool", 50, 200, 2, 1),
            sample("legacy-standard-v1", "tool", 100, 250, 3, 1),
        ]
    )
    assert result["fast-v1"]["task_success_rate"] == 1
    assert result["comparison"] == {
        "first_token_ratio": 0.5,
        "total_latency_ratio": 0.7317,
        "model_call_delta": -1.0,
        "tool_call_delta": 0.0,
        "error_rate_delta": 0.0,
        "task_success_delta": 0.0,
    }


def test_fast_legacy_summary_rejects_unpaired_shadow_samples():
    with pytest.raises(ValueError, match="complete"):
        summarize([sample("fast-v1", "direct", 10, 20, 1, 0)])
