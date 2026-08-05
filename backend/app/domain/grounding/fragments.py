from __future__ import annotations

from typing import Any

from app.domain.grounding.identity import (
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
from app.domain.grounding.schemas import (
    GroundingConstraintAudit,
    GroundingEvidenceFragment,
    GroundingEvidenceKind,
    GroundingEvidenceLineage,
    GroundingSearchCandidate,
    GroundingSearchConstraints,
    GroundingSearchTrace,
    GroundingSourceSnapshot,
)


def _fragment(
    kind: GroundingEvidenceKind,
    model: Any,
    *,
    lineage: GroundingEvidenceLineage | None = None,
) -> GroundingEvidenceFragment:
    payload = model.model_dump(mode="json", exclude_none=True)
    identity = str(payload["id"])
    return GroundingEvidenceFragment(
        id=evidence_identity(kind.value, identity),
        kind=kind,
        evidence_key=f"{kind.value}:{identity}",
        payload_digest=digest_payload(payload),
        payload=payload,
        lineage=lineage or GroundingEvidenceLineage(),
    )


def fragments_from_web_result(
    tool_name: str,
    output: dict[str, Any],
    *,
    lineage: GroundingEvidenceLineage | None = None,
) -> list[GroundingEvidenceFragment]:
    data = dict(output.get("data") or output)
    if tool_name == "web_search" or "candidates" in data:
        return _search_fragments(data, lineage=lineage)
    if tool_name == "web_fetch" or "snapshot" in data or "content" in data:
        return _read_fragments(data, lineage=lineage)
    return []


def _search_fragments(
    data: dict[str, Any],
    *,
    lineage: GroundingEvidenceLineage | None,
) -> list[GroundingEvidenceFragment]:
    traces = _build_search_traces(data, lineage)
    fragments = [
        _fragment(GroundingEvidenceKind.search_trace, trace, lineage=lineage)
        for trace in traces.values()
    ]
    fallback_trace = next(iter(traces.values()))
    fragments.extend(
        _fragment(GroundingEvidenceKind.search_candidate, candidate, lineage=lineage)
        for rank, raw in enumerate(data.get("candidates") or [], start=1)
        if (candidate := _build_search_candidate(raw, rank, traces, fallback_trace))
    )
    return fragments


def _build_search_traces(
    data: dict[str, Any], lineage: GroundingEvidenceLineage | None
) -> dict[str, GroundingSearchTrace]:
    raw_traces = list(data.get("search_traces") or [_fallback_trace(data, lineage)])
    traces = [
        _build_search_trace(raw, index, data, lineage)
        for index, raw in enumerate(raw_traces)
    ]
    return {trace.id: trace for trace in traces}


def _fallback_trace(
    data: dict[str, Any], lineage: GroundingEvidenceLineage | None
) -> dict[str, Any]:
    query = str(data.get("query") or "")
    invocation_scope = lineage.tool_call_id if lineage else None
    return {
        "id": search_trace_id(query, 0, invocation_scope),
        "query": query,
        "provider": str(data.get("provider") or "unknown"),
        "constraints": data.get("constraints") or {},
        "constraint_audit": data.get("constraint_audit") or {},
        "retrieved_at": data.get("retrieved_at"),
    }


def _build_search_trace(
    raw: dict[str, Any],
    index: int,
    data: dict[str, Any],
    lineage: GroundingEvidenceLineage | None,
) -> GroundingSearchTrace:
    query = str(raw.get("query") or "")
    invocation_scope = lineage.tool_call_id if lineage else None
    retrieved_at = {"retrieved_at": raw["retrieved_at"]} if raw.get("retrieved_at") else {}
    return GroundingSearchTrace(
        id=str(raw.get("id") or search_trace_id(query, index, invocation_scope)),
        query=query,
        purpose=raw.get("purpose"),
        provider=str(raw.get("provider") or data.get("provider") or "unknown"),
        constraints=GroundingSearchConstraints.model_validate(raw.get("constraints") or {}),
        constraint_audit=GroundingConstraintAudit.model_validate(
            raw.get("constraint_audit") or data.get("constraint_audit") or {}
        ),
        **retrieved_at,
    )


def _build_search_candidate(
    raw: dict[str, Any],
    rank: int,
    traces: dict[str, GroundingSearchTrace],
    fallback: GroundingSearchTrace,
) -> GroundingSearchCandidate | None:
    url = str(raw.get("url") or "")
    if not url:
        return None
    trace = traces.get(str(raw.get("search_trace_id") or fallback.id), fallback)
    retrieved_at = _optional_timestamp(raw)
    identity = str(raw.get("candidate_id") or candidate_id(trace.id, url))
    provider_rank = int(raw.get("provider_rank") or raw.get("rank") or rank)
    return GroundingSearchCandidate(
        id=identity,
        search_trace_id=trace.id,
        url=url,
        canonical_url=str(raw.get("canonical_url") or canonical_url(url)),
        title=str(raw.get("title") or ""),
        snippet=str(raw.get("snippet") or ""),
        provider=str(raw.get("provider") or trace.provider),
        provider_rank=provider_rank,
        display_link=raw.get("display_link"),
        published_at=raw.get("published_at"),
        source_type=str(raw.get("source_type") or "web"),
        metadata=dict(raw.get("metadata") or {}),
        **retrieved_at,
    )


def _optional_timestamp(payload: dict[str, Any]) -> dict[str, Any]:
    return {"retrieved_at": payload["retrieved_at"]} if payload.get("retrieved_at") else {}


def _read_fragments(
    data: dict[str, Any],
    *,
    lineage: GroundingEvidenceLineage | None,
) -> list[GroundingEvidenceFragment]:
    model, passages = _build_source_snapshot(data)
    fragments = [_fragment(GroundingEvidenceKind.source_snapshot, model, lineage=lineage)]
    fragments.extend(
        _fragment(GroundingEvidenceKind.passage, passage, lineage=lineage)
        for passage in passages
    )
    return fragments


def _build_source_snapshot(data: dict[str, Any]):
    identity = _snapshot_identity(data)
    passages = _snapshot_passages(data, **identity)
    return _snapshot_model(data, passages, **identity), passages


def _snapshot_identity(data: dict[str, Any]) -> dict[str, str]:
    content = str(data.get("content") or "")
    url = str(data.get("final_url") or data.get("url") or "")
    canonical = canonical_url(str(data.get("canonical_url") or url))
    source = str(data.get("source_id") or source_id(canonical))
    content_hash = str(data.get("content_digest") or digest_text(content))
    snapshot = str(data.get("snapshot_id") or snapshot_id(source, content_hash))
    return {
        "content": content,
        "url": url,
        "canonical": canonical,
        "source": source,
        "content_hash": content_hash,
        "snapshot": snapshot,
    }


def _snapshot_passages(data: dict[str, Any], *, content: str, source: str, snapshot: str, **_):
    from app.domain.grounding.schemas import GroundingSourcePassage

    raw_passages = list(data.get("passages") or [])
    if raw_passages:
        return [GroundingSourcePassage.model_validate(item) for item in raw_passages]
    return segment_passages(content, source=source, snapshot=snapshot)


def _snapshot_model(
    data: dict[str, Any],
    passages,
    *,
    content: str,
    url: str,
    canonical: str,
    source: str,
    content_hash: str,
    snapshot: str,
):
    metadata = dict(data.get("metadata") or {})
    return GroundingSourceSnapshot(
        id=snapshot,
        source_id=source,
        requested_url=str(data.get("requested_url") or url),
        canonical_url=canonical,
        title=data.get("title"),
        description=data.get("description"),
        published_at=metadata.get("published_at"),
        **_optional_timestamp(data),
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
