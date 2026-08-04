from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.memory.consolidation import (
    CONSOLIDATION_PROPOSAL_SCHEMA_VERSION,
    MAX_MODEL_OUTPUT_BYTES,
    MAX_PROPOSAL_CONTENT_CHARS,
    MAX_PROPOSAL_OPERATIONS,
    MAX_TOTAL_PROPOSAL_CONTENT_CHARS,
    ConsolidationAction,
    ConsolidationInputManifest,
    ConsolidationOperation,
    ConsolidationProposal,
    ConsolidationValidationError,
    FrozenMemoryInput,
    _bounded_ids,
    _bounded_number,
    _bounded_string,
    _json_object,
    _strict_fields,
    canonical_json,
    equivalent_content_key,
    normalize_memory_key,
    normalize_text,
)
from app.memory.domain import MemoryKind, normalize_memory_kind


def normalize_model_output(
    raw_output: str | Mapping[str, Any],
    *,
    producer: str = "model",
) -> ConsolidationProposal:
    decoded = _decode_output(raw_output)
    raw_operations = _raw_operations(decoded)
    operations: list[ConsolidationOperation] = []
    replaced_ids: set[str] = set()
    total_content_chars = 0
    for index, raw_operation in enumerate(raw_operations):
        operation = _normalize_operation(index, raw_operation, replaced_ids)
        total_content_chars += len(operation.content)
        if total_content_chars > MAX_TOTAL_PROPOSAL_CONTENT_CHARS:
            raise ConsolidationValidationError("Proposal exceeds the total content character limit")
        operations.append(operation)
    output_keys = [operation.memory_key for operation in operations]
    if len(output_keys) != len(set(output_keys)):
        raise ConsolidationValidationError(
            "Proposal cannot create multiple outputs for one normalized Memory key"
        )
    return ConsolidationProposal.build(producer=producer, operations=operations)


