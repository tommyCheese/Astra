from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("astra.model")


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        if start < 0:
            raise
        payload, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(payload, dict):
        raise ValueError("Model JSON root must be an object")
    return payload


def find_json_string_field(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if isinstance(value, str):
        return value
    for nested in payload.values():
        if isinstance(nested, dict):
            found = find_json_string_field(nested, field)
            if found:
                return found
    return ""
