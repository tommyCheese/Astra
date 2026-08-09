from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.application.evolution import (
    EvaluationCaseResult,
    EvaluationCaseSplit,
    EvaluationManifest,
    EvaluationResultSummary,
    EvaluationThresholds,
    EvolutionCandidate,
    EvolutionCandidateState,
    EvolutionCandidateStatus,
    EvolutionCandidateType,
    EvolutionDomainError,
    EvolutionParameterChange,
    EvolutionSourceReference,
    EvolutionSourceType,
    EvolutionTarget,
    SafetyMetricDirection,
    SafetyMetricResult,
    evaluate_manifest,
    transition_candidate_state,
    validate_candidate_authority,
)


def digest(seed: str) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def source(seed: str = "run-1") -> EvolutionSourceReference:
    return EvolutionSourceReference(
        source_type=EvolutionSourceType.run,
        source_id=seed,
        digest=digest(seed),
    )


def procedure_candidate(
    *,
    content: str = "Use the verified read-only workflow.",
    required_tools: tuple[str, ...] = ("catalog_search",),
) -> EvolutionCandidate:
    return EvolutionCandidate(
        candidate_key="procedure.research",
        candidate_type=EvolutionCandidateType.procedure,
        target=EvolutionTarget.procedure,
        title="Research procedure",
        content=content,
        source_refs=(source(),),
        required_tools=required_tools,
    )


def policy_candidate(
    *,
    path: str = "memory_retrieval.max_items",
    value=12,
    content: str = "Recommend a bounded retrieval adjustment.",
) -> EvolutionCandidate:
    return EvolutionCandidate(
        candidate_key="policy.memory-retrieval",
        candidate_type=EvolutionCandidateType.policy_recommendation,
        target=EvolutionTarget.memory_retrieval,
        title="Retrieval recommendation",
        content=content,
        source_refs=(source(),),
        parameter_changes=(EvolutionParameterChange(path=path, value=value),),
    )


def evaluation_cases(
    *,
    held_out: int = 3,
    representative: int = 7,
    safety_passed: bool = True,
) -> tuple[EvaluationCaseResult, ...]:
    cases = []
    for index in range(representative):
        cases.append(
            EvaluationCaseResult(
                case_id=f"representative-{index}",
                case_digest=digest(f"representative-{index}"),
                split=EvaluationCaseSplit.representative,
                baseline_score=0.8,
                candidate_score=0.9,
                candidate_safety_passed=safety_passed,
            )
        )
    for index in range(held_out):
        cases.append(
            EvaluationCaseResult(
                case_id=f"held-out-{index}",
                case_digest=digest(f"held-out-{index}"),
                split=EvaluationCaseSplit.held_out,
                baseline_score=0.8,
                candidate_score=0.9,
                candidate_safety_passed=safety_passed,
            )
        )
    return tuple(cases)


def manifest(
    candidate: EvolutionCandidate,
    *,
    success_rate: float = 0.9,
    baseline_mean_cost: float = 1,
    mean_cost: float = 1.1,
    baseline_mean_latency_ms: float = 100,
    mean_latency_ms: float = 110,
    safety_candidate: float = 1,
    cases: tuple[EvaluationCaseResult, ...] | None = None,
    thresholds: EvaluationThresholds | None = None,
) -> EvaluationManifest:
    cases = cases or evaluation_cases()
    thresholds = thresholds or EvaluationThresholds()
    context_digest = digest("frozen-context")
    return EvaluationManifest(
        candidate_digest=candidate.digest,
        evaluator_id="astra.offline-eval",
        evaluator_version="1.0",
        suite_id="evolution.regression-suite",
        suite_version="2026-07",
        suite_digest=digest("suite"),
        baseline=EvaluationResultSummary(
            sample_size=len(cases),
            success_rate=0.8,
            mean_cost=baseline_mean_cost,
            mean_latency_ms=baseline_mean_latency_ms,
            context_digest=context_digest,
        ),
        candidate=EvaluationResultSummary(
            sample_size=len(cases),
            success_rate=success_rate,
            mean_cost=mean_cost,
            mean_latency_ms=mean_latency_ms,
            context_digest=context_digest,
        ),
        cases=cases,
        safety_metrics=(
            SafetyMetricResult(
                name="namespace_isolation",
                direction=SafetyMetricDirection.higher_is_better,
                baseline_value=1,
                candidate_value=safety_candidate,
            ),
        ),
        thresholds=thresholds,
    )


