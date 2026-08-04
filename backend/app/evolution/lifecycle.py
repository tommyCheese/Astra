from __future__ import annotations

from collections.abc import Set
from types import MappingProxyType

from app.evolution.domain import (
    EvaluationManifest,
    EvaluationThresholds,
    EvolutionCandidate,
    EvolutionCandidateState,
    EvolutionCandidateStatus,
    EvolutionDomainError,
    assert_candidate_authority,
)
from app.evolution.evaluation import evaluate_manifest

LIFECYCLE_TRANSITIONS = MappingProxyType(
    {
        EvolutionCandidateStatus.draft: frozenset(
            {EvolutionCandidateStatus.evaluating, EvolutionCandidateStatus.rejected}
        ),
        EvolutionCandidateStatus.evaluating: frozenset(
            {EvolutionCandidateStatus.approved, EvolutionCandidateStatus.rejected}
        ),
        EvolutionCandidateStatus.approved: frozenset(
            {EvolutionCandidateStatus.shadow, EvolutionCandidateStatus.rejected}
        ),
        EvolutionCandidateStatus.shadow: frozenset(
            {EvolutionCandidateStatus.canary, EvolutionCandidateStatus.rolled_back}
        ),
        EvolutionCandidateStatus.canary: frozenset(
            {EvolutionCandidateStatus.promoted, EvolutionCandidateStatus.rolled_back}
        ),
        EvolutionCandidateStatus.promoted: frozenset({EvolutionCandidateStatus.rolled_back}),
        EvolutionCandidateStatus.rejected: frozenset(),
        EvolutionCandidateStatus.rolled_back: frozenset(),
    }
)

ROLLOUT_STATES = frozenset(
    {
        EvolutionCandidateStatus.shadow,
        EvolutionCandidateStatus.canary,
        EvolutionCandidateStatus.promoted,
    }
)

EVALUATED_STATES = frozenset(
    {
        EvolutionCandidateStatus.approved,
        EvolutionCandidateStatus.shadow,
        EvolutionCandidateStatus.canary,
        EvolutionCandidateStatus.promoted,
    }
)


def transition_candidate_state(
    candidate: EvolutionCandidate,
    state: EvolutionCandidateState,
    target: EvolutionCandidateStatus,
    *,
    expected_state_version: int,
    available_tools: Set[str],
    evaluation_manifest: EvaluationManifest | None = None,
    required_thresholds: EvaluationThresholds | None = None,
    promotion_enabled: bool = False,
) -> EvolutionCandidateState:
    _validate_transition_request(
        candidate,
        state,
        target,
        expected_state_version=expected_state_version,
        available_tools=available_tools,
        promotion_enabled=promotion_enabled,
    )
    evaluation_digest = _evaluation_digest(
        candidate,
        state,
        target,
        evaluation_manifest=evaluation_manifest,
        required_thresholds=required_thresholds,
    )
    return state.model_copy(
        update={
            "status": target,
            "state_version": state.state_version + 1,
            "evaluation_digest": evaluation_digest,
        }
    )


def _validate_transition_request(
    candidate: EvolutionCandidate,
    state: EvolutionCandidateState,
    target: EvolutionCandidateStatus,
    *,
    expected_state_version: int,
    available_tools: Set[str],
    promotion_enabled: bool,
) -> None:
    if state.candidate_digest != candidate.digest:
        raise EvolutionDomainError(
            "EVOLUTION_CANDIDATE_MISMATCH",
            "Lifecycle state belongs to a different immutable candidate revision.",
        )
    if state.state_version != expected_state_version:
        raise EvolutionDomainError(
            "EVOLUTION_STATE_STALE",
            "Evolution candidate state has changed.",
            {
                "expected_state_version": expected_state_version,
                "actual_state_version": state.state_version,
            },
        )
    if target in ROLLOUT_STATES and not promotion_enabled:
        raise EvolutionDomainError(
            "EVOLUTION_PROMOTION_DISABLED",
            "Shadow, Canary, and production promotion are disabled.",
            {"requested_status": target.value},
        )
    if target not in LIFECYCLE_TRANSITIONS[state.status]:
        raise EvolutionDomainError(
            "EVOLUTION_TRANSITION_INVALID",
            f"Invalid evolution transition: {state.status.value} -> {target.value}.",
        )
    if target not in {
        EvolutionCandidateStatus.rejected,
        EvolutionCandidateStatus.rolled_back,
    }:
        assert_candidate_authority(candidate, available_tools=available_tools)


def _evaluation_digest(
    candidate: EvolutionCandidate,
    state: EvolutionCandidateState,
    target: EvolutionCandidateStatus,
    *,
    evaluation_manifest: EvaluationManifest | None,
    required_thresholds: EvaluationThresholds | None,
) -> str | None:
    if target not in EVALUATED_STATES:
        return state.evaluation_digest
    if evaluation_manifest is None:
        raise EvolutionDomainError(
            "EVOLUTION_EVALUATION_REQUIRED",
            "A frozen evaluation manifest is required for this transition.",
        )
    if evaluation_manifest.candidate_digest != candidate.digest:
        raise EvolutionDomainError(
            "EVOLUTION_EVALUATION_MISMATCH",
            "Evaluation manifest belongs to a different candidate revision.",
        )
    decision = evaluate_manifest(
        evaluation_manifest,
        required_thresholds=required_thresholds,
    )
    if not decision.passed:
        raise EvolutionDomainError(
            "EVOLUTION_EVALUATION_FAILED",
            "Evaluation gates did not pass.",
            {"issues": [item.model_dump(mode="json") for item in decision.issues]},
        )
    return evaluation_manifest.digest
