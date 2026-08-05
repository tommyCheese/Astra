from __future__ import annotations

from datetime import datetime, timezone

from app.common.schemas.context_compaction import (
    CheckpointTrustMetadata,
    ChildCompactionLocalFact,
    ChildContextCheckpointV2,
    CompactionContextEnvelope,
    ContextOwnerRole,
    ConversationContextCheckpointV2,
    RootContextCheckpointV2,
)


def deterministic_emergency_checkpoint(envelope: CompactionContextEnvelope):
    now = datetime.now(timezone.utc)
    trust = CheckpointTrustMetadata(generated_from_canonical_state=True)
    summaries = _item_summaries((*envelope.compactable_body, *envelope.recent_tail))[-20:]
    canonical_text = _item_summaries(envelope.protected_prefix)
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
    artifact_refs = _accessible_refs(envelope, "artifact")
    evidence_refs = _accessible_refs(envelope, "evidence")
    return ChildContextCheckpointV2(
        agent_execution_id=envelope.owner_id,
        manifest_hash=envelope.continuation.manifest_hash or "missing",
        contract_hash=envelope.continuation.contract_hash or "missing",
        local_facts=tuple(ChildCompactionLocalFact(text=text, confidence=0.0) for text in summaries),
        remaining_budget=envelope.continuation.remaining_budget,
        trust=trust,
        created_at=now,
        artifact_refs=artifact_refs,
        evidence_refs=evidence_refs,
    )


def _item_summaries(items) -> tuple[str, ...]:
    return tuple(text for item in items if (text := _item_text(item)))


def _item_text(item) -> str:
    return item.summary or (item.content if isinstance(item.content, str) else "")


def _accessible_refs(envelope: CompactionContextEnvelope, kind: str) -> tuple[str, ...]:
    return tuple(
        reference.ref
        for reference in envelope.reference_manifest
        if reference.accessible and reference.kind == kind
    )
