from __future__ import annotations

import json
import re
from typing import Any


class CheckpointParseError(ValueError):
    pass


def extract_json_object(text: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(text, dict):
        return text
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end < start:
        raise CheckpointParseError("checkpoint output contains no JSON object")
    candidate = candidate[start : end + 1]
    repairs = (
        candidate,
        re.sub(r",\s*([}\]])", r"\1", candidate),
    )
    last_error: Exception | None = None
    for repaired in repairs:
        try:
            value = json.loads(repaired)
            if not isinstance(value, dict):
                raise CheckpointParseError("checkpoint JSON must be an object")
            return value
        except (json.JSONDecodeError, CheckpointParseError) as exc:
            last_error = exc
    raise CheckpointParseError(f"invalid checkpoint JSON: {last_error}")
