"""Normalize model-authored task contracts into the stable Astra shape."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("astra.model")


def normalize_contract_payload(payload: dict[str, Any], goal: str) -> dict[str, Any]:
    reported_goal = str(payload.get("original_goal") or "").strip()
    if _goal_mismatch(reported_goal, goal):
        logger.warning(
            "model.contract.goal_mismatch expected_chars=%s reported_chars=%s fallback=default",
            len(goal),
            len(reported_goal),
        )
        return _default_contract(goal)
    normalized = dict(payload)
    normalized["original_goal"] = goal.strip()
    _normalize_text_lists(normalized)
    normalized["assumptions"] = _normalize_assumptions(normalized.get("assumptions"))
    normalized["success_criteria"] = _normalize_criteria(normalized.get("success_criteria"), goal)
    normalized["verification_requirements"] = _normalize_requirements(
        normalized.get("verification_requirements")
    )
    _normalize_ambiguity(normalized)
    return normalized


def _goal_mismatch(reported_goal: str, expected_goal: str) -> bool:
    return bool(
        reported_goal and normalize_goal_text(reported_goal) != normalize_goal_text(expected_goal)
    )


def _default_contract(goal: str) -> dict[str, Any]:
    stripped_goal = goal.strip()
    return {
        "original_goal": stripped_goal,
        "deliverables": [f"回复用户请求：{stripped_goal}"],
        "constraints": [],
        "prohibited_actions": ["执行未注册或未授权的工具"],
        "assumptions": [],
        "success_criteria": [
            {
                "id": "criterion-result",
                "description": f"正确回应用户请求：{stripped_goal}",
                "mandatory": True,
                "verification_method": "task_adapter",
            }
        ],
        "verification_requirements": [{"id": "verify-result", "validator": "task_adapter"}],
        "risk_level": "low",
        "ambiguity_status": "clear",
        "clarification_question": None,
    }


def _normalize_text_lists(contract: dict[str, Any]) -> None:
    for field in ("deliverables", "constraints", "prohibited_actions"):
        value = contract.get(field, [])
        contract[field] = value if isinstance(value, list) else [str(value)]


def _items(value: object) -> list[Any]:
    if not value:
        return []
    return value if isinstance(value, list) else [value]


def _normalize_assumptions(value: object) -> list[dict[str, Any]]:
    assumptions = []
    for index, raw in enumerate(_items(value), start=1):
        item = dict(raw) if isinstance(raw, dict) else {"statement": str(raw)}
        item["id"] = str(item.get("id") or f"assumption-{index}")
        item["statement"] = str(item.get("statement") or item.get("description") or "未声明的假设")
        assumptions.append(item)
    return assumptions


def _normalize_criteria(value: object, goal: str) -> list[dict[str, Any]]:
    criteria = []
    for index, raw in enumerate(_items(value), start=1):
        item = dict(raw) if isinstance(raw, dict) else {"description": str(raw)}
        item["id"] = str(item.get("id") or f"criterion-{index}")
        item["description"] = str(
            item.get("description") or item.get("criterion") or f"正确回应用户请求：{goal}"
        )
        item["verification_method"] = str(item.get("verification_method") or "task_adapter")
        criteria.append(item)
    return criteria


def _normalize_requirements(value: object) -> list[dict[str, Any]]:
    requirements = []
    for index, raw in enumerate(_items(value), start=1):
        item = dict(raw) if isinstance(raw, dict) else {"validator": str(raw)}
        item["id"] = str(item.get("id") or f"verify-{index}")
        item["validator"] = str(item.get("validator") or "task_adapter")
        requirements.append(item)
    return requirements


def _normalize_ambiguity(contract: dict[str, Any]) -> None:
    ambiguity = str(contract.get("ambiguity_status") or "clear").lower()
    contract["ambiguity_status"] = ambiguity if ambiguity in {"clear", "ambiguous"} else "clear"
    if contract["ambiguity_status"] == "clear":
        contract["clarification_question"] = None


def normalize_goal_text(value: str) -> str:
    return "".join(value.lower().split()).strip("。！？!?.,，")
