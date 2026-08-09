import hashlib
import json

import pytest

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import (
    apply_validation_outcomes,
    build_default_contract,
)
from app.common.schemas.agent.execution_state import AgentState
from app.common.schemas.agent.run_result import (
    AgentAnswerFinding,
    AgentAnswerSourceReference,
    AgentFinalAnswer,
    AgentValidationOutcome,
)
from app.common.schemas.agent.types import TerminalState
from app.domain.grounding.ledger import (
    GroundingEvidenceConflictError,
    GroundingEvidenceLedger,
)
from app.domain.grounding.projection import project_grounded_answer
from app.domain.grounding.schemas import GroundingEvidenceFragment, GroundingEvidenceKind
from app.domain.grounding.validators import grounding_validation_outcomes
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.evidence import EvidenceRepository


def evidence_fragment(
    evidence_id: str,
    kind: GroundingEvidenceKind,
    payload: dict,
) -> GroundingEvidenceFragment:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return GroundingEvidenceFragment(
        id=evidence_id,
        kind=kind,
        evidence_key=f"{kind.value}:{evidence_id}",
        payload_digest=hashlib.sha256(serialized).hexdigest(),
        payload=payload,
    )


def passage_fragment(
    evidence_id: str = "evidence-passage-1",
    *,
    source: str = "source-1",
    snapshot: str = "snapshot-1",
    text: str = "Astra evidence supports the material conclusion.",
) -> GroundingEvidenceFragment:
    return evidence_fragment(
        evidence_id,
        GroundingEvidenceKind.passage,
        {
            "id": "passage-1",
            "source_id": source,
            "snapshot_id": snapshot,
            "ordinal": 0,
            "text": text,
            "start_offset": 0,
            "end_offset": len(text),
            "evidence_strength": "source_passage",
        },
    )


def search_trace_fragment() -> GroundingEvidenceFragment:
    return evidence_fragment(
        "evidence-search-1",
        GroundingEvidenceKind.search_trace,
        {
            "id": "search-1",
            "query": "Astra",
            "provider": "google",
        },
    )


def test_evidence_ledger_finds_and_opens_canonical_passages():
    ledger = GroundingEvidenceLedger([passage_fragment(text="Alpha evidence. Beta conclusion.")])

    matches = ledger.find_passages("source-1", "Beta", max_passages=2)

    assert [item.id for item in matches] == ["passage-1"]
    assert ledger.open_passage("source-1", "passage-1", context_before=1, context_after=1) == matches


def test_evidence_ledger_replay_is_idempotent_and_conflicts_fail():
    fragments = [search_trace_fragment()]
    ledger = GroundingEvidenceLedger(fragments)
    assert len(ledger.records()) == 1
    ledger.append(fragments[0])
    assert len(ledger.records()) == 1
    conflicting = GroundingEvidenceFragment.model_validate(fragments[0].model_dump(mode="json") | {"payload_digest": "0" * 64})
    with pytest.raises(GroundingEvidenceConflictError):
        ledger.append(conflicting)


async def test_evidence_writer_persists_run_lineage_idempotently(session):
    task = TaskRecord(title="Grounding", description="Grounding")
    session.add(task)
    await session.flush()
    run = RunRecord(task_id=task.id)
    session.add(run)
    await session.flush()
    fragments = [search_trace_fragment()]
    repository = EvidenceRepository(session)
    first = await repository.append_with_lineage(
        run.id,
        fragments,
        tool_call_id="tool-call-1",
    )
    second = await repository.append_with_lineage(
        run.id,
        fragments,
        tool_call_id="tool-call-1",
    )

    assert first[0].id == second[0].id
    assert first[0].evidence_id == fragments[0].id
    assert first[0].fragment["lineage"]["run_id"] == run.id
    ledger = await EvidenceRepository(session).ledger_for_run(run.id)
    assert ledger.evidence_ids() == {fragments[0].id}


def test_grounded_projection_binds_findings_to_passages_and_validates():
    snapshot = evidence_fragment(
        "evidence-snapshot-1",
        GroundingEvidenceKind.source_snapshot,
        {
            "id": "snapshot-1",
            "source_id": "source-1",
            "requested_url": "https://example.com/report",
            "canonical_url": "https://example.com/report",
            "title": "Report",
        },
    )
    ledger = GroundingEvidenceLedger([snapshot, passage_fragment()])
    answer = project_grounded_answer(
        AgentFinalAnswer(
            summary="Material conclusion",
            findings=[
                AgentAnswerFinding(
                    text="Material conclusion",
                    source_urls=["https://example.com/report"],
                )
            ],
            sources=[AgentAnswerSourceReference(url="https://example.com/report")],
        ),
        ledger,
    )
    outcomes = grounding_validation_outcomes(
        answer.model_dump(mode="json"),
        {"grounding": ledger.model_dump()},
    )

    assert answer.claims[0].support_status == "supported"
    assert answer.citations[0].evidence_ref == answer.claims[0].evidence_refs[0]
    assert {outcome.validator for outcome in outcomes} == {
        "grounding.provenance",
        "grounding.citation_integrity",
        "grounding.claim_support",
    }
    assert all(outcome.passed for outcome in outcomes)


def test_candidate_only_evidence_cannot_support_material_claim():
    candidate = evidence_fragment(
        "evidence-candidate-1",
        GroundingEvidenceKind.search_candidate,
        {
            "id": "candidate-1",
            "search_trace_id": "search-1",
            "url": "https://example.com/result",
            "canonical_url": "https://example.com/result",
            "evidence_strength": "candidate_only",
        },
    )
    ledger = GroundingEvidenceLedger([candidate])
    result = {
        "claims": [
            {
                "id": "claim-1",
                "text": "Material claim",
                "material": True,
                "evidence_refs": [candidate.id],
            }
        ],
        "citations": [],
    }
    outcomes = grounding_validation_outcomes(
        result,
        {"grounding": ledger.model_dump()},
    )
    support = next(outcome for outcome in outcomes if outcome.validator == "grounding.claim_support")
    assert support.passed is False
    assert support.issues[0].code == "grounding_material_claim_unsupported"


def test_non_web_result_does_not_activate_grounding_validators():
    assert (
        grounding_validation_outcomes(
            {"summary": "Stable general knowledge"},
            {},
        )
        == []
    )


def test_grounding_failure_blocks_trusted_completion_without_affecting_non_web():
    task_adapter_passed = AgentValidationOutcome(
        validator="task_adapter",
        passed=True,
        blocking=True,
    )
    non_web_state = apply_validation_outcomes(
        AgentState(task_contract=build_default_contract("General task")),
        [task_adapter_passed],
    )
    assert (
        AgentCompletionGate()
        .evaluate(
            non_web_state,
            validation_outcomes=[task_adapter_passed],
        )
        .state
        == TerminalState.completed
    )

    grounding_failed = AgentValidationOutcome(
        validator="grounding.claim_support",
        passed=False,
        blocking=True,
    )
    grounded_state = apply_validation_outcomes(
        non_web_state,
        [grounding_failed],
    )
    decision = AgentCompletionGate().evaluate(
        grounded_state,
        validation_outcomes=[task_adapter_passed, grounding_failed],
    )
    assert decision.state == TerminalState.blocked
    assert "validator:grounding.claim_support" in decision.unmet_criteria
