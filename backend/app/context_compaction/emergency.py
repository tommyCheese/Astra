from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.context_compaction import (
    CheckpointTrustMetadata,
    ChildContextCheckpointV2,
    ChildLocalFact,
    ContextEnvelope,
    ContextOwnerRole,
    ConversationContextCheckpointV2,
    RootContextCheckpointV2,
)


def deterministic_emergency_checkpoint(envelope: ContextEnvelope):
    now = datetime.now(timezone.utc)
    trust = CheckpointTrustMetadata(generated_from_canonical_state=True)
    summaries = tuple(
        text
        for item in (*envelope.compactable_body, *envelope.recent_tail)
        if (text := (item.summary or (item.content if isinstance(item.content, str) else "")))
    )[-20:]
    canonical_text = tuple(
        text
        for item in envelope.protected_prefix
        if (text := (item.summary or (item.content if isinstance(item.content, str) else "")))
    )
    if envelope.owner_type == ContextOwnerRole.conversation:
        return ConversationContextCheckpointV2(
            user_intent=canonical_text[-1] if canonical_text else envelope.purpose,
            completed_outcomes=summaries,
            trust=trust,
            created_at=now,
        )
    if envelope.owner_type == ContextOwnerRole.root_execution:
        return RootContextCheckpointV2(
            user_intent=canonical_text[-1] if canonical_text else envelope.purpose,
            next_steps=summaries,
            trust=trust,
            created_at=now,
        )
    refs = {ref.ref for ref in envelope.reference_manifest if ref.accessible}
    return ChildContextCheckpointV2(
        agent_execution_id=envelope.owner_id,
        manifest_hash=envelope.continuation.manifest_hash or "missing",
        contract_hash=envelope.continuation.contract_hash or "missing",
        local_facts=tuple(
            ChildLocalFact(text=text, confidence=0.0)
            for text in summaries
        ),
        remaining_budget=envelope.continuation.remaining_budget,
        trust=trust,
        created_at=now,
        artifact_refs=tuple(
            ref.ref for ref in envelope.reference_manifest if ref.kind == "artifact" and ref.ref in refs
        ),
        evidence_refs=tuple(
            ref.ref for ref in envelope.reference_manifest if ref.kind == "evidence" and ref.ref in refs
        ),
    )
