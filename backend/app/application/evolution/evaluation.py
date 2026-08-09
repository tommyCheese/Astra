from __future__ import annotations

import sys

from app.domain.evolution import (
    EvaluationDecision,
    EvaluationIssue,
    EvaluationManifest,
    EvaluationThresholds,
)


def evaluate_manifest(
    manifest: EvaluationManifest,
    *,
    required_thresholds: EvaluationThresholds | None = None,
) -> EvaluationDecision:
    issues = _policy_issues(manifest, required_thresholds)
    success_delta = manifest.candidate.success_rate - manifest.baseline.success_rate
    success_regression = max(0.0, -success_delta)
    if success_regression > manifest.thresholds.max_success_rate_regression:
        issues.append(
            _metric_issue(
                "evaluation.success_rate_regression",
                "Candidate task success exceeds the allowed regression.",
                "success_rate",
                manifest.baseline.success_rate,
                manifest.candidate.success_rate,
                manifest.thresholds.max_success_rate_regression,
            )
        )
    cost_ratio = _increase_ratio(manifest.baseline.mean_cost, manifest.candidate.mean_cost)
    latency_ratio = _increase_ratio(manifest.baseline.mean_latency_ms, manifest.candidate.mean_latency_ms)
    _append_efficiency_issues(manifest, issues, cost_ratio, latency_ratio)
    _append_safety_issues(manifest, issues)
    return EvaluationDecision(
        manifest_digest=manifest.digest,
        passed=not issues,
        success_rate_delta=success_delta,
        cost_increase_ratio=cost_ratio,
        latency_increase_ratio=latency_ratio,
        issues=tuple(issues),
    )


def _policy_issues(
    manifest: EvaluationManifest,
    required_thresholds: EvaluationThresholds | None,
) -> list[EvaluationIssue]:
    if required_thresholds is None:
        return []
    return _threshold_policy_issues(manifest.thresholds, required_thresholds)


def _append_efficiency_issues(
    manifest: EvaluationManifest,
    issues: list[EvaluationIssue],
    cost_ratio: float,
    latency_ratio: float,
) -> None:
    if cost_ratio > manifest.thresholds.max_cost_increase_ratio:
        issues.append(
            _metric_issue(
                "evaluation.cost_regression",
                "Candidate cost exceeds the allowed increase.",
                "mean_cost",
                manifest.baseline.mean_cost,
                manifest.candidate.mean_cost,
                manifest.thresholds.max_cost_increase_ratio,
            )
        )
    if latency_ratio > manifest.thresholds.max_latency_increase_ratio:
        issues.append(
            _metric_issue(
                "evaluation.latency_regression",
                "Candidate latency exceeds the allowed increase.",
                "mean_latency_ms",
                manifest.baseline.mean_latency_ms,
                manifest.candidate.mean_latency_ms,
                manifest.thresholds.max_latency_increase_ratio,
            )
        )


def _metric_issue(
    code: str,
    message: str,
    metric: str,
    baseline: float,
    candidate: float,
    limit: float,
) -> EvaluationIssue:
    return EvaluationIssue(
        code=code,
        message=message,
        metric=metric,
        baseline=baseline,
        candidate=candidate,
        limit=limit,
    )


def _append_safety_issues(manifest: EvaluationManifest, issues: list[EvaluationIssue]) -> None:
    for metric in manifest.safety_metrics:
        if metric.regressed:
            issues.append(
                _metric_issue(
                    "evaluation.safety_regression",
                    "A protected safety metric regressed.",
                    metric.name,
                    metric.baseline_value,
                    metric.candidate_value,
                    0,
                )
            )
    for case in manifest.cases:
        if not case.candidate_safety_passed:
            issues.append(
                EvaluationIssue(
                    code="evaluation.case_safety_failure",
                    message="A case failed its protected safety checks.",
                    metric=case.case_id,
                )
            )


def _threshold_policy_issues(
    actual: EvaluationThresholds,
    required: EvaluationThresholds,
) -> list[EvaluationIssue]:
    comparisons = (
        ("minimum_sample_size", actual.minimum_sample_size, required.minimum_sample_size, False),
        (
            "minimum_held_out_cases",
            actual.minimum_held_out_cases,
            required.minimum_held_out_cases,
            False,
        ),
        (
            "max_success_rate_regression",
            actual.max_success_rate_regression,
            required.max_success_rate_regression,
            True,
        ),
        (
            "max_cost_increase_ratio",
            actual.max_cost_increase_ratio,
            required.max_cost_increase_ratio,
            True,
        ),
        (
            "max_latency_increase_ratio",
            actual.max_latency_increase_ratio,
            required.max_latency_increase_ratio,
            True,
        ),
    )
    return [
        EvaluationIssue(
            code="evaluation.threshold_policy_mismatch",
            message="Manifest threshold is weaker than the required policy.",
            metric=name,
            candidate=float(actual_value),
            limit=float(required_value),
        )
        for name, actual_value, required_value, maximum in comparisons
        if (actual_value > required_value if maximum else actual_value < required_value)
    ]


def _increase_ratio(baseline: float, candidate: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else sys.float_info.max
    return (candidate - baseline) / baseline
