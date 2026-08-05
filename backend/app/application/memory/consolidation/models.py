from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.infrastructure.db.models.memory import MemoryRecord, MemorySourceRecord


INPUT_MANIFEST_SCHEMA_VERSION = 1
CONSOLIDATION_PROPOSAL_SCHEMA_VERSION = 1
MAX_CONSOLIDATION_INPUTS = 100
MAX_PROPOSAL_OPERATIONS = 32
MAX_OPERATION_SOURCES = 100
MAX_MODEL_OUTPUT_BYTES = 256 * 1024
MAX_PROPOSAL_CONTENT_CHARS = 20_000
MAX_TOTAL_PROPOSAL_CONTENT_CHARS = 100_000

class ConsolidationAction(str, Enum):
    add = "add"
    replace = "replace"


class ConsolidationValidationError(ValueError):
    pass


class ConsolidationConflictError(RuntimeError):
    pass


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def normalize_memory_key(value: str) -> str:
    normalized = normalize_text(value).casefold()
    normalized = re.sub(r"[\W_]+", ".", normalized, flags=re.UNICODE).strip(".")
    if not normalized:
        raise ConsolidationValidationError("Memory key must contain a stable identifier")
    if len(normalized) > 240:
        raise ConsolidationValidationError("Memory key exceeds the 240 character limit")
    return normalized


def equivalent_content_key(value: str) -> str:
    return " ".join(normalize_text(value).casefold().split())


