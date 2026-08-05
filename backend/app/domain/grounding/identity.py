from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.domain.grounding.schemas import Passage

SEGMENTATION_VERSION = "passages.v1"


def canonical_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in {"fbclid", "gclid", "msclkid"}
    ]
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname
    if port and not (
        parsed.scheme.lower() == "http" and port == 80
        or parsed.scheme.lower() == "https" and port == 443
    ):
        netloc = f"{hostname}:{port}"
    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path.rstrip("/") or "/",
            "",
            urlencode(query),
            "",
        )
    )


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def digest_text(value: str) -> str:
    return hashlib.sha256(normalized_text(value).encode("utf-8")).hexdigest()


def digest_payload(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def source_id(url: str) -> str:
    return stable_id("src", canonical_url(url))


def snapshot_id(source: str, content_digest: str, version: str = SEGMENTATION_VERSION) -> str:
    return stable_id("snap", source, content_digest, version)


def search_trace_id(
    query: str,
    ordinal: int = 0,
    invocation_scope: str | None = None,
) -> str:
    return stable_id(
        "search",
        normalized_text(query).casefold(),
        str(ordinal),
        invocation_scope or "unscoped",
    )


def candidate_id(trace_id: str, url: str) -> str:
    return stable_id("candidate", trace_id, canonical_url(url))


def evidence_identity(kind: str, *parts: str) -> str:
    return stable_id("ev", kind, *parts)


def segment_passages(
    content: str,
    *,
    source: str,
    snapshot: str,
    max_chars: int = 900,
    overlap_chars: int = 120,
    max_passages: int = 32,
) -> list[Passage]:
    text = normalized_text(content)
    if not text:
        return []
    max_chars = max(160, min(max_chars, 2000))
    overlap_chars = max(0, min(overlap_chars, max_chars // 2))
    passages: list[Passage] = []
    start = 0
    while start < len(text) and len(passages) < max_passages:
        target_end = min(len(text), start + max_chars)
        end = target_end
        if target_end < len(text):
            boundary = max(
                text.rfind("。", start + max_chars // 2, target_end),
                text.rfind(". ", start + max_chars // 2, target_end),
                text.rfind("；", start + max_chars // 2, target_end),
                text.rfind("; ", start + max_chars // 2, target_end),
            )
            if boundary > start:
                end = boundary + 1
        passage_text = text[start:end].strip()
        if passage_text:
            ordinal = len(passages)
            passages.append(
                Passage(
                    id=stable_id("passage", snapshot, str(ordinal), digest_text(passage_text)),
                    source_id=source,
                    snapshot_id=snapshot,
                    ordinal=ordinal,
                    text=passage_text,
                    start_offset=start,
                    end_offset=end,
                )
            )
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return passages
