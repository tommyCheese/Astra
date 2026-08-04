"""Normalize provider plan JSON into typed-planning input."""

from __future__ import annotations

from typing import Any

from app.schemas.agent.planning import TaskContract


def normalize_plan_payload(
    payload: dict[str, Any],
    *,
    contract: TaskContract,
) -> dict[str, Any]:
    criterion_ids = [criterion.id for criterion in contract.success_criteria]
    return {
        "nodes": [
            _normalize_node(raw_node, index, criterion_ids)
            for index, raw_node in enumerate(_items(payload.get("nodes")), start=1)
        ]
    }


def _normalize_node(
    raw_node: object,
    index: int,
    default_criterion_ids: list[str],
) -> dict[str, Any]:
    node = dict(raw_node) if isinstance(raw_node, dict) else {"title": str(raw_node)}
    node_key = str(node.get("node_key") or f"step-{index}")
    title = str(node.get("title") or node_key)
    criterion_refs = node.get("success_criteria_refs") or default_criterion_ids
    return {
        "node_key": node_key,
        "title": title,
        "intent": str(node.get("intent") or title),
        "depends_on": _string_list(node.get("depends_on")),
        "required_capabilities": _string_list(node.get("required_capabilities")),
        "success_criteria_refs": _string_list(criterion_refs),
        "expected_outcome": _expected_outcome(node.get("expected_outcome")),
        "risk_level": str(node.get("risk_level") or "low"),
        "optional": bool(node.get("optional", False)),
    }


def _expected_outcome(value: object) -> dict[str, Any]:
    expected = value if isinstance(value, dict) else {}
    return {
        "kind": str(expected.get("kind") or "step_result"),
        "success_condition": str(
            expected.get("success_condition") or "step completed with accepted evidence"
        ),
        "required_fields": _string_list(expected.get("required_fields")),
    }


def _items(value: object) -> list[Any]:
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _string_list(value: object) -> list[str]:
    return [str(item) for item in _items(value)]
