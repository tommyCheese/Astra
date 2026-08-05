"""Normalize model-authored contract, result, Memory, Plan, and reflection payloads."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from app.common.schemas.agent.planning import TaskContract
from app.domain.grounding.identity import stable_id
from app.domain.memory import normalize_memory_kind

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

def _string_list(value: object) -> list[str]:
    return [str(item) for item in _items(value)]

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

