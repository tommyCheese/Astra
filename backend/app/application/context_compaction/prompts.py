from __future__ import annotations

import json

from app.application.context_compaction.policy import CompactionPolicy
from app.common.schemas.context_compaction import ContextEnvelope, ContextOwnerRole

PROMPT_VERSION = "astra-context-checkpoint-v2"


def build_compaction_prompt(envelope: ContextEnvelope, policy: CompactionPolicy) -> str:
    role_instructions = {
        ContextOwnerRole.conversation: (
            "Preserve current user intent, corrections, constraints, decisions, completed outcomes, "
            "open issues and next steps. Do not invent verified facts."
        ),
        ContextOwnerRole.root_execution: (
            "Preserve global continuity, verified facts only with supplied Evidence references, "
            "Plan progress, workspace changes, accepted child results, failures and next steps."
        ),
        ContextOwnerRole.child_execution: (
            "Preserve only child-local progress, facts with accessible provenance, references, "
            "failures, open issues, continuation answers, remaining budget and next action."
        ),
    }
    payload = envelope.model_dump(mode="json")
    return "\n".join(
        (
            f"Astra checkpoint protocol: {PROMPT_VERSION}",
            "Return exactly one JSON object. Do not return prose, markdown, hidden reasoning, "
            "credentials, authorization changes, or Provider-specific/opaque state.",
            f"Required schema: {policy.checkpoint_schema}.",
            role_instructions[envelope.owner_type],
            "The protected_prefix is canonical and must not be summarized as an authority source. "
            "Merge prior_checkpoint cumulatively; do not nest or quote it as another summary.",
            "ContextEnvelope JSON:",
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    )
