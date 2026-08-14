from __future__ import annotations

import json
from typing import Any

from app.interfaces.ag_ui.metrics import ag_ui_metrics
from app.interfaces.ag_ui.sanitization import MAX_ACTIVITY_BYTES, sanitize_public

ACTIVITY_EVENT_PREFIXES = {
    "plan.": "astra.plan",
    "agent_execution.": "astra.agent_tree",
    "subagent.": "astra.agent_tree",
    "verification.": "astra.verification",
    "artifact.": "astra.artifact",
    "tool_call.": "astra.tool_activity",
}


def activity_type_for(event_type: str) -> str | None:
    return next((activity for prefix, activity in ACTIVITY_EVENT_PREFIXES.items() if event_type.startswith(prefix)), None)


def activity_entity_id(activity_type: str, payload: dict[str, Any], run_id: str) -> str:
    candidates = {
        "astra.plan": ("plan_node_id", "node_id", "plan_id"),
        "astra.agent_tree": ("agent_execution_id", "execution_id", "child_run_id", "delegation_id"),
        "astra.verification": ("verification_id",),
        "astra.artifact": ("artifact_id",),
        "astra.tool_activity": ("tool_call_id",),
    }[activity_type]
    return str(next((payload[key] for key in candidates if payload.get(key)), run_id))


def activity_group_id(activity_type: str, payload: dict[str, Any], run_id: str) -> str:
    if activity_type == "astra.plan":
        return str(payload.get("plan_id") or run_id)
    if activity_type == "astra.agent_tree":
        return run_id
    return activity_entity_id(activity_type, payload, run_id)


def activity_snapshot(
    activity_type: str,
    entity_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    revision: int,
    source_event_id: int,
) -> dict[str, Any]:
    safe = sanitize_public(payload)
    oversized = len(json.dumps(payload, ensure_ascii=False, default=str).encode()) > MAX_ACTIVITY_BYTES
    if oversized:
        ag_ui_metrics.increment("payload_truncations", event_type=activity_type)
        safe = {"_truncated": True, "status": safe.get("status"), "id": entity_id}
    status = str(safe.get("status") or safe.get("state") or event_type.rsplit(".", 1)[-1])
    title = {
        "astra.plan": "执行计划",
        "astra.agent_tree": "Agent 协作",
        "astra.verification": "结果验证",
        "astra.artifact": "生成内容",
        "astra.tool_activity": "工具执行",
    }[activity_type]
    item = {"id": entity_id, "status": status, "details": safe}
    return {
        "schemaVersion": 1,
        "revision": revision,
        "sourceEventId": source_event_id,
        "title": title,
        "summary": status,
        "fallbackText": f"{title}：{status}",
        "order": [entity_id],
        "byId": {entity_id: item},
        "truncated": oversized,
    }


def merge_activity_entities(
    previous: dict[str, Any],
    current: dict[str, Any],
    entity_id: str,
    activity_type: str,
) -> dict[str, Any]:
    order = list(previous.get("order", []))
    if entity_id not in order:
        order.append(entity_id)
    by_id = {**previous.get("byId", {}), **current.get("byId", {})}
    merged = {**current, "order": order, "byId": by_id}
    if activity_type == "astra.agent_tree":
        statuses = [str(item.get("status", "unknown")) for item in by_id.values() if isinstance(item, dict)]
        merged["counts"] = {
            "active": sum(status in {"running", "executing", "created"} for status in statuses),
            "waiting": sum(status.startswith("waiting") for status in statuses),
            "completed": sum(status in {"completed", "succeeded"} for status in statuses),
            "failed": sum(status in {"failed", "blocked", "cancelled"} for status in statuses),
        }
        merged["fallbackText"] = (
            f"Agent 协作：运行 {merged['counts']['active']}，等待 {merged['counts']['waiting']}，"
            f"完成 {merged['counts']['completed']}，失败 {merged['counts']['failed']}"
        )
    return merged
