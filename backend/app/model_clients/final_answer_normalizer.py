"""Normalize model-authored final answers into the public result contract."""

from __future__ import annotations

from typing import Any

from app.grounding.identity import stable_id


def normalize_final_answer_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["summary"] = str(normalized.get("summary") or "已完成回复。")
    normalized["findings"] = _findings(normalized.get("findings"))
    normalized["claims"] = _claims(normalized.get("claims"))
    normalized["citations"] = _citations(normalized.get("citations"))
    normalized["sources"] = _object_or_url_items(normalized.get("sources"))
    _normalize_supplementary_lists(normalized)
    return normalized


def _findings(value: object) -> list[dict[str, Any]]:
    findings = []
    for raw in _items(value):
        item = dict(raw) if isinstance(raw, dict) else {"text": str(raw)}
        item["text"] = str(item.get("text") or item.get("finding") or "")
        item["source_urls"] = _string_list(item.get("source_urls"))
        artifact_ids = item.get("artifact_ids")
        item["artifact_ids"] = (
            [identifier for identifier in artifact_ids if isinstance(identifier, str)]
            if isinstance(artifact_ids, list)
            else []
        )
        findings.append(item)
    return findings


def _claims(value: object) -> list[dict[str, Any]]:
    claims = []
    for index, raw in enumerate(_items(value)):
        item = dict(raw) if isinstance(raw, dict) else {"text": str(raw)}
        text = str(item.get("text") or "")
        item.update(
            text=text,
            id=str(item.get("id") or stable_id("claim", str(index), text)),
            evidence_refs=_string_list(item.get("evidence_refs")),
            material=bool(item.get("material", True)),
            support_status=_support_status(item.get("support_status")),
        )
        claims.append(item)
    return claims


def _support_status(value: object) -> str:
    status = str(value or "unverified").strip().lower()
    return status if status in {"unverified", "supported", "unsupported"} else "unverified"


def _citations(value: object) -> list[dict[str, Any]]:
    citations = []
    for index, raw in enumerate(_items(value)):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        claim_id = str(item.get("claim_id") or "")
        evidence_ref = str(item.get("evidence_ref") or "")
        item.update(
            claim_id=claim_id,
            evidence_ref=evidence_ref,
            id=str(item.get("id") or stable_id("citation", str(index), claim_id, evidence_ref)),
        )
        citations.append(item)
    return citations


def _normalize_supplementary_lists(answer: dict[str, Any]) -> None:
    object_fields = ("failed_sources", "source_quality", "conflicts", "memory_references")
    text_fields = ("caveats", "verification_notes")
    for field in object_fields:
        answer[field] = [item for item in _items(answer.get(field)) if isinstance(item, dict)]
    for field in text_fields:
        answer[field] = [str(item) for item in _items(answer.get(field))]


def _object_or_url_items(value: object) -> list[dict[str, Any]]:
    return [item if isinstance(item, dict) else {"url": str(item)} for item in _items(value)]


def _items(value: object) -> list[Any]:
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: object) -> list[str]:
    return [str(item) for item in _items(value)]
