from __future__ import annotations

import json
from typing import Any

from app.interfaces.ag_ui.metrics import ag_ui_metrics


def escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def json_patch(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        patch: list[dict[str, Any]] = []
        for key in sorted(before.keys() - after.keys()):
            patch.append({"op": "remove", "path": f"{path}/{escape_pointer(str(key))}"})
        for key in sorted(after.keys() - before.keys()):
            patch.append({"op": "add", "path": f"{path}/{escape_pointer(str(key))}", "value": after[key]})
        for key in sorted(before.keys() & after.keys()):
            patch.extend(json_patch(before[key], after[key], f"{path}/{escape_pointer(str(key))}"))
        return patch
    return [{"op": "replace", "path": path or "", "value": after}]


def bounded_patch(before: dict[str, Any], after: dict[str, Any], *, ratio: float = 0.65) -> list[dict[str, Any]] | None:
    patch = json_patch(before, after)
    patch_size = len(json.dumps(patch, ensure_ascii=False, separators=(",", ":")))
    snapshot_size = max(1, len(json.dumps(after, ensure_ascii=False, separators=(",", ":"))))
    if patch_size <= snapshot_size * ratio:
        return patch
    ag_ui_metrics.increment("patch_fallbacks")
    return None
