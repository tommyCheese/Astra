from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from app.domain.grounding.schemas import (
    GroundingEvidenceFragment,
    GroundingEvidenceKind,
    GroundingSourcePassage,
)


class GroundingEvidenceConflictError(ValueError):
    pass


class GroundingEvidenceLedger:
    def __init__(self, fragments: Iterable[GroundingEvidenceFragment] = ()):
        self._records: dict[str, GroundingEvidenceFragment] = {}
        for fragment in fragments:
            self.append(fragment)

    def append(self, fragment: GroundingEvidenceFragment) -> GroundingEvidenceFragment:
        existing = self._records.get(fragment.evidence_key)
        if existing is not None:
            if existing.payload_digest != fragment.payload_digest:
                raise GroundingEvidenceConflictError(
                    f"conflicting evidence replay for {fragment.evidence_key}"
                )
            return existing
        self._records[fragment.evidence_key] = fragment
        return fragment

    def extend(
        self, fragments: Iterable[GroundingEvidenceFragment]
    ) -> list[GroundingEvidenceFragment]:
        return [self.append(fragment) for fragment in fragments]

    def records(
        self, kind: GroundingEvidenceKind | None = None
    ) -> list[GroundingEvidenceFragment]:
        values = list(self._records.values())
        if kind is not None:
            values = [item for item in values if item.kind == kind]
        return values

    def evidence_ids(self) -> set[str]:
        return {item.id for item in self._records.values()}

    def get_by_id(self, evidence_id: str) -> GroundingEvidenceFragment | None:
        return next((item for item in self._records.values() if item.id == evidence_id), None)

    def passages(self, source_id: str | None = None) -> list[GroundingSourcePassage]:
        passages = [
            GroundingSourcePassage.model_validate(item.payload)
            for item in self.records(GroundingEvidenceKind.passage)
        ]
        if source_id is not None:
            passages = [item for item in passages if item.source_id == source_id]
        return sorted(passages, key=lambda item: (item.source_id, item.ordinal))

    def find_passages(
        self,
        source_id: str,
        query: str,
        *,
        max_passages: int = 5,
    ) -> list[GroundingSourcePassage]:
        terms = [term.casefold() for term in query.split() if term.strip()]
        ranked = []
        for passage in self.passages(source_id):
            text = passage.text.casefold()
            score = sum(text.count(term) for term in terms)
            if score:
                ranked.append((score, passage))
        ranked.sort(key=lambda item: (-item[0], item[1].ordinal))
        return [item[1] for item in ranked[: max(1, min(max_passages, 20))]]

    def open_passage(
        self,
        source_id: str,
        passage_id: str,
        *,
        context_before: int = 0,
        context_after: int = 0,
    ) -> list[GroundingSourcePassage]:
        passages = self.passages(source_id)
        index = next(
            (index for index, item in enumerate(passages) if item.id == passage_id),
            None,
        )
        if index is None:
            return []
        start = max(0, index - max(0, min(context_before, 4)))
        end = min(len(passages), index + max(0, min(context_after, 4)) + 1)
        return passages[start:end]

    def context_projection(
        self,
        *,
        max_passages: int = 12,
        max_chars: int = 9000,
    ) -> dict:
        selected = []
        used_chars = 0
        for passage in self.passages():
            if len(selected) >= max_passages or used_chars + len(passage.text) > max_chars:
                break
            record = next(
                item
                for item in self.records(GroundingEvidenceKind.passage)
                if item.payload.get("id") == passage.id
            )
            selected.append(
                {
                    "evidence_id": record.id,
                    "passage": passage.model_dump(mode="json"),
                    "lineage": record.lineage.model_dump(mode="json", exclude_none=True),
                }
            )
            used_chars += len(passage.text)
        counts = Counter(item.kind.value for item in self._records.values())
        return {
            "schema_version": 1,
            "record_count": len(self._records),
            "counts": dict(counts),
            "passages": selected,
        }

    def model_dump(self) -> dict:
        return {
            "schema_version": 1,
            "records": [
                item.model_dump(mode="json", exclude_none=True)
                for item in self._records.values()
            ],
        }