def _decode_output(raw_output: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(raw_output, str):
        if len(raw_output.encode("utf-8")) > MAX_MODEL_OUTPUT_BYTES:
            raise ConsolidationValidationError("Model consolidation output is too large")
        try:
            decoded = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ConsolidationValidationError(
                "Model consolidation output must be valid JSON"
            ) from exc
    elif isinstance(raw_output, Mapping):
        decoded = dict(raw_output)
    else:
        raise ConsolidationValidationError("Model consolidation output must be a JSON object")
    if not isinstance(decoded, Mapping):
        raise ConsolidationValidationError("Model consolidation output must be a JSON object")
    if len(canonical_json(decoded).encode("utf-8")) > MAX_MODEL_OUTPUT_BYTES:
        raise ConsolidationValidationError("Model consolidation output is too large")
    return decoded


def _raw_operations(decoded: Mapping[str, Any]) -> list[Any]:
    _strict_fields(
        decoded,
        allowed={"schema_version", "operations"},
        required={"schema_version", "operations"},
        label="Model consolidation output",
    )
    if decoded["schema_version"] != CONSOLIDATION_PROPOSAL_SCHEMA_VERSION:
        raise ConsolidationValidationError("Unsupported consolidation proposal schema")
    operations = decoded["operations"]
    if not isinstance(operations, list):
        raise ConsolidationValidationError("Proposal operations must be a list")
    if len(operations) > MAX_PROPOSAL_OPERATIONS:
        raise ConsolidationValidationError(
            f"Proposal exceeds the {MAX_PROPOSAL_OPERATIONS} operation limit"
        )
    return operations


def _normalize_operation(
    index: int,
    raw_operation: Any,
    replaced_ids: set[str],
) -> ConsolidationOperation:
    if not isinstance(raw_operation, Mapping):
        raise ConsolidationValidationError(f"Proposal operation {index} must be an object")
    _validate_operation_fields(index, raw_operation)
    action = _action(index, raw_operation["action"])
    kind = _kind(index, raw_operation["kind"])
    scope = _scope(index, raw_operation["scope"])
    replacement_ids = _bounded_ids(
        raw_operation.get("replace_memory_ids", []),
        label=f"Proposal operation {index} replace_memory_ids",
        required=action is ConsolidationAction.replace,
    )
    _reserve_replacements(index, action, replacement_ids, replaced_ids)
    operation = ConsolidationOperation.build(
        action=action,
        memory_key=_bounded_string(
            raw_operation["memory_key"], label=f"Proposal operation {index} Memory key", maximum=240
        ),
        kind=kind,
        scope=scope,
        content=_bounded_string(
            raw_operation["content"],
            label=f"Proposal operation {index} content",
            maximum=MAX_PROPOSAL_CONTENT_CHARS,
        ),
        structured_data=_structured_data(index, raw_operation.get("structured_data", {})),
        confidence=_bounded_number(
            raw_operation.get("confidence", 0.8),
            label=f"Proposal operation {index} confidence",
            minimum=0.0,
            maximum=1.0,
        ),
        importance=_bounded_number(
            raw_operation.get("importance", 0.5),
            label=f"Proposal operation {index} importance",
            minimum=0.0,
            maximum=1.0,
        ),
        source_memory_ids=_bounded_ids(
            raw_operation["source_memory_ids"],
            label=f"Proposal operation {index} source_memory_ids",
            required=True,
        ),
        replace_memory_ids=replacement_ids,
    )
    supplied_id = raw_operation.get("operation_id")
    if supplied_id is not None and normalize_text(str(supplied_id)) != operation.operation_id:
        raise ConsolidationValidationError(
            f"Proposal operation {index} operation_id does not match its content"
        )
    return operation


def _validate_operation_fields(index: int, operation: Mapping[str, Any]) -> None:
    _strict_fields(
        operation,
        allowed={
            "action",
            "memory_key",
            "kind",
            "scope",
            "content",
            "structured_data",
            "confidence",
            "importance",
            "source_memory_ids",
            "replace_memory_ids",
            "operation_id",
        },
        required={"action", "memory_key", "kind", "scope", "content", "source_memory_ids"},
        label=f"Proposal operation {index}",
    )


def _action(index: int, value: Any) -> ConsolidationAction:
    try:
        return ConsolidationAction(str(value))
    except ValueError as exc:
        raise ConsolidationValidationError(
            f"Proposal operation {index} has an unsupported action"
        ) from exc


def _kind(index: int, value: Any) -> str:
    raw_kind = _bounded_string(value, label=f"Proposal operation {index} kind", maximum=80)
    kind = normalize_memory_kind(raw_kind)
    if not isinstance(kind, MemoryKind):
        raise ConsolidationValidationError(
            f"Proposal operation {index} has an unsupported Memory kind"
        )
    return kind.value


def _scope(index: int, value: Any) -> str:
    scope = _bounded_string(value, label=f"Proposal operation {index} scope", maximum=40)
    if scope not in {"run", "task", "session", "user"}:
        raise ConsolidationValidationError(
            f"Proposal operation {index} has an unsupported Memory scope"
        )
    return scope


def _structured_data(index: int, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ConsolidationValidationError(
            f"Proposal operation {index} structured_data must be an object"
        )
    return _json_object(value)


def _reserve_replacements(
    index: int,
    action: ConsolidationAction,
    replacement_ids: tuple[str, ...],
    replaced_ids: set[str],
) -> None:
    if action is ConsolidationAction.add and replacement_ids:
        raise ConsolidationValidationError(
            f"Proposal operation {index} add action cannot replace Memory"
        )
    if replaced_ids.intersection(replacement_ids):
        raise ConsolidationValidationError(
            "Proposal cannot replace the same Memory in multiple operations"
        )
    replaced_ids.update(replacement_ids)


def deterministic_duplicate_proposal(
    manifest: ConsolidationInputManifest,
) -> ConsolidationProposal:
    groups: dict[tuple[str, str, str, str], list[FrozenMemoryInput]] = {}
    for item in manifest.items:
        if item.status != "active":
            continue
        group_key = (
            normalize_memory_key(item.memory_key),
            equivalent_content_key(item.content),
            item.kind,
            item.scope,
        )
        groups.setdefault(group_key, []).append(item)
    operations: list[ConsolidationOperation] = []
    for group_key, items in sorted(groups.items()):
        if len(items) < 2:
            continue
        canonical = min(
            items,
            key=lambda item: (
                -item.confidence,
                -item.importance,
                -item.version,
                item.id,
            ),
        )
        source_ids = tuple(sorted(item.id for item in items))
        operations.append(
            ConsolidationOperation.build(
                action=ConsolidationAction.replace,
                memory_key=group_key[0],
                kind=canonical.kind,
                scope=canonical.scope,
                content=canonical.content,
                structured_data=canonical.structured_data,
                confidence=max(item.confidence for item in items),
                importance=max(item.importance for item in items),
                source_memory_ids=source_ids,
                replace_memory_ids=source_ids,
            )
        )
    return ConsolidationProposal.build(
        producer="deterministic",
        operations=operations,
    )
