from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from app.schemas.context_compaction import (
    ChildContextCheckpointV2,
    ContextEnvelope,
    ContextOwnerRole,
    ConversationContextCheckpointV2,
    RootContextCheckpointV2,
)

CheckpointV2 = RootContextCheckpointV2 | ConversationContextCheckpointV2 | ChildContextCheckpointV2


class CheckpointValidationError(ValueError):
    pass


FORBIDDEN_KEYS = frozenset(
    {
        "chain_of_thought",
        "hidden_reasoning",
        "system_prompt",
        "credentials",
        "api_key",
        "secret",
        "private_scratchpad",
        "parent_history",
        "sibling_context",
        "opaque_item",
        "encrypted_content",
    }
)


def validate_checkpoint_payload(
    payload: dict[str, Any],
    envelope: ContextEnvelope,
) -> CheckpointV2:
    _reject_forbidden_content(payload)
    schema: type[BaseModel]
    if envelope.owner_type == ContextOwnerRole.root_execution:
        schema = RootContextCheckpointV2
    elif envelope.owner_type == ContextOwnerRole.conversation:
        schema = ConversationContextCheckpointV2
    else:
        schema = ChildContextCheckpointV2
    try:
        checkpoint = schema.model_validate(payload)
    except ValidationError as exc:
        raise CheckpointValidationError(f"checkpoint_schema_invalid: {exc}") from exc
    _validate_binding(checkpoint, envelope)
    _validate_references(checkpoint, envelope)
    return cast(CheckpointV2, checkpoint)


def _reject_forbidden_content(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise CheckpointValidationError(f"forbidden_checkpoint_field:{key}")
            _reject_forbidden_content(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_content(child)


def _validate_binding(checkpoint: CheckpointV2, envelope: ContextEnvelope) -> None:
    if isinstance(checkpoint, ChildContextCheckpointV2):
        continuation = envelope.continuation
        if checkpoint.agent_execution_id != envelope.owner_id:
            raise CheckpointValidationError("child_execution_binding_mismatch")
        if checkpoint.manifest_hash != continuation.manifest_hash:
            raise CheckpointValidationError("child_manifest_hash_mismatch")
        if checkpoint.contract_hash != continuation.contract_hash:
            raise CheckpointValidationError("child_contract_hash_mismatch")


def _validate_references(checkpoint: CheckpointV2, envelope: ContextEnvelope) -> None:
    allowed = {ref.ref: ref for ref in envelope.reference_manifest if ref.accessible}
    referenced: set[str] = set()
    if isinstance(checkpoint, RootContextCheckpointV2):
        for fact in checkpoint.verified_facts:
            referenced.update(fact.evidence_refs)
        referenced.update(change.artifact_or_path_ref for change in checkpoint.workspace_changes)
        referenced.update(child.result_ref for child in checkpoint.child_results)
    elif isinstance(checkpoint, ChildContextCheckpointV2):
        referenced.update(checkpoint.evidence_refs)
        referenced.update(checkpoint.artifact_refs)
        referenced.update(
            fact.provenance_ref for fact in checkpoint.local_facts if fact.provenance_ref
        )
    missing = sorted(ref for ref in referenced if ref not in allowed)
    if missing:
        raise CheckpointValidationError(
            "checkpoint_reference_inaccessible:" + json.dumps(missing, ensure_ascii=False)
        )
