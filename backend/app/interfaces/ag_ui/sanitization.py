from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from app.interfaces.ag_ui.metrics import ag_ui_metrics

MAX_ERROR_CHARS = 1_000
MAX_REASONING_CHARS = 4_000
MAX_TOOL_CHARS = 8_000
MAX_ACTIVITY_BYTES = 64_000
MAX_COLLECTION_ITEMS = 200
MAX_DEPTH = 8

SENSITIVE_KEY = re.compile(
    r"(^|_)(api_?key|authorization|cookie|credential|password|permission_bundle|secret|token)(_|$)", re.I
)
PRIVATE_PATH = re.compile(r"(?:^|\s)(?:/Users/|/home/|/private/|[A-Za-z]:\\)")


def bounded_text(value: Any, limit: int) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    ag_ui_metrics.increment("payload_truncations")
    return text[: max(0, limit - 1)] + "…", True


def _safe_url(value: str) -> str | None:
    if value.startswith("/api/artifacts/"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme == "https" and parsed.hostname:
        return value
    return None


def sanitize_public(value: Any, *, depth: int = 0) -> Any:
    if depth >= MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        return _sanitize_mapping(value, depth)
    if isinstance(value, (list, tuple)):
        return _sanitize_sequence(value, depth)
    if isinstance(value, str):
        return _sanitize_string(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return bounded_text(value, 500)[0]


def _sanitize_mapping(value: dict[Any, Any], depth: int) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    forbidden = {"traceback", "stack", "workspace_path", "continuation_token"}
    for raw_key, item in list(value.items())[:MAX_COLLECTION_ITEMS]:
        key = str(raw_key)[:200]
        if SENSITIVE_KEY.search(key) or key in forbidden:
            continue
        if key in {"url", "href", "content_url"} and isinstance(item, str):
            safe_url = _safe_url(item)
            if safe_url is not None:
                sanitized[key] = safe_url
            continue
        sanitized[key] = sanitize_public(item, depth=depth + 1)
    if len(value) > MAX_COLLECTION_ITEMS:
        sanitized["_truncated"] = True
    return sanitized


def _sanitize_sequence(value: list[Any] | tuple[Any, ...], depth: int) -> list[Any]:
    items = [sanitize_public(item, depth=depth + 1) for item in list(value)[:MAX_COLLECTION_ITEMS]]
    if len(value) > MAX_COLLECTION_ITEMS:
        items.append({"_truncated": True})
    return items


def _sanitize_string(value: str) -> str:
    if PRIVATE_PATH.search(value):
        return "[private path removed]"
    return bounded_text(value, MAX_TOOL_CHARS)[0]


def safe_error(payload: dict[str, Any]) -> dict[str, Any]:
    message, truncated = bounded_text(payload.get("message") or "Astra 无法完成本次运行。", MAX_ERROR_CHARS)
    result: dict[str, Any] = {
        "message": sanitize_public(message),
        "code": bounded_text(payload.get("code") or "RUN_FAILED", 120)[0],
    }
    if truncated:
        result["truncated"] = True
    return result


def safe_reasoning(payload: dict[str, Any]) -> tuple[str, bool]:
    value = payload.get("delta") if "delta" in payload else payload.get("summary", "")
    text, truncated = bounded_text(value, MAX_REASONING_CHARS)
    return str(sanitize_public(text)), truncated


def safe_tool_arguments(value: Any) -> dict[str, Any]:
    """Return complete, bounded JSON arguments without exposing invalid inputs."""
    if not isinstance(value, dict):
        return {}
    sanitized = sanitize_public(value)
    if not isinstance(sanitized, dict):
        return {}
    if _json_size(sanitized) <= MAX_TOOL_CHARS:
        return sanitized

    ag_ui_metrics.increment("payload_truncations", event_type="tool_arguments")
    bounded: dict[str, Any] = {"_truncated": True}
    for key, item in sanitized.items():
        candidate = {**bounded, key: item}
        if _json_size(candidate) > MAX_TOOL_CHARS:
            continue
        bounded[key] = item
    return bounded


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
