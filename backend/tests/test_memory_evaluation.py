from pathlib import Path

from app.memory.evaluation import (
    EVALUATION_STRATEGIES,
    evaluate_memory_strategies,
    load_evaluation_fixtures,
)
from app.memory.retrieval import MemoryRetrievalBudget, MemoryRetrievalPolicy

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "deep_memory_retrieval_cases.json"


def test_fixed_memory_evaluation_compares_all_rollout_strategies_and_metrics():
    fixtures = load_evaluation_fixtures(FIXTURE_PATH)

    report = evaluate_memory_strategies(
        fixtures,
        policy=MemoryRetrievalPolicy(minimum_confidence=0.5, minimum_score=0),
        budget=MemoryRetrievalBudget(
            max_items=1,
            max_characters=2_000,
            max_tokens=500,
        ),
    )

    assert tuple(report) == EVALUATION_STRATEGIES
    assert all(metric.case_count == 2 for metric in report.values())
    assert report["no_memory"].selected_count == 0
    assert report["no_memory"].task_success_rate == 0
    assert report["cross_session"].task_success_rate == 1
    assert report["cross_session"].recall == 1
    assert report["consolidation"].task_success_rate == 1
    assert report["consolidation"].precision == 1
    assert all(metric.namespace_leakage_count == 0 for metric in report.values())
    assert all(metric.token_cost >= 0 for metric in report.values())
    assert all(metric.mean_latency_ms >= 0 for metric in report.values())


def test_fixture_loader_rejects_duplicate_case_ids(tmp_path):
    duplicate = tmp_path / "duplicates.json"
    duplicate.write_text(
        """
        {
          "cases": [
            {
              "case_id": "same",
              "query": "q",
              "as_of": "2026-07-30T00:00:00Z",
              "legacy_run_id": "run",
              "namespaces": [{"type": "run", "id": "run"}]
            },
            {
              "case_id": "same",
              "query": "q",
              "as_of": "2026-07-30T00:00:00Z",
              "legacy_run_id": "run",
              "namespaces": [{"type": "run", "id": "run"}]
            }
          ]
        }
        """,
        encoding="utf-8",
    )

    try:
        load_evaluation_fixtures(duplicate)
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate case IDs must be rejected")
