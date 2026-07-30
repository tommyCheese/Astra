from __future__ import annotations

from typing import Any

from app.grounding.identity import (
    candidate_id,
    canonical_url,
    digest_payload,
    digest_text,
    evidence_identity,
    search_trace_id,
    segment_passages,
    snapshot_id,
    source_id,
)
from app.grounding.schemas import (
    ConstraintAudit,
    EvidenceFragment,
    EvidenceKind,
    EvidenceLineage,
    SearchCandidate,
    SearchConstraints,
    SearchTrace,
    SourceSnapshot,
)


def _fragment(
    kind: EvidenceKind,
    model: Any,
    *,
    lineage: EvidenceLineage | None = None,
) -> EvidenceFragment:
    payload = model.model_dump(mode="json", exclude_none=True)
    identity = str(payload["id"])
    return EvidenceFragment(
        id=evidence_identity(kind.value, identity),
        kind=kind,
        evidence_key=f"{kind.value}:{identity}",
        payload_digest=digest_payload(payload),
        payload=payload,
        lineage=lineage or EvidenceLineage(),
    )


def fragments_from_web_result(
    tool_name: str,
    output: dict[str, Any],
    *,
    lineage: EvidenceLineage | None = None,
) -> list[EvidenceFragment]:
    data = dict(output.get("data") or output)
    if tool_name == "web_search" or "candidates" in data:
        return _search_fragments(data, lineage=lineage)
    if tool_name == "web_fetch" or "snapshot" in data or "content" in data:
        return _read_fragments(data, lineage=lineage)
    return []


def _search_fragments(
    data: dict[str, Any],
    *,
    lineage: EvidenceLineage | None,
) -> list[EvidenceFragment]:
    trace_payloads = list(data.get("search_traces") or [])
    if not trace_payloads:
        invocation_scope = lineage.tool_call_id if lineage is not None else None
        trace_payloads = [
            {
                "id": search_trace_id(
                    str(data.get("query") or ""),
                    0,
                    invocation_scope,
                ),
                "query": str(data.get("query") or ""),
                "provider": str(data.get("provider") or "unknown"),
                "constraints": data.get("constraints") or {},
                "constraint_audit": data.get("constraint_audit") or {},
                "retrieved_at": data.get("retrieved_at"),
            }
        ]
    traces: dict[str, SearchTrace] = {}
    fragments: list[EvidenceFragment] = []
    for index, raw in enumerate(trace_payloads):
        query = str(raw.get("query") or "")
        invocation_scope = lineage.tool_call_id if lineage is not None else None
        trace = SearchTrace(
            id=str(
                raw.get("id")
                or search_trace_id(query, index, invocation_scope)
            ),
            query=query,
            purpose=raw.get("purpose"),
            provider=str(raw.get("provider") or data.get("provider") or "unknown"),
            constraints=SearchConstraints.model_validate(raw.get("constraints") or {}),
            constraint_audit=ConstraintAudit.model_validate(
                raw.get("constraint_audit") or data.get("constraint_audit") or {}
            ),
            **({"retrieved_at": raw["retrieved_at"]} if raw.get("retrieved_at") else {}),
        )
        traces[trace.id] = trace
        fragments.append(_fragment(EvidenceKind.search_trace, trace, lineage=lineage))

    fallback_trace = next(iter(traces.values()))
    for rank, raw in enumerate(data.get("candidates") or [], start=1):
        url = str(raw.get("url") or "")
        if not url:
            continue
        trace_id = str(raw.get("search_trace_id") or fallback_trace.id)
        trace = traces.get(trace_id, fallback_trace)
        candidate = SearchCandidate(
            id=str(raw.get("candidate_id") or candidate_id(trace.id, url)),
            search_trace_id=trace.id,
            url=url,
            canonical_url=str(raw.get("canonical_url") or canonical_url(url)),
            title=str(raw.get("title") or ""),
            snippet=str(raw.get("snippet") or ""),
            provider=str(raw.get("provider") or trace.provider),
            provider_rank=int(raw.get("provider_rank") or raw.get("rank") or rank),
            display_link=raw.get("display_link"),
            published_at=raw.get("published_at"),
            source_type=str(raw.get("source_type") or "web"),
            **({"retrieved_at": raw["retrieved_at"]} if raw.get("retrieved_at") else {}),
            metadata=dict(raw.get("metadata") or {}),
        )
        fragments.append(_fragment(EvidenceKind.search_candidate, candidate, lineage=lineage))
    return fragments


def _read_fragments(
    data: dict[str, Any],
    *,
    lineage: EvidenceLineage | None,
) -> list[EvidenceFragment]:
    content = str(data.get("content") or "")
    url = str(data.get("final_url") or data.get("url") or "")
    canonical = canonical_url(str(data.get("canonical_url") or url))
    source = str(data.get("source_id") or source_id(canonical))
    content_hash = str(data.get("content_digest") or digest_text(content))
    snapshot = str(data.get("snapshot_id") or snapshot_id(source, content_hash))
    raw_passages = list(data.get("passages") or [])
    passages = (
        segment_passages(content, source=source, snapshot=snapshot)
        if not raw_passages
        else []
    )
    if raw_passages:
        from app.grounding.schemas import Passage

        passages = [Passage.model_validate(item) for item in raw_passages]
    metadata = dict(data.get("metadata") or {})
    model = SourceSnapshot(
        id=snapshot,
        source_id=source,
        requested_url=str(data.get("requested_url") or url),
        canonical_url=canonical,
        title=data.get("title"),
        description=data.get("description"),
        published_at=metadata.get("published_at"),
        **({"retrieved_at": data["retrieved_at"]} if data.get("retrieved_at") else {}),
        content_digest=content_hash,
        content_length=int(data.get("content_length") or len(content)),
        segmentation_version=str(data.get("segmentation_version") or "passages.v1"),
        extraction_strategy=data.get("extraction_strategy"),
        source_type=str(data.get("source_type") or "web"),
        artifact_ids=list(data.get("artifact_ids") or []),
        passage_ids=[item.id for item in passages],
        links=list(data.get("links") or []),
        signals=dict(data.get("signals") or {}),
        warnings=list(data.get("warnings") or []),
    )
    fragments = [_fragment(EvidenceKind.source_snapshot, model, lineage=lineage)]
    fragments.extend(
        _fragment(EvidenceKind.passage, passage, lineage=lineage) for passage in passages
    )
    return fragments
