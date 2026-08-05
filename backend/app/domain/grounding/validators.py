from __future__ import annotations

from typing import Any

from app.common.schemas.agent.run_result import AgentValidationIssue, AgentValidationOutcome
from app.domain.grounding.ledger import GroundingEvidenceLedger
from app.domain.grounding.schemas import GroundingEvidenceFragment, GroundingEvidenceKind


def ledger_from_evidence(evidence: dict[str, Any]) -> GroundingEvidenceLedger:
    grounding = evidence.get("grounding") or {}
    records = grounding.get("records") if isinstance(grounding, dict) else []
    return GroundingEvidenceLedger(
        GroundingEvidenceFragment.model_validate(item)
        for item in (records or [])
        if isinstance(item, dict)
    )


def grounding_validation_outcomes(
    result: dict[str, Any],
    evidence: dict[str, Any],
) -> list[AgentValidationOutcome]:
    ledger = ledger_from_evidence(evidence)
    if not ledger.records():
        return []
    claims = [
        item for item in result.get("claims", [])
        if isinstance(item, dict)
    ]
    citations = [
        item for item in result.get("citations", [])
        if isinstance(item, dict)
    ]
    evidence_by_id = {item.id: item for item in ledger.records()}
    claim_ids = {str(item.get("id")) for item in claims if item.get("id")}

    cited_refs = {
        str(item.get("evidence_ref"))
        for item in citations
        if item.get("evidence_ref")
    }
    claim_refs = {
        str(ref)
        for item in claims
        for ref in item.get("evidence_refs", [])
    }
    provenance = _provenance_outcome(cited_refs, claim_refs, evidence_by_id)
    citation_integrity = _citation_outcome(
        claims, citations, claim_ids, cited_refs, evidence_by_id
    )
    claim_support = _claim_support_outcome(claims, claim_refs, ledger, evidence_by_id)
    return [provenance, citation_integrity, claim_support]


def _provenance_outcome(cited_refs, claim_refs, evidence_by_id):
    unknown_refs = sorted((cited_refs | claim_refs) - evidence_by_id.keys())
    issues = [
        AgentValidationIssue(
            code="grounding_evidence_unknown",
            message="回答引用了本次运行中不存在的证据。",
            evidence_refs=[evidence_ref],
        )
        for evidence_ref in unknown_refs
    ]
    return AgentValidationOutcome(
        validator="grounding.provenance",
        passed=not issues,
        blocking=True,
        issues=issues,
        evidence_refs=sorted((cited_refs | claim_refs) & evidence_by_id.keys()),
    )


def _citation_outcome(claims, citations, claim_ids, cited_refs, evidence_by_id):
    citation_issues = [
        issue
        for citation in citations
        for issue in _citation_issues(citation, claim_ids, evidence_by_id)
    ]
    if claims and not citations:
        citation_issues.append(
            AgentValidationIssue(
                code="grounding_citations_missing",
                message="已生成事实声明，但没有可展示的来源引用。",
            )
        )
    return AgentValidationOutcome(
        validator="grounding.citation_integrity",
        passed=not citation_issues,
        blocking=True,
        issues=citation_issues,
        evidence_refs=sorted(cited_refs & evidence_by_id.keys()),
    )


def _citation_issues(citation, claim_ids, evidence_by_id):
    claim_id = str(citation.get("claim_id") or "")
    evidence_ref = str(citation.get("evidence_ref") or "")
    evidence_refs = [evidence_ref] if evidence_ref else []
    issues = []
    if claim_id not in claim_ids:
        issues.append(AgentValidationIssue(
            code="grounding_citation_claim_unknown",
            message="引用指向了不存在的声明。",
            evidence_refs=evidence_refs,
        ))
    record = evidence_by_id.get(evidence_ref)
    if record is None or record.kind != GroundingEvidenceKind.passage:
        issues.append(AgentValidationIssue(
            code="grounding_citation_passage_invalid",
            message="引用没有指向可复核的来源片段。",
            evidence_refs=evidence_refs,
        ))
    return issues



def _claim_support_outcome(claims, claim_refs, ledger, evidence_by_id):
    support_issues = [
        issue
        for claim in claims
        if (issue := _unsupported_claim_issue(claim, evidence_by_id)) is not None
    ]
    if ledger.records(GroundingEvidenceKind.passage) and not claims:
        support_issues.append(
            AgentValidationIssue(
                code="grounding_material_claims_missing",
                message="已读取外部来源，但最终答案没有声明级证据绑定。",
            )
        )
    return AgentValidationOutcome(
        validator="grounding.claim_support",
        passed=not support_issues,
        blocking=True,
        issues=support_issues,
        evidence_refs=sorted(claim_refs & evidence_by_id.keys()),
    )


def _unsupported_claim_issue(claim, evidence_by_id):
    if not bool(claim.get("material", True)):
        return None
    refs = [str(ref) for ref in claim.get("evidence_refs", [])]
    eligible = [ref for ref in refs if _is_supporting_passage(ref, evidence_by_id)]
    if eligible:
        return None
    return AgentValidationIssue(
        code="grounding_material_claim_unsupported",
        message="一个关键声明没有可复核的来源片段支持。",
        evidence_refs=refs,
    )


def _is_supporting_passage(evidence_ref, evidence_by_id):
    record = evidence_by_id.get(evidence_ref)
    return (
        record is not None
        and record.kind == GroundingEvidenceKind.passage
        and record.payload.get("evidence_strength") != "candidate_only"
    )
