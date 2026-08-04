from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from app.memory.domain import normalize_memory_kind

logger = logging.getLogger("astra.model")


def normalize_memory_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    content = str(payload.get("content") or "").strip()
    if not content:
        return None
    scope = _memory_scope(payload.get("scope"))
    if scope is None:
        return None
    kind = normalize_memory_kind(str(payload.get("kind") or "semantic_fact"))
    if kind is None:
        return None
    normalized = dict(payload)
    normalized["content"] = content
    normalized["scope"] = scope
    normalized["kind"] = kind.value
    normalized["memory_key"] = _memory_key(payload, scope, kind.value, content)
    normalized["status"] = "candidate"
    if not isinstance(payload.get("structured_data"), dict):
        normalized["structured_data"] = {}
    if not isinstance(payload.get("provenance"), dict):
        normalized["provenance"] = {}
    normalized["confidence"] = _bounded_score(payload.get("confidence"))
    normalized["importance"] = _bounded_score(payload.get("importance"))
    normalized["utility_score"] = 0.0
    return normalized


def _memory_scope(value: object) -> str | None:
    scope = str(value or "run").strip().lower()
    if scope == "workspace":
        return None
    return scope if scope in {"run", "task", "session", "user"} else "run"


def _memory_key(payload: dict[str, Any], scope: str, kind: str, content: str) -> str:
    proposed = str(payload.get("memory_key") or "").strip()
    if (
        proposed
        and len(proposed) <= 240
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", proposed)
    ):
        return proposed
    structured_data = payload.get("structured_data")
    key_material = json.dumps(
        {
            "scope": scope,
            "kind": kind,
            "content": content,
            "structured_data": structured_data if isinstance(structured_data, dict) else {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"memory:{hashlib.sha256(key_material.encode('utf-8')).hexdigest()[:32]}"


def _bounded_score(value: object) -> float:
    try:
        return min(1.0, max(0.0, float(value if value is not None else 0.5)))
    except (TypeError, ValueError):
        return 0.5
