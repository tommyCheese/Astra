"""Offline Memory strategy evaluation used by benchmark-oriented tests."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.memory.domain import MemoryNamespace, MemoryNamespaceType, MemoryStatus
from app.memory.retrieval import (
    MemoryRetrievalBudget,
    MemoryRetrievalCandidate,
    MemoryRetrievalPolicy,
    MemoryRetrievalQuery,
    estimate_text_tokens,
    retrieve_memories,
)

EVALUATION_STRATEGIES = (
    "no_memory",
    "cross_session",
    "consolidation",
)


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _candidate(value: dict[str, Any]) -> MemoryRetrievalCandidate:
    return MemoryRetrievalCandidate(
        id=str(value["id"]),
        namespace_type=str(value["namespace_type"]),
        namespace_id=str(value["namespace_id"]),
        kind=str(value["kind"]),
        status=str(value.get("status", MemoryStatus.active.value)),
        content=str(value["content"]),
        structured_data=value.get("structured_data") or {},
        provenance=value.get("provenance") or {},
        confidence=float(value.get("confidence", 0.5)),
        importance=float(value.get("importance", 0.5)),
        utility_score=float(value.get("utility_score", 0.0)),
        version=int(value.get("version", 1)),
        observed_at=_datetime(value.get("observed_at")),
        valid_from=_datetime(value.get("valid_from")),
        valid_to=_datetime(value.get("valid_to")),
        expires_at=_datetime(value.get("expires_at")),
        revoked_at=_datetime(value.get("revoked_at")),
        updated_at=_datetime(value.get("updated_at")),
        accessible_source_count=int(value.get("accessible_source_count", 1)),
    )


@dataclass(frozen=True)
class MemoryEvaluationFixture:
    case_id: str
    query: str
    as_of: datetime
    namespaces: frozenset[MemoryNamespace]
    candidates: tuple[MemoryRetrievalCandidate, ...]
    consolidated_candidates: tuple[MemoryRetrievalCandidate, ...]
    relevant_ids: frozenset[str]
    required_ids: frozenset[str]
    stale_ids: frozenset[str]
    harmful_ids: frozenset[str]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MemoryEvaluationFixture:
        namespaces = frozenset(
            MemoryNamespace(
                MemoryNamespaceType(item["type"]),
                str(item["id"]),
            )
            for item in value["namespaces"]
        )
        candidates = tuple(_candidate(item) for item in value.get("candidates", []))
        consolidated = tuple(_candidate(item) for item in value.get("consolidated_candidates", []))
        return cls(
            case_id=str(value["case_id"]),
            query=str(value["query"]),
            as_of=_datetime(value["as_of"]),
            namespaces=namespaces,
            candidates=candidates,
            consolidated_candidates=consolidated or candidates,
            relevant_ids=frozenset(map(str, value.get("relevant_ids", []))),
            required_ids=frozenset(map(str, value.get("required_ids", []))),
            stale_ids=frozenset(map(str, value.get("stale_ids", []))),
            harmful_ids=frozenset(map(str, value.get("harmful_ids", []))),
        )


@dataclass(frozen=True)
class MemoryEvaluationObservation:
    case_id: str
    strategy: str
    selected_ids: tuple[str, ...]
    relevant_selected: int
    relevant_total: int
    token_cost: int
    latency_ms: float
    task_success: bool
    stale_use_count: int
    harmful_feedback_count: int
    namespace_leakage_count: int


@dataclass(frozen=True)
class MemoryStrategyMetrics:
    strategy: str
    case_count: int
    selected_count: int
    precision: float
    recall: float
    task_success_rate: float
    token_cost: int
    mean_latency_ms: float
    stale_use_count: int
    harmful_feedback_count: int
    namespace_leakage_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "case_count": self.case_count,
            "selected_count": self.selected_count,
            "precision": self.precision,
            "recall": self.recall,
            "task_success_rate": self.task_success_rate,
            "token_cost": self.token_cost,
            "mean_latency_ms": self.mean_latency_ms,
            "stale_use_count": self.stale_use_count,
            "harmful_feedback_count": self.harmful_feedback_count,
            "namespace_leakage_count": self.namespace_leakage_count,
        }


def load_evaluation_fixtures(path: str | Path) -> tuple[MemoryEvaluationFixture, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Memory evaluation fixture must contain non-empty cases")
    fixtures = tuple(MemoryEvaluationFixture.from_dict(item) for item in cases)
    if len({item.case_id for item in fixtures}) != len(fixtures):
        raise ValueError("Memory evaluation case IDs must be unique")
    return fixtures


def _observe(
    fixture: MemoryEvaluationFixture,
    strategy: str,
    selected: tuple[MemoryRetrievalCandidate, ...],
    latency_ms: float,
) -> MemoryEvaluationObservation:
    selected_ids = tuple(item.id for item in selected)
    selected_set = set(selected_ids)
    allowed_namespaces = {(namespace.type.value, namespace.id) for namespace in fixture.namespaces}
    relevant_selected = len(selected_set & fixture.relevant_ids)
    return MemoryEvaluationObservation(
        case_id=fixture.case_id,
        strategy=strategy,
        selected_ids=selected_ids,
        relevant_selected=relevant_selected,
        relevant_total=len(fixture.relevant_ids),
        token_cost=sum(estimate_text_tokens(item.content) for item in selected),
        latency_ms=max(0.0, latency_ms),
        task_success=fixture.required_ids.issubset(selected_set),
        stale_use_count=len(selected_set & fixture.stale_ids),
        harmful_feedback_count=len(selected_set & fixture.harmful_ids),
        namespace_leakage_count=sum(
            (item.namespace_type, item.namespace_id) not in allowed_namespaces for item in selected
        ),
    )


def evaluate_memory_strategies(
    fixtures: tuple[MemoryEvaluationFixture, ...],
    *,
    policy: MemoryRetrievalPolicy | None = None,
    budget: MemoryRetrievalBudget | None = None,
) -> dict[str, MemoryStrategyMetrics]:
    effective_policy = policy or MemoryRetrievalPolicy(minimum_score=0.05)
    effective_budget = budget or MemoryRetrievalBudget()
    observations = [
        _evaluate_strategy(fixture, strategy, effective_policy, effective_budget)
        for fixture in fixtures
        for strategy in EVALUATION_STRATEGIES
    ]
    return {
        strategy: _strategy_metrics(strategy, observations) for strategy in EVALUATION_STRATEGIES
    }


def _evaluate_strategy(fixture, strategy, policy, budget):
    started = time.perf_counter()
    selected: tuple[MemoryRetrievalCandidate, ...] = ()
    if strategy != "no_memory":
        candidates = (
            fixture.candidates if strategy == "cross_session" else fixture.consolidated_candidates
        )
        result = retrieve_memories(
            candidates,
            MemoryRetrievalQuery(
                text=fixture.query, namespaces=fixture.namespaces, as_of=fixture.as_of
            ),
            policy=policy,
            budget=budget,
        )
        selected = tuple(item.candidate for item in result.selected)
    return _observe(fixture, strategy, selected, (time.perf_counter() - started) * 1_000)


def _strategy_metrics(strategy, observations):
    rows = [item for item in observations if item.strategy == strategy]
    selected_count = sum(len(item.selected_ids) for item in rows)
    relevant_selected = sum(item.relevant_selected for item in rows)
    relevant_total = sum(item.relevant_total for item in rows)
    return MemoryStrategyMetrics(
        strategy=strategy,
        case_count=len(rows),
        selected_count=selected_count,
        precision=_ratio(relevant_selected, selected_count),
        recall=_ratio(relevant_selected, relevant_total),
        task_success_rate=_ratio(sum(item.task_success for item in rows), len(rows)),
        token_cost=sum(item.token_cost for item in rows),
        mean_latency_ms=_ratio(sum(item.latency_ms for item in rows), len(rows)),
        stale_use_count=sum(item.stale_use_count for item in rows),
        harmful_feedback_count=sum(item.harmful_feedback_count for item in rows),
        namespace_leakage_count=sum(item.namespace_leakage_count for item in rows),
    )


def _ratio(numerator, denominator) -> float:
    return numerator / denominator if denominator else 0.0
