from __future__ import annotations

from app.domain.grounding.identity import stable_id
from app.domain.grounding.ledger import EvidenceLedger
from app.domain.grounding.schemas import Citation, Claim, EvidenceKind


def project_grounded_answer(final_answer, ledger: EvidenceLedger):
    """Add deterministic grounding bindings without changing answer prose."""
    if not ledger.records(EvidenceKind.passage):
        return final_answer
    valid_evidence = ledger.evidence_ids()
    claims = _valid_claims(final_answer.claims, valid_evidence)
    passage_records = ledger.records(EvidenceKind.passage)
    snapshots = _snapshots_by_source(ledger)
    passage_by_url = _passages_by_url(passage_records, snapshots)
    claims = _ensure_claims(final_answer, claims, passage_records, passage_by_url)
    citations = _build_citations(final_answer.citations, claims, ledger, snapshots)
    return final_answer.model_copy(update={"claims": claims, "citations": citations})


def _valid_claims(claims, valid_evidence: set[str]):
    return [
        claim.model_copy(
            update={
                "evidence_refs": [
                    ref for ref in claim.evidence_refs if ref in valid_evidence
                ]
            }
        )
        for claim in claims
    ]


def _snapshots_by_source(ledger: EvidenceLedger):
    return {
        item.payload["source_id"]: item
        for item in ledger.records(EvidenceKind.source_snapshot)
    }


def _passages_by_url(passage_records, snapshots):
    passage_by_url: dict[str, list] = {}
    for record in passage_records:
        snapshot = snapshots.get(record.payload.get("source_id"))
        if snapshot is None:
            continue
        passage_by_url.setdefault(str(snapshot.payload.get("canonical_url")), []).append(record)
        passage_by_url.setdefault(str(snapshot.payload.get("requested_url")), []).append(record)
    return passage_by_url


def _ensure_claims(final_answer, claims, passage_records, passage_by_url):
    if not claims:
        return _synthesized_claims(final_answer, passage_records, passage_by_url)
    return [
        claim.model_copy(
            update={"support_status": "supported" if claim.evidence_refs else "unsupported"}
        )
        for claim in claims
    ]


def _synthesized_claims(final_answer, passage_records, passage_by_url):
    material = list(final_answer.findings)
    if not material and final_answer.summary.strip():
        material = [type("_Finding", (), {"text": final_answer.summary, "source_urls": []})()]
    return [
        _claim_from_finding(index, finding, passage_records, passage_by_url)
        for index, finding in enumerate(material)
    ]


def _claim_from_finding(index, finding, passage_records, passage_by_url):
    candidates = [
        record
        for url in getattr(finding, "source_urls", [])
        for record in passage_by_url.get(url, [])
    ]
    if not candidates and len(passage_records) == 1:
        candidates = passage_records
    refs = [item.id for item in candidates[:2]]
    return Claim(
        id=stable_id("claim", str(index), finding.text),
        text=finding.text,
        evidence_refs=refs,
        material=True,
        support_status="supported" if refs else "unsupported",
    )


def _build_citations(existing_citations, claims, ledger, snapshots):
    valid_evidence = ledger.evidence_ids()
    existing = {
        (citation.claim_id, citation.evidence_ref): citation
        for citation in existing_citations
        if citation.evidence_ref in valid_evidence
    }
    citations: list[Citation] = []
    ordinal = 1
    for claim in claims:
        for evidence_ref in claim.evidence_refs:
            record = ledger.get_by_id(evidence_ref)
            if record is None or record.kind != EvidenceKind.passage:
                continue
            passage = record.payload
            snapshot = snapshots.get(passage.get("source_id"))
            current = existing.get((claim.id, evidence_ref))
            citations.append(
                (current or Citation(
                    id=stable_id("citation", claim.id, evidence_ref),
                    claim_id=claim.id,
                    evidence_ref=evidence_ref,
                    source_id=passage.get("source_id"),
                    passage_id=passage.get("id"),
                    url=snapshot.payload.get("canonical_url") if snapshot else None,
                    title=snapshot.payload.get("title") if snapshot else None,
                )).model_copy(update={"ordinal": ordinal})
            )
            ordinal += 1
    return citations
