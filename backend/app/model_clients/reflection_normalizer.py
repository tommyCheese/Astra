"""Normalize reflection patches without leaking provider-specific shapes."""

from __future__ import annotations

import json
from typing import Any


def normalize_reflection_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.update(
        trigger=str(payload.get("trigger") or "adaptive"),
        summary=str(payload.get("summary") or "已检查当前结果。"),
        next_action=str(payload.get("next_action") or "continue"),
    )
    patch = payload.get("patch")
    normalized["patch"] = _normalize_patch(patch, payload) if isinstance(patch, dict) else None
    return normalized


def _normalize_patch(patch: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(patch)
    normalized["level"] = str(patch.get("level") or payload.get("level") or "local")
    normalized["invalidated_assumption_ids"] = _list_or_empty(
        patch.get("invalidated_assumption_ids")
    )
    normalized["criterion_updates"] = (
        patch.get("criterion_updates") if isinstance(patch.get("criterion_updates"), dict) else {}
    )
    normalized["terminal_intent"] = _terminal_intent(patch.get("terminal_intent"))
    normalized["fact_updates"] = _fact_updates(patch.get("fact_updates"))
    normalized["added_verification_requirements"] = _requirements(
        patch.get("added_verification_requirements")
    )
    return normalized


def _terminal_intent(value: object) -> object:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _fact_updates(value: object) -> list[dict[str, Any]]:
    facts = []
    for index, raw in enumerate(_list_or_empty(value), start=1):
        if not isinstance(raw, dict):
            continue
        statement = raw.get("statement") or raw.get("add")
        if statement:
            facts.append(_fact(raw, index, str(statement)))
    return facts


def _fact(raw: dict[str, Any], index: int, statement: str) -> dict[str, Any]:
    provenance = raw.get("provenance")
    conflicts = raw.get("conflicts_with")
    return {
        "id": str(raw.get("id") or f"reflection-fact-{index}"),
        "statement": statement,
        "provenance": provenance
        if isinstance(provenance, dict)
        else {"source": "model_reflection"},
        "confidence": raw.get("confidence", 0.5),
        "conflicts_with": conflicts if isinstance(conflicts, list) else [],
    }


def _requirements(value: object) -> list[dict[str, Any]]:
    requirements = []
    for index, raw in enumerate(_list_or_empty(value), start=1):
        if isinstance(raw, str):
            requirements.append({"id": f"reflection-validator-{index}", "validator": raw})
        elif isinstance(raw, dict) and raw.get("validator"):
            requirements.append(
                {
                    **raw,
                    "id": str(raw.get("id") or f"reflection-validator-{index}"),
                }
            )
    return requirements


def _list_or_empty(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
