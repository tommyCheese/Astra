from __future__ import annotations

from typing import Any

from app.grounding.ledger import EvidenceLedger
from app.grounding.schemas import EvidenceFragment, EvidenceKind
from app.schemas.agent import ValidationIssue, ValidationOutcome


def ledger_from_evidence(evidence: dict[str, Any]) -> EvidenceLedger:
    grounding = evidence.get("grounding") or {}
    records = grounding.get("records") if isinstance(grounding, dict) else []
    return EvidenceLedger(
        EvidenceFragment.model_validate(item)
        for item in (records or [])
        if isinstance(item, dict)
    )


def grounding_validation_outcomes(
    result: dict[str, Any],
    evidence: dict[str, Any],
) -> list[ValidationOutcome]:
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

    provenance_issues = []
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
    for evidence_ref in sorted(cited_refs | claim_refs):
        if evidence_ref not in evidence_by_id:
            provenance_issues.append(
                ValidationIssue(
                    code="grounding_evidence_unknown",
                    message="回答引用了本次运行中不存在的证据。",
                    evidence_refs=[evidence_ref],
                )
            )
    provenance = ValidationOutcome(
        validator="grounding.provenance",
        passed=not provenance_issues,
        blocking=True,
        issues=provenance_issues,
        evidence_refs=sorted((cited_refs | claim_refs) & evidence_by_id.keys()),
    )

    citation_issues = []
    for citation in citations:
        claim_id = str(citation.get("claim_id") or "")
        evidence_ref = str(citation.get("evidence_ref") or "")
        if claim_id not in claim_ids:
            citation_issues.append(
                ValidationIssue(
                    code="grounding_citation_claim_unknown",
                    message="引用指向了不存在的声明。",
                    evidence_refs=[evidence_ref] if evidence_ref else [],
                )
            )
        record = evidence_by_id.get(evidence_ref)
        if record is None or record.kind != EvidenceKind.passage:
            citation_issues.append(
                ValidationIssue(
                    code="grounding_citation_passage_invalid",
                    message="引用没有指向可复核的来源片段。",
                    evidence_refs=[evidence_ref] if evidence_ref else [],
                )
            )
    if claims and not citations:
        citation_issues.append(
            ValidationIssue(
                code="grounding_citations_missing",
                message="已生成事实声明，但没有可展示的来源引用。",
            )
        )
    citation_integrity = ValidationOutcome(
        validator="grounding.citation_integrity",
        passed=not citation_issues,
        blocking=True,
        issues=citation_issues,
        evidence_refs=sorted(cited_refs & evidence_by_id.keys()),
    )

    support_issues = []
    for claim in claims:
        if not bool(claim.get("material", True)):
            continue
        refs = [str(ref) for ref in claim.get("evidence_refs", [])]
        eligible = [
            ref
            for ref in refs
            if ref in evidence_by_id
            and evidence_by_id[ref].kind == EvidenceKind.passage
            and evidence_by_id[ref].payload.get("evidence_strength")
            != "candidate_only"
        ]
        if not eligible:
            support_issues.append(
                ValidationIssue(
                    code="grounding_material_claim_unsupported",
                    message="一个关键声明没有可复核的来源片段支持。",
                    evidence_refs=refs,
                )
            )
    if ledger.records(EvidenceKind.passage) and not claims:
        support_issues.append(
            ValidationIssue(
                code="grounding_material_claims_missing",
                message="已读取外部来源，但最终答案没有声明级证据绑定。",
            )
        )
    claim_support = ValidationOutcome(
        validator="grounding.claim_support",
        passed=not support_issues,
        blocking=True,
        issues=support_issues,
        evidence_refs=sorted(claim_refs & evidence_by_id.keys()),
    )
    return [provenance, citation_integrity, claim_support]
