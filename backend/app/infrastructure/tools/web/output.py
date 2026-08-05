"""Project fetched content into grounded tool output."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.domain.grounding.identity import (
    canonical_url,
    digest_text,
    segment_passages,
    snapshot_id,
    source_id,
)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def build_fetch_output(
    url: str,
    status_code: int,
    title: str | None,
    description: str | None,
    content: str,
    metadata: dict[str, Any],
    strategy: str,
    warnings: list[str],
    retrieved_at: str,
    min_quality_chars: int,
    *,
    requested_url: str | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    content = normalize_space(content)
    quality_score = min(1.0, len(content) / max(float(min_quality_chars), 1.0))
    canonical = canonical_url(str(metadata.get("canonical_url") or url))
    source = source_id(canonical)
    content_hash = digest_text(content)
    snapshot = snapshot_id(source, content_hash)
    passages = segment_passages(
        content,
        source=source,
        snapshot=snapshot,
        max_chars=900,
        overlap_chars=120,
        max_passages=32,
    )
    links = list(dict.fromkeys(metadata.get("links") or []))
    return {
        "url": url,
        "requested_url": requested_url or url,
        "final_url": url,
        "canonical_url": canonical,
        "status_code": status_code,
        "title": title,
        "description": description,
        "content": content,
        "metadata": metadata,
        "extraction_strategy": strategy,
        "quality_score": round(quality_score, 3),
        "content_length": len(content),
        "content_digest": content_hash,
        "source_id": source,
        "snapshot_id": snapshot,
        "segmentation_version": "passages.v1",
        "passages": [item.model_dump(mode="json") for item in passages],
        "links": links,
        "signals": {
            "content_length": len(content),
            "extraction_confidence": round(quality_score, 3),
            "published_at_detected": bool(metadata.get("published_at")),
            "truncated": truncated,
        },
        "source_type": infer_source_type(url, metadata),
        "warnings": warnings,
        "retrieved_at": retrieved_at,
    }


def quality_warnings(content: str, query: str, min_quality_chars: int) -> list[str]:
    warnings = []
    if len(content) < min_quality_chars:
        warnings.append("正文过短，可能不足以支撑总结。")
    if query and content and not has_query_overlap(query, content):
        warnings.append("正文与查询词重叠较少，需要在总结中谨慎使用。")
    return warnings


def has_query_overlap(query: str, content: str) -> bool:
    query_terms = {term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)}
    content_lower = content.lower()
    return any(term in content_lower for term in list(query_terms)[:6])


def infer_source_type(url: str, metadata: dict[str, Any]) -> str:
    if metadata.get("published_at"):
        return "article"
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    return "web_page"