def _normalize_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = normalize_text(str(raw_key))
            if not key:
                raise ConsolidationValidationError("JSON object keys must be non-empty")
            if key in normalized:
                raise ConsolidationValidationError(
                    f"JSON object has duplicate normalized key: {key}"
                )
            normalized[key] = _normalize_json(raw_value)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item) for item in value]
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if isinstance(value, str):
        return normalize_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConsolidationValidationError("JSON numbers must be finite")
        return value
    raise ConsolidationValidationError(f"Value is not JSON serializable: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _normalize_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = _normalize_json(value or {})
    if not isinstance(normalized, dict):
        raise ConsolidationValidationError("Expected a JSON object")
    return normalized


def _strict_fields(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> None:
    keys = {str(key) for key in value}
    missing = sorted(required - keys)
    unexpected = sorted(keys - allowed)
    if missing:
        raise ConsolidationValidationError(
            f"{label} is missing required fields: {', '.join(missing)}"
        )
    if unexpected:
        raise ConsolidationValidationError(
            f"{label} has unexpected fields: {', '.join(unexpected)}"
        )


def _bounded_string(value: Any, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ConsolidationValidationError(f"{label} must be a string")
    normalized = normalize_text(value)
    if not normalized:
        raise ConsolidationValidationError(f"{label} must be non-empty")
    if len(normalized) > maximum:
        raise ConsolidationValidationError(f"{label} exceeds the {maximum} character limit")
    return normalized


def _optional_string(value: Any, *, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, label=label, maximum=maximum)


def _bounded_number(
    value: Any,
    *,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsolidationValidationError(f"{label} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        raise ConsolidationValidationError(f"{label} must be between {minimum} and {maximum}")
    return normalized


def _bounded_ids(
    value: Any,
    *,
    label: str,
    required: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConsolidationValidationError(f"{label} must be a list")
    if required and not value:
        raise ConsolidationValidationError(f"{label} must be non-empty")
    if len(value) > MAX_OPERATION_SOURCES:
        raise ConsolidationValidationError(
            f"{label} exceeds the {MAX_OPERATION_SOURCES} item limit"
        )
    normalized = tuple(_bounded_string(item, label=f"{label} item", maximum=120) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ConsolidationValidationError(f"{label} contains duplicate IDs")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class FrozenSourceReference:
    source_kind: str
    source_ref: str
    source_hash: str
    accessible: bool
    run_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    artifact_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "accessible": self.accessible,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "artifact_id": self.artifact_id,
        }

    @classmethod
    def from_record(cls, source: MemorySourceRecord) -> FrozenSourceReference:
        return cls(
            source_kind=normalize_text(source.source_kind),
            source_ref=normalize_text(source.source_ref),
            source_hash=normalize_text(source.source_hash),
            accessible=bool(source.accessible and source.revoked_at is None),
            run_id=source.run_id,
            turn_id=source.turn_id,
            tool_call_id=source.tool_call_id,
            artifact_id=source.artifact_id,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FrozenSourceReference:
        allowed = {
            "source_kind",
            "source_ref",
            "source_hash",
            "accessible",
            "run_id",
            "turn_id",
            "tool_call_id",
            "artifact_id",
        }
        _strict_fields(
            value,
            allowed=allowed,
            required={"source_kind", "source_ref", "source_hash", "accessible"},
            label="Frozen source reference",
        )
        if not isinstance(value["accessible"], bool):
            raise ConsolidationValidationError("Frozen source accessibility must be a boolean")
        return cls(
            source_kind=_bounded_string(value["source_kind"], label="Source kind", maximum=40),
            source_ref=_bounded_string(value["source_ref"], label="Source reference", maximum=320),
            source_hash=_bounded_string(value["source_hash"], label="Source hash", maximum=128),
            accessible=value["accessible"],
            run_id=_optional_string(value.get("run_id"), label="Run ID", maximum=120),
            turn_id=_optional_string(value.get("turn_id"), label="Turn ID", maximum=120),
            tool_call_id=_optional_string(
                value.get("tool_call_id"), label="Tool call ID", maximum=120
            ),
            artifact_id=_optional_string(
                value.get("artifact_id"), label="Artifact ID", maximum=120
            ),
        )


@dataclass(frozen=True, slots=True)
class FrozenMemoryInput:
    id: str
    memory_key: str
    version: int
    state_version: int
    status: str
    namespace_type: str
    namespace_id: str
    scope: str
    kind: str
    content: str
    structured_data_json: str
    provenance_json: str
    confidence: float
    importance: float
    utility_score: float
    run_id: str | None
    created_by: str | None
    observed_at: str
    valid_from: str
    valid_to: str | None
    expires_at: str | None
    consolidation_generation: int
    sources: tuple[FrozenSourceReference, ...]
    content_hash: str
    memory_hash: str

    @property
    def structured_data(self) -> dict[str, Any]:
        return json.loads(self.structured_data_json)

    @property
    def provenance(self) -> dict[str, Any]:
        return json.loads(self.provenance_json)

    def _payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "memory_key": self.memory_key,
            "version": self.version,
            "state_version": self.state_version,
            "status": self.status,
            "namespace_type": self.namespace_type,
            "namespace_id": self.namespace_id,
            "scope": self.scope,
            "kind": self.kind,
            "content": self.content,
            "structured_data": self.structured_data,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "importance": self.importance,
            "utility_score": self.utility_score,
            "run_id": self.run_id,
            "created_by": self.created_by,
            "observed_at": self.observed_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "expires_at": self.expires_at,
            "consolidation_generation": self.consolidation_generation,
            "sources": [source.to_dict() for source in self.sources],
            "content_hash": self.content_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "memory_hash": self.memory_hash}

    @classmethod
    def from_record(cls, memory: MemoryRecord) -> FrozenMemoryInput:
        content = normalize_text(memory.content)
        structured_data = _json_object(memory.structured_data)
        provenance = _json_object(memory.provenance)
        sources = tuple(
            sorted(
                (FrozenSourceReference.from_record(source) for source in memory.sources),
                key=lambda source: (
                    source.source_kind,
                    source.source_ref,
                    source.source_hash,
                ),
            )
        )
        base = cls(
            id=normalize_text(memory.id),
            memory_key=normalize_text(memory.memory_key),
            version=int(memory.version),
            state_version=int(memory.state_version),
            status=normalize_text(memory.status),
            namespace_type=normalize_text(memory.namespace_type),
            namespace_id=normalize_text(memory.namespace_id),
            scope=normalize_text(memory.scope),
            kind=normalize_text(memory.kind),
            content=content,
            structured_data_json=canonical_json(structured_data),
            provenance_json=canonical_json(provenance),
            confidence=float(memory.confidence),
            importance=float(memory.importance),
            utility_score=float(memory.utility_score),
            run_id=memory.run_id,
            created_by=memory.created_by,
            observed_at=_normalize_datetime(memory.observed_at),
            valid_from=_normalize_datetime(memory.valid_from),
            valid_to=(
                _normalize_datetime(memory.valid_to) if memory.valid_to is not None else None
            ),
            expires_at=(
                _normalize_datetime(memory.expires_at) if memory.expires_at is not None else None
            ),
            consolidation_generation=int(memory.consolidation_generation),
            sources=sources,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            memory_hash="",
        )
        return replace(base, memory_hash=canonical_digest(base._payload()))

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FrozenMemoryInput:
        allowed = _FROZEN_MEMORY_FIELDS
        _strict_fields(
            value,
            allowed=allowed,
            required=allowed,
            label="Frozen Memory input",
        )
        _validate_frozen_memory_types(value)
        content, content_hash = _validated_frozen_content(value)
        candidate = cls(
            **{
                **_frozen_memory_identity(value, content),
                **_frozen_memory_scores(value),
                **_frozen_memory_lifecycle(value),
                "sources": _frozen_sources(value["sources"]),
                "content_hash": content_hash,
                "memory_hash": _bounded_string(
                    value["memory_hash"], label="Frozen Memory hash", maximum=128
                ),
            }
        )
        if canonical_digest(candidate._payload()) != candidate.memory_hash:
            raise ConsolidationValidationError("Frozen Memory hash mismatch")
        return candidate


_FROZEN_MEMORY_FIELDS = {
    "id",
    "memory_key",
    "version",
    "state_version",
    "status",
    "namespace_type",
    "namespace_id",
    "scope",
    "kind",
    "content",
    "structured_data",
    "provenance",
    "confidence",
    "importance",
    "utility_score",
    "run_id",
    "created_by",
    "observed_at",
    "valid_from",
    "valid_to",
    "expires_at",
    "consolidation_generation",
    "sources",
    "content_hash",
    "memory_hash",
}


def _validate_frozen_memory_types(value: Mapping[str, Any]) -> None:
    for field in ("version", "state_version", "consolidation_generation"):
        if isinstance(value[field], bool) or not isinstance(value[field], int):
            raise ConsolidationValidationError(f"Frozen Memory {field} must be an integer")
    if not isinstance(value["sources"], list):
        raise ConsolidationValidationError("Frozen Memory sources must be a list")
    for field in ("structured_data", "provenance"):
        if not isinstance(value[field], Mapping):
            raise ConsolidationValidationError(f"Frozen Memory {field} must be an object")


def _frozen_sources(value: Any) -> tuple[FrozenSourceReference, ...]:
    sources = tuple(
        sorted(
            (FrozenSourceReference.from_dict(item) for item in value if isinstance(item, Mapping)),
            key=lambda item: (item.source_kind, item.source_ref, item.source_hash),
        )
    )
    if len(sources) != len(value):
        raise ConsolidationValidationError("Frozen Memory source entries must be objects")
    return sources


def _validated_frozen_content(value: Mapping[str, Any]) -> tuple[str, str]:
    content = _bounded_string(value["content"], label="Frozen Memory content", maximum=50_000)
    content_hash = _bounded_string(
        value["content_hash"], label="Frozen Memory content hash", maximum=128
    )
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash:
        raise ConsolidationValidationError("Frozen Memory content hash mismatch")
    return content, content_hash


def _frozen_memory_identity(value: Mapping[str, Any], content: str) -> dict[str, Any]:
    return {
        "id": _bounded_string(value["id"], label="Memory ID", maximum=120),
        "memory_key": _bounded_string(value["memory_key"], label="Memory key", maximum=240),
        "version": value["version"],
        "state_version": value["state_version"],
        "status": _bounded_string(value["status"], label="Memory status", maximum=40),
        "namespace_type": _bounded_string(
            value["namespace_type"], label="Namespace type", maximum=40
        ),
        "namespace_id": _bounded_string(value["namespace_id"], label="Namespace ID", maximum=120),
        "scope": _bounded_string(value["scope"], label="Memory scope", maximum=40),
        "kind": _bounded_string(value["kind"], label="Memory kind", maximum=80),
        "content": content,
        "structured_data_json": canonical_json(value["structured_data"]),
        "provenance_json": canonical_json(value["provenance"]),
    }


def _frozen_memory_scores(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "confidence": _bounded_number(
            value["confidence"],
            label="Memory confidence",
            minimum=0.0,
            maximum=1.0,
        ),
        "importance": _bounded_number(
            value["importance"],
            label="Memory importance",
            minimum=0.0,
            maximum=1.0,
        ),
        "utility_score": _bounded_number(
            value["utility_score"],
            label="Memory utility",
            minimum=-1.0,
            maximum=1.0,
        ),
    }


def _frozen_memory_lifecycle(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": _optional_string(value["run_id"], label="Run ID", maximum=120),
        "created_by": _optional_string(value["created_by"], label="Creator ID", maximum=120),
        "observed_at": _bounded_string(value["observed_at"], label="Observed time", maximum=80),
        "valid_from": _bounded_string(value["valid_from"], label="Valid-from time", maximum=80),
        "valid_to": _optional_string(value["valid_to"], label="Valid-to time", maximum=80),
        "expires_at": _optional_string(value["expires_at"], label="Expiration time", maximum=80),
        "consolidation_generation": value["consolidation_generation"],
    }


@dataclass(frozen=True, slots=True)
class ConsolidationInputManifest:
    namespace_type: str
    namespace_id: str
    items: tuple[FrozenMemoryInput, ...]
    input_hash: str
    schema_version: int = INPUT_MANIFEST_SCHEMA_VERSION

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "namespace": {
                "type": self.namespace_type,
                "id": self.namespace_id,
            },
            "items": [item.to_dict() for item in self.items],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "input_hash": self.input_hash}

    @classmethod
    def build(
        cls,
        *,
        namespace_type: str,
        namespace_id: str,
        items: Iterable[FrozenMemoryInput],
    ) -> ConsolidationInputManifest:
        normalized_items = tuple(
            sorted(
                items,
                key=lambda item: (
                    normalize_memory_key(item.memory_key),
                    item.version,
                    item.id,
                ),
            )
        )
        if not normalized_items:
            raise ConsolidationValidationError("Consolidation input manifest must be non-empty")
        if len(normalized_items) > MAX_CONSOLIDATION_INPUTS:
            raise ConsolidationValidationError(
                f"Consolidation input exceeds the {MAX_CONSOLIDATION_INPUTS} item limit"
            )
        if len({item.id for item in normalized_items}) != len(normalized_items):
            raise ConsolidationValidationError("Consolidation input contains duplicate Memory IDs")
        normalized_type = _bounded_string(namespace_type, label="Namespace type", maximum=40)
        normalized_id = _bounded_string(namespace_id, label="Namespace ID", maximum=120)
        for item in normalized_items:
            if item.namespace_type != normalized_type or item.namespace_id != normalized_id:
                raise ConsolidationValidationError("Consolidation input cannot cross namespaces")
        base = cls(
            namespace_type=normalized_type,
            namespace_id=normalized_id,
            items=normalized_items,
            input_hash="",
        )
        return cls(
            namespace_type=base.namespace_type,
            namespace_id=base.namespace_id,
            items=base.items,
            input_hash=canonical_digest(base._payload()),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConsolidationInputManifest:
        _strict_fields(
            value,
            allowed={"schema_version", "namespace", "items", "input_hash"},
            required={"schema_version", "namespace", "items", "input_hash"},
            label="Consolidation input manifest",
        )
        if value["schema_version"] != INPUT_MANIFEST_SCHEMA_VERSION:
            raise ConsolidationValidationError("Unsupported consolidation input manifest schema")
        namespace = value["namespace"]
        if not isinstance(namespace, Mapping):
            raise ConsolidationValidationError("Manifest namespace must be an object")
        _strict_fields(
            namespace,
            allowed={"type", "id"},
            required={"type", "id"},
            label="Manifest namespace",
        )
        raw_items = value["items"]
        if not isinstance(raw_items, list):
            raise ConsolidationValidationError("Manifest items must be a list")
        if not all(isinstance(item, Mapping) for item in raw_items):
            raise ConsolidationValidationError("Manifest items must be objects")
        candidate = cls.build(
            namespace_type=str(namespace["type"]),
            namespace_id=str(namespace["id"]),
            items=(FrozenMemoryInput.from_dict(item) for item in raw_items),
        )
        stored_hash = _bounded_string(value["input_hash"], label="Input manifest hash", maximum=128)
        if candidate.input_hash != stored_hash:
            raise ConsolidationValidationError("Input manifest hash mismatch")
        return candidate


@dataclass(frozen=True, slots=True)
class ConsolidationOperation:
    action: ConsolidationAction
    memory_key: str
    kind: str
    scope: str
    content: str
    structured_data_json: str
    confidence: float
    importance: float
    source_memory_ids: tuple[str, ...]
    replace_memory_ids: tuple[str, ...]
    operation_id: str

    @property
    def structured_data(self) -> dict[str, Any]:
        return json.loads(self.structured_data_json)

    def _payload(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "memory_key": self.memory_key,
            "kind": self.kind,
            "scope": self.scope,
            "content": self.content,
            "structured_data": self.structured_data,
            "confidence": self.confidence,
            "importance": self.importance,
            "source_memory_ids": list(self.source_memory_ids),
            "replace_memory_ids": list(self.replace_memory_ids),
        }

    def to_dict(self) -> dict[str, Any]:
        return {"operation_id": self.operation_id, **self._payload()}

    @classmethod
    def build(
        cls,
        *,
        action: ConsolidationAction,
        memory_key: str,
        kind: str,
        scope: str,
        content: str,
        structured_data: Mapping[str, Any],
        confidence: float,
        importance: float,
        source_memory_ids: Sequence[str],
        replace_memory_ids: Sequence[str],
    ) -> ConsolidationOperation:
        base = cls(
            action=action,
            memory_key=normalize_memory_key(memory_key),
            kind=kind,
            scope=scope,
            content=normalize_text(content),
            structured_data_json=canonical_json(structured_data),
            confidence=float(confidence),
            importance=float(importance),
            source_memory_ids=tuple(sorted(source_memory_ids)),
            replace_memory_ids=tuple(sorted(replace_memory_ids)),
            operation_id="",
        )
        return cls(
            action=base.action,
            memory_key=base.memory_key,
            kind=base.kind,
            scope=base.scope,
            content=base.content,
            structured_data_json=base.structured_data_json,
            confidence=base.confidence,
            importance=base.importance,
            source_memory_ids=base.source_memory_ids,
            replace_memory_ids=base.replace_memory_ids,
            operation_id=f"operation-{canonical_digest(base._payload())[:24]}",
        )


@dataclass(frozen=True, slots=True)
class ConsolidationProposal:
    producer: str
    operations: tuple[ConsolidationOperation, ...]
    proposal_hash: str
    schema_version: int = CONSOLIDATION_PROPOSAL_SCHEMA_VERSION

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "producer": self.producer,
            "operations": [operation.to_dict() for operation in self.operations],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "proposal_hash": self.proposal_hash}

    @classmethod
    def build(
        cls,
        *,
        producer: str,
        operations: Iterable[ConsolidationOperation],
    ) -> ConsolidationProposal:
        normalized_producer = _bounded_string(producer, label="Proposal producer", maximum=40)
        normalized_operations = tuple(
            sorted(operations, key=lambda operation: operation.operation_id)
        )
        if len(normalized_operations) > MAX_PROPOSAL_OPERATIONS:
            raise ConsolidationValidationError(
                f"Proposal exceeds the {MAX_PROPOSAL_OPERATIONS} operation limit"
            )
        operation_ids = [operation.operation_id for operation in normalized_operations]
        if len(operation_ids) != len(set(operation_ids)):
            raise ConsolidationValidationError("Proposal contains duplicate normalized operations")
        base = cls(
            producer=normalized_producer,
            operations=normalized_operations,
            proposal_hash="",
        )
        return cls(
            producer=base.producer,
            operations=base.operations,
            proposal_hash=canonical_digest(base._payload()),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ConsolidationProposal:
        _strict_fields(
            value,
            allowed={
                "schema_version",
                "producer",
                "operations",
                "proposal_hash",
            },
            required={
                "schema_version",
                "producer",
                "operations",
                "proposal_hash",
            },
            label="Consolidation proposal",
        )
        if value["schema_version"] != CONSOLIDATION_PROPOSAL_SCHEMA_VERSION:
            raise ConsolidationValidationError("Unsupported consolidation proposal schema")
        raw_operations = value["operations"]
        if not isinstance(raw_operations, list):
            raise ConsolidationValidationError("Proposal operations must be a list")
        proposal = cls.build(
            producer=str(value["producer"]),
            operations=(_stored_operation(item) for item in raw_operations),
        )
        stored_hash = _bounded_string(value["proposal_hash"], label="Proposal hash", maximum=128)
        if proposal.proposal_hash != stored_hash:
            raise ConsolidationValidationError("Consolidation proposal hash mismatch")
        return proposal


def _stored_operation(value: Any) -> ConsolidationOperation:
    if not isinstance(value, Mapping):
        raise ConsolidationValidationError("Stored consolidation operation must be an object")
    try:
        operation = ConsolidationOperation.build(
            action=ConsolidationAction(str(value["action"])),
            memory_key=str(value["memory_key"]),
            kind=str(value["kind"]),
            scope=str(value["scope"]),
            content=str(value["content"]),
            structured_data=_json_object(value.get("structured_data")),
            confidence=float(value["confidence"]),
            importance=float(value["importance"]),
            source_memory_ids=_bounded_ids(
                value["source_memory_ids"], label="source ids", required=True
            ),
            replace_memory_ids=_bounded_ids(
                value.get("replace_memory_ids", []),
                label="replacement ids",
                required=False,
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConsolidationValidationError("Stored consolidation operation is invalid") from exc
    if value.get("operation_id") != operation.operation_id:
        raise ConsolidationValidationError("Stored consolidation operation hash mismatch")
    return operation