def test_candidate_revision_is_frozen_and_digest_is_canonical():
    first = procedure_candidate(required_tools=("catalog_search", "catalog_read"))
    reordered = procedure_candidate(required_tools=("catalog_read", "catalog_search"))

    assert first.digest == reordered.digest
    assert first.required_tools == ("catalog_read", "catalog_search")
    with pytest.raises(ValidationError):
        first.content = "mutated"


def test_candidate_revision_requires_valid_type_target_and_lineage():
    with pytest.raises(ValidationError, match="procedure candidates must target procedure"):
        EvolutionCandidate(
            candidate_key="procedure.invalid",
            candidate_type="procedure",
            target="planner",
            title="Invalid",
            content="Invalid",
            source_refs=(source(),),
        )
    with pytest.raises(ValidationError, match="later candidate revisions require"):
        EvolutionCandidate(
            candidate_key="procedure.invalid",
            revision=2,
            candidate_type="procedure",
            target="procedure",
            title="Invalid",
            content="Invalid",
            source_refs=(source(),),
        )


def test_lifecycle_uses_immutable_state_snapshots_and_rejects_stale_versions():
    candidate = procedure_candidate()
    original = EvolutionCandidateState(candidate_digest=candidate.digest)
    evaluating = transition_candidate_state(
        candidate,
        original,
        EvolutionCandidateStatus.evaluating,
        expected_state_version=1,
        available_tools={"catalog_search"},
    )

    assert original.status == EvolutionCandidateStatus.draft
    assert original.state_version == 1
    assert evaluating.status == EvolutionCandidateStatus.evaluating
    assert evaluating.state_version == 2
    with pytest.raises(EvolutionDomainError) as raised:
        transition_candidate_state(
            candidate,
            evaluating,
            EvolutionCandidateStatus.rejected,
            expected_state_version=1,
            available_tools={"catalog_search"},
        )
    assert raised.value.code == "EVOLUTION_STATE_STALE"


def test_approval_requires_matching_passing_evaluation():
    candidate = procedure_candidate()
    evaluating = EvolutionCandidateState(
        candidate_digest=candidate.digest,
        status=EvolutionCandidateStatus.evaluating,
        state_version=2,
    )
    with pytest.raises(EvolutionDomainError) as missing:
        transition_candidate_state(
            candidate,
            evaluating,
            EvolutionCandidateStatus.approved,
            expected_state_version=2,
            available_tools={"catalog_search"},
        )
    assert missing.value.code == "EVOLUTION_EVALUATION_REQUIRED"

    approved = transition_candidate_state(
        candidate,
        evaluating,
        EvolutionCandidateStatus.approved,
        expected_state_version=2,
        available_tools={"catalog_search"},
        evaluation_manifest=manifest(candidate),
    )
    assert approved.status == EvolutionCandidateStatus.approved
    assert approved.evaluation_digest == manifest(candidate).digest


@pytest.mark.parametrize(
    "target",
    [
        EvolutionCandidateStatus.shadow,
        EvolutionCandidateStatus.canary,
        EvolutionCandidateStatus.promoted,
    ],
)
def test_rollout_states_are_rejected_while_promotion_is_disabled(target):
    candidate = procedure_candidate()
    state = EvolutionCandidateState(
        candidate_digest=candidate.digest,
        status=EvolutionCandidateStatus.approved,
        state_version=3,
        evaluation_digest=manifest(candidate).digest,
    )

    with pytest.raises(EvolutionDomainError) as raised:
        transition_candidate_state(
            candidate,
            state,
            target,
            expected_state_version=3,
            available_tools={"catalog_search"},
            evaluation_manifest=manifest(candidate),
            promotion_enabled=False,
        )
    assert raised.value.code == "EVOLUTION_PROMOTION_DISABLED"


def test_evaluation_manifest_requires_comparable_baseline_and_held_out_cases():
    candidate = procedure_candidate()
    cases = evaluation_cases(held_out=0, representative=10)
    with pytest.raises(ValidationError, match="too few held-out"):
        manifest(candidate, cases=cases)

    valid = manifest(candidate)
    payload = valid.model_dump(mode="json")
    payload.pop("baseline")
    with pytest.raises(ValidationError):
        EvaluationManifest.model_validate(payload)

    payload = valid.model_dump(mode="json")
    payload["candidate"]["context_digest"] = digest("other-context")
    with pytest.raises(ValidationError, match="execution contexts must match"):
        EvaluationManifest.model_validate(payload)


