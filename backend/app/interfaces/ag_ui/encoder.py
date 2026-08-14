"""Text SSE encoding for validated AG-UI events."""

import json
from typing import Any

from app.interfaces.ag_ui.metrics import ag_ui_metrics
from app.interfaces.ag_ui.schemas import validate_public_event


def encode_sse(event: dict[str, Any]) -> str:
    validated = validate_public_event(event)
    ag_ui_metrics.increment("events_emitted", event_type=str(validated["type"]))
    return f"data: {json.dumps(validated, ensure_ascii=False, separators=(',', ':'))}\n\n"
