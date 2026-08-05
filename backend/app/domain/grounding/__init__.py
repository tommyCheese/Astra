"""Provider-independent evidence grounding contracts and services."""

from app.domain.grounding.fragments import fragments_from_web_result
from app.domain.grounding.ledger import EvidenceConflictError, EvidenceLedger
from app.domain.grounding.schemas import (
    Citation,
    Claim,
    EvidenceFragment,
    EvidenceKind,
    Passage,
    SearchCandidate,
    SearchConstraints,
    SearchTrace,
    SourceSnapshot,
)

__all__ = [
    "Citation",
    "Claim",
    "EvidenceConflictError",
    "EvidenceFragment",
    "EvidenceKind",
    "EvidenceLedger",
    "Passage",
    "SearchCandidate",
    "SearchConstraints",
    "SearchTrace",
    "SourceSnapshot",
    "fragments_from_web_result",
]