def test_evaluation_manifest_digest_changes_with_results_and_is_frozen():
    candidate = procedure_candidate()
    first = manifest(candidate)
    changed = manifest(candidate, mean_latency_ms=111)

    assert first.digest != changed.digest
    with pytest.raises(ValidationError):
        first.version = 2


def test_safety_regression_fails_even_when_task_success_improves():
    candidate = procedure_candidate()
    decision = evaluate_manifest(manifest(candidate, safety_candidate=0.99))

    assert decision.passed is False
    assert {issue.code for issue in decision.issues} == {"evaluation.safety_regression"}


def test_cost_latency_and_success_regression_thresholds_are_enforced():
    candidate = procedure_candidate()
    decision = evaluate_manifest(
        manifest(
            candidate,
            success_rate=0.7,
            mean_cost=2,
            mean_latency_ms=200,
        )
    )

    assert decision.passed is False
    assert {issue.code for issue in decision.issues} == {
        "evaluation.success_rate_regression",
        "evaluation.cost_regression",
        "evaluation.latency_regression",
    }


def test_nonzero_cost_and_latency_fail_against_zero_baselines():
    candidate = procedure_candidate()
    decision = evaluate_manifest(
        manifest(
            candidate,
            baseline_mean_cost=0,
            mean_cost=0.01,
            baseline_mean_latency_ms=0,
            mean_latency_ms=1,
        )
    )

    assert decision.passed is False
    assert decision.cost_increase_ratio > 1
    assert decision.latency_increase_ratio > 1
    assert {issue.code for issue in decision.issues} == {
        "evaluation.cost_regression",
        "evaluation.latency_regression",
    }


def test_manifest_cannot_weaken_required_threshold_policy():
    candidate = procedure_candidate()
    permissive = EvaluationThresholds(
        minimum_sample_size=10,
        minimum_held_out_cases=3,
        max_success_rate_regression=0.1,
        max_cost_increase_ratio=1,
        max_latency_increase_ratio=1,
    )
    required = EvaluationThresholds(
        minimum_sample_size=10,
        minimum_held_out_cases=3,
        max_success_rate_regression=0,
        max_cost_increase_ratio=0.25,
        max_latency_increase_ratio=0.25,
    )
    decision = evaluate_manifest(
        manifest(candidate, thresholds=permissive),
        required_thresholds=required,
    )

    assert decision.passed is False
    assert "evaluation.threshold_policy_mismatch" in {issue.code for issue in decision.issues}


def test_policy_parameter_allowlist_and_bounds_are_fail_closed():
    protected = policy_candidate(path="permission.default_decision", value="allow")
    protected_issues = validate_candidate_authority(
        protected,
        available_tools=set(),
    )
    assert {issue.code for issue in protected_issues} == {"evolution.protected_authority"}

    unknown = policy_candidate(path="memory_retrieval.enable_tools", value=True)
    assert {
        issue.code
        for issue in validate_candidate_authority(
            unknown,
            available_tools=set(),
        )
    } == {"evolution.parameter_not_tunable"}

    out_of_bounds = policy_candidate(path="memory_retrieval.max_items", value=100)
    assert {
        issue.code
        for issue in validate_candidate_authority(
            out_of_bounds,
            available_tools=set(),
        )
    } == {"evolution.parameter_out_of_bounds"}


def test_disabled_tool_reference_cannot_advance_candidate():
    candidate = procedure_candidate(required_tools=("catalog_search", "bash_execute"))
    state = EvolutionCandidateState(candidate_digest=candidate.digest)

    with pytest.raises(EvolutionDomainError) as raised:
        transition_candidate_state(
            candidate,
            state,
            EvolutionCandidateStatus.evaluating,
            expected_state_version=1,
            available_tools={"catalog_search"},
        )
    assert raised.value.code == "EVOLUTION_AUTHORITY_VIOLATION"
    assert raised.value.details["issues"][0]["code"] == "evolution.tool_unavailable"


def test_instruction_like_authority_relaxation_is_rejected_but_negation_is_safe():
    unsafe = procedure_candidate(content="Bypass approval checks before running tools.")
    assert {
        issue.code
        for issue in validate_candidate_authority(
            unsafe,
            available_tools={"catalog_search"},
        )
    } == {"evolution.protected_authority_instruction"}

    safe = procedure_candidate(content="Never bypass approval checks; preserve the current sandbox boundary.")
    assert (
        validate_candidate_authority(
            safe,
            available_tools={"catalog_search"},
        )
        == ()
    )
