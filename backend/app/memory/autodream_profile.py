from __future__ import annotations

import json
from typing import Any

from app.agent_profile.profile import AgentProfile, ModelOperation
from app.memory.consolidation import (
    ConsolidationValidationError,
    canonical_digest,
    canonical_json,
)


def autodream_profile_snapshot(profile: AgentProfile) -> dict[str, Any]:
    selected = profile.documents_for(ModelOperation.AUTODREAM)
    selected_names = tuple(document.name for document in selected)
    if selected_names != ("identity", "memory", "autodream"):
        raise ConsolidationValidationError(
            "AutoDream Profile selection must be identity, memory, and autodream"
        )
    payload = {
        "operation": ModelOperation.AUTODREAM.value,
        "profile": profile.snapshot(),
        "selected_documents": [document.safe_metadata() for document in selected],
    }
    normalized = json.loads(canonical_json(payload))
    return {
        **normalized,
        "snapshot_hash": canonical_digest(normalized),
    }
