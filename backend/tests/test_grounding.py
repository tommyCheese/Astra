import pytest

from app.agent_runtime.policies.completion import CompletionGate
from app.agent_runtime.policies.reasoning import apply_validation_outcomes, build_default_contract
from app.db.models.conversations import TaskRecord
from app.db.models.runs import RunRecord
from app.grounding.fragments import fragments_from_web_result
from app.grounding.identity import (
    canonical_url,
    digest_text,
    search_trace_id,
    segment_passages,
    snapshot_id,
    source_id,
)
from app.grounding.ledger import EvidenceConflictError, EvidenceLedger
from app.grounding.projection import project_grounded_answer
from app.grounding.repository import EvidenceRepository, EvidenceWriter
from app.grounding.schemas import EvidenceFragment, EvidenceKind
from app.grounding.validators import grounding_validation_outcomes
from app.schemas.agent.execution_state import AgentState
from app.schemas.agent.run_result import FinalAnswer, Finding, SourceReference, ValidationOutcome
from app.schemas.agent.types import TerminalState


def test_grounding_identities_are_stable_and_tracking_parameters_are_removed():
    first = canonical_url("https://Example.com/docs/?utm_source=test&b=2&a=1")
    second = canonical_url("https://example.com/docs?b=2&a=1")
    assert first == second
    source = source_id(first)
    assert source == source_id(second)
    content_hash = digest_text("A  grounded\nsource")
    assert snapshot_id(source, content_hash) == snapshot_id(source, content_hash)
    assert search_trace_id("Astra", 0, "tool-1") == search_trace_id(
        "Astra", 0, "tool-1"
    )
    assert search_trace_id("Astra", 0, "tool-1") != search_trace_id(
        "Astra", 0, "tool-2"
    )


def test_segment_passages_is_bounded_and_find_open_are_local():
    content = "Alpha evidence. " * 100 + "Beta conclusion. " * 80
    source = source_id("https://example.com/report")
    snapshot = snapshot_id(source, digest_text(content))
    passages = segment_passages(
        content,
        source=source,
        snapshot=snapshot,
        max_chars=240,
        overlap_chars=30,
        max_passages=8,
    )
    assert 1 < len(passages) <= 8
    assert all(len(item.text) <= 240 for item in passages)
    fragments = fragments_from_web_result(
        "web_fetch",
        {
            "url": "https://example.com/report",
            "content": content,
            "content_length": len(content),
        },
    )
    ledger = EvidenceLedger(fragments)
    matches = ledger.find_passages(source, "Beta", max_passages=2)
    assert matches
    assert ledger.open_passage(source, matches[0].id, context_before=1, context_after=1)


def test_search_snippets_are_candidate_only_evidence():
    fragments = fragments_from_web_result(
        "web_search",
        {
            "query": "Astra",
            "provider": "google",
            "candidates": [
                {
                    "url": "https://example.com/astra",
                    "title": "Astra",
                    "snippet": "Candidate summary",
                    "rank": 1,
                }
            ],
        },
    )
    candidate = next(item for item in fragments if item.kind == EvidenceKind.search_candidate)
    assert candidate.payload["evidence_strength"] == "candidate_only"


def test_evidence_ledger_replay_is_idempotent_and_conflicts_fail():
    fragments = fragments_from_web_result(
        "web_search",
        {"query": "Astra", "provider": "google", "candidates": []},
    )
    ledger = EvidenceLedger(fragments)
    assert len(ledger.records()) == 1
    ledger.append(fragments[0])
    assert len(ledger.records()) == 1
    conflicting = EvidenceFragment.model_validate(
        fragments[0].model_dump(mode="json")
        | {"payload_digest": "0" * 64}
    )
    with pytest.raises(EvidenceConflictError):
        ledger.append(conflicting)


async def test_evidence_writer_persists_run_lineage_idempotently(session):
    task = TaskRecord(title="Grounding", description="Grounding")
    session.add(task)
    await session.flush()
    run = RunRecord(task_id=task.id)
    session.add(run)
    await session.flush()
    fragments = fragments_from_web_result(
        "web_search",
        {"query": "Astra", "provider": "google", "candidates": []},
    )
    writer = EvidenceWriter(EvidenceRepository(session))
    first = await writer.write(
        run.id,
        fragments,
        tool_call_id="tool-call-1",
    )
    second = await writer.write(
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
    output = {
        "url": "https://example.com/report",
        "content": "Astra evidence supports the material conclusion.",
        "content_length": 48,
        "title": "Report",
    }
    ledger = EvidenceLedger(fragments_from_web_result("web_fetch", output))
    answer = project_grounded_answer(
        FinalAnswer(
            summary="Material conclusion",
            findings=[
                Finding(
                    text="Material conclusion",
                    source_urls=["https://example.com/report"],
                )
            ],
            sources=[SourceReference(url="https://example.com/report")],
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
    ledger = EvidenceLedger(
        fragments_from_web_result(
            "web_search",
            {
                "query": "Astra",
                "provider": "google",
                "candidates": [
                    {
                        "url": "https://example.com/result",
                        "title": "Result",
                        "snippet": "Unverified snippet",
                        "rank": 1,
                    }
                ],
            },
        )
    )
    candidate = ledger.records(EvidenceKind.search_candidate)[0]
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
    support = next(
        outcome for outcome in outcomes
        if outcome.validator == "grounding.claim_support"
    )
    assert support.passed is False
    assert support.issues[0].code == "grounding_material_claim_unsupported"


def test_non_web_result_does_not_activate_grounding_validators():
    assert grounding_validation_outcomes(
        {"summary": "Stable general knowledge"},
        {},
    ) == []


def test_grounding_failure_blocks_trusted_completion_without_affecting_non_web():
    task_adapter_passed = ValidationOutcome(
        validator="task_adapter",
        passed=True,
        blocking=True,
    )
    non_web_state = apply_validation_outcomes(
        AgentState(task_contract=build_default_contract("General task")),
        [task_adapter_passed],
    )
    assert CompletionGate().evaluate(
        non_web_state,
        validation_outcomes=[task_adapter_passed],
    ).state == TerminalState.completed

    grounding_failed = ValidationOutcome(
        validator="grounding.claim_support",
        passed=False,
        blocking=True,
    )
    grounded_state = apply_validation_outcomes(
        non_web_state,
        [grounding_failed],
    )
    decision = CompletionGate().evaluate(
        grounded_state,
        validation_outcomes=[task_adapter_passed, grounding_failed],
    )
    assert decision.state == TerminalState.blocked
    assert "validator:grounding.claim_support" in decision.unmet_criteria
