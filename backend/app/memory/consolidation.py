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

from app.memory.domain import MemoryKind, normalize_memory_kind

if TYPE_CHECKING:
    from app.agent_profile.profile import AgentProfile
    from app.db.models import MemoryRecord, MemorySourceRecord


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
        allowed = {
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
        _strict_fields(
            value,
            allowed=allowed,
            required=allowed,
            label="Frozen Memory input",
        )
        integer_fields = ("version", "state_version", "consolidation_generation")
        for field in integer_fields:
            if isinstance(value[field], bool) or not isinstance(value[field], int):
                raise ConsolidationValidationError(f"Frozen Memory {field} must be an integer")
        if not isinstance(value["sources"], list):
            raise ConsolidationValidationError("Frozen Memory sources must be a list")
        sources = tuple(
            sorted(
                (
                    FrozenSourceReference.from_dict(source)
                    for source in value["sources"]
                    if isinstance(source, Mapping)
                ),
                key=lambda source: (
                    source.source_kind,
                    source.source_ref,
                    source.source_hash,
                ),
            )
        )
        if len(sources) != len(value["sources"]):
            raise ConsolidationValidationError("Frozen Memory source entries must be objects")
        content = _bounded_string(
            value["content"],
            label="Frozen Memory content",
            maximum=50_000,
        )
        content_hash = _bounded_string(
            value["content_hash"],
            label="Frozen Memory content hash",
            maximum=128,
        )
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash:
            raise ConsolidationValidationError("Frozen Memory content hash mismatch")
        if not isinstance(value["structured_data"], Mapping):
            raise ConsolidationValidationError("Frozen Memory structured_data must be an object")
        if not isinstance(value["provenance"], Mapping):
            raise ConsolidationValidationError("Frozen Memory provenance must be an object")
        candidate = cls(
            id=_bounded_string(value["id"], label="Memory ID", maximum=120),
            memory_key=_bounded_string(value["memory_key"], label="Memory key", maximum=240),
            version=value["version"],
            state_version=value["state_version"],
            status=_bounded_string(value["status"], label="Memory status", maximum=40),
            namespace_type=_bounded_string(
                value["namespace_type"], label="Namespace type", maximum=40
            ),
            namespace_id=_bounded_string(value["namespace_id"], label="Namespace ID", maximum=120),
            scope=_bounded_string(value["scope"], label="Memory scope", maximum=40),
            kind=_bounded_string(value["kind"], label="Memory kind", maximum=80),
            content=content,
            structured_data_json=canonical_json(value["structured_data"]),
            provenance_json=canonical_json(value["provenance"]),
            confidence=_bounded_number(
                value["confidence"],
                label="Memory confidence",
                minimum=0.0,
                maximum=1.0,
            ),
            importance=_bounded_number(
                value["importance"],
                label="Memory importance",
                minimum=0.0,
                maximum=1.0,
            ),
            utility_score=_bounded_number(
                value["utility_score"],
                label="Memory utility",
                minimum=-1.0,
                maximum=1.0,
            ),
            run_id=_optional_string(value["run_id"], label="Run ID", maximum=120),
            created_by=_optional_string(value["created_by"], label="Creator ID", maximum=120),
            observed_at=_bounded_string(value["observed_at"], label="Observed time", maximum=80),
            valid_from=_bounded_string(value["valid_from"], label="Valid-from time", maximum=80),
            valid_to=_optional_string(value["valid_to"], label="Valid-to time", maximum=80),
            expires_at=_optional_string(value["expires_at"], label="Expiration time", maximum=80),
            consolidation_generation=value["consolidation_generation"],
            sources=sources,
            content_hash=content_hash,
            memory_hash=_bounded_string(
                value["memory_hash"], label="Frozen Memory hash", maximum=128
            ),
        )
        if canonical_digest(candidate._payload()) != candidate.memory_hash:
            raise ConsolidationValidationError("Frozen Memory hash mismatch")
        return candidate


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
        raw = {
            "schema_version": value["schema_version"],
            "operations": value["operations"],
        }
        proposal = normalize_model_output(raw, producer=str(value["producer"]))
        stored_hash = _bounded_string(value["proposal_hash"], label="Proposal hash", maximum=128)
        if proposal.proposal_hash != stored_hash:
            raise ConsolidationValidationError("Consolidation proposal hash mismatch")
        return proposal


def normalize_model_output(
    raw_output: str | Mapping[str, Any],
    *,
    producer: str = "model",
) -> ConsolidationProposal:
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
    _strict_fields(
        decoded,
        allowed={"schema_version", "operations"},
        required={"schema_version", "operations"},
        label="Model consolidation output",
    )
    if decoded["schema_version"] != CONSOLIDATION_PROPOSAL_SCHEMA_VERSION:
        raise ConsolidationValidationError("Unsupported consolidation proposal schema")
    raw_operations = decoded["operations"]
    if not isinstance(raw_operations, list):
        raise ConsolidationValidationError("Proposal operations must be a list")
    if len(raw_operations) > MAX_PROPOSAL_OPERATIONS:
        raise ConsolidationValidationError(
            f"Proposal exceeds the {MAX_PROPOSAL_OPERATIONS} operation limit"
        )
    operations: list[ConsolidationOperation] = []
    total_content_chars = 0
    replaced_ids: set[str] = set()
    for index, raw_operation in enumerate(raw_operations):
        if not isinstance(raw_operation, Mapping):
            raise ConsolidationValidationError(f"Proposal operation {index} must be an object")
        allowed = {
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
        }
        required = {
            "action",
            "memory_key",
            "kind",
            "scope",
            "content",
            "source_memory_ids",
        }
        _strict_fields(
            raw_operation,
            allowed=allowed,
            required=required,
            label=f"Proposal operation {index}",
        )
        try:
            action = ConsolidationAction(str(raw_operation["action"]))
        except ValueError as exc:
            raise ConsolidationValidationError(
                f"Proposal operation {index} has an unsupported action"
            ) from exc
        memory_key = _bounded_string(
            raw_operation["memory_key"],
            label=f"Proposal operation {index} Memory key",
            maximum=240,
        )
        raw_kind = _bounded_string(
            raw_operation["kind"],
            label=f"Proposal operation {index} kind",
            maximum=80,
        )
        normalized_kind = normalize_memory_kind(
            raw_kind,
        )
        if not isinstance(normalized_kind, MemoryKind):
            raise ConsolidationValidationError(
                f"Proposal operation {index} has an unsupported Memory kind"
            )
        scope = _bounded_string(
            raw_operation["scope"],
            label=f"Proposal operation {index} scope",
            maximum=40,
        )
        if scope not in {"run", "task", "session", "user"}:
            raise ConsolidationValidationError(
                f"Proposal operation {index} has an unsupported Memory scope"
            )
        content = _bounded_string(
            raw_operation["content"],
            label=f"Proposal operation {index} content",
            maximum=MAX_PROPOSAL_CONTENT_CHARS,
        )
        total_content_chars += len(content)
        if total_content_chars > MAX_TOTAL_PROPOSAL_CONTENT_CHARS:
            raise ConsolidationValidationError("Proposal exceeds the total content character limit")
        structured_data = raw_operation.get("structured_data", {})
        if not isinstance(structured_data, Mapping):
            raise ConsolidationValidationError(
                f"Proposal operation {index} structured_data must be an object"
            )
        confidence = _bounded_number(
            raw_operation.get("confidence", 0.8),
            label=f"Proposal operation {index} confidence",
            minimum=0.0,
            maximum=1.0,
        )
        importance = _bounded_number(
            raw_operation.get("importance", 0.5),
            label=f"Proposal operation {index} importance",
            minimum=0.0,
            maximum=1.0,
        )
        source_ids = _bounded_ids(
            raw_operation["source_memory_ids"],
            label=f"Proposal operation {index} source_memory_ids",
            required=True,
        )
        replacement_ids = _bounded_ids(
            raw_operation.get("replace_memory_ids", []),
            label=f"Proposal operation {index} replace_memory_ids",
            required=action is ConsolidationAction.replace,
        )
        if action is ConsolidationAction.add and replacement_ids:
            raise ConsolidationValidationError(
                f"Proposal operation {index} add action cannot replace Memory"
            )
        overlap = replaced_ids.intersection(replacement_ids)
        if overlap:
            raise ConsolidationValidationError(
                "Proposal cannot replace the same Memory in multiple operations"
            )
        replaced_ids.update(replacement_ids)
        operation = ConsolidationOperation.build(
            action=action,
            memory_key=memory_key,
            kind=normalized_kind.value,
            scope=scope,
            content=content,
            structured_data=_json_object(structured_data),
            confidence=confidence,
            importance=importance,
            source_memory_ids=source_ids,
            replace_memory_ids=replacement_ids,
        )
        supplied_id = raw_operation.get("operation_id")
        if supplied_id is not None and normalize_text(str(supplied_id)) != operation.operation_id:
            raise ConsolidationValidationError(
                f"Proposal operation {index} operation_id does not match its content"
            )
        operations.append(operation)
    output_keys = [operation.memory_key for operation in operations]
    if len(output_keys) != len(set(output_keys)):
        raise ConsolidationValidationError(
            "Proposal cannot create multiple outputs for one normalized Memory key"
        )
    return ConsolidationProposal.build(producer=producer, operations=operations)


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


@dataclass(frozen=True, slots=True)
class ConsolidationValidationIssue:
    code: str
    detail: str
    operation_id: str | None = None
    memory_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "operation_id": self.operation_id,
            "memory_id": self.memory_id,
        }


@dataclass(frozen=True, slots=True)
class ConsolidationValidationReport:
    input_hash: str
    proposal_hash: str
    issues: tuple[ConsolidationValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "input_hash": self.input_hash,
            "proposal_hash": self.proposal_hash,
            "issues": [issue.to_dict() for issue in self.issues],
        }


_INSTRUCTION_PATTERNS = (
    re.compile(
        r"\bignore\s+(?:all\s+)?(?:previous|prior|trusted|system)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:replace|override|rewrite)\b.{0,80}"
        r"\b(?:system\s+prompt|agent\s+profile|autodream|memory\s+governance)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"<\s*/?\s*astra_(?:runtime_context|skill)\b", re.IGNORECASE),
    re.compile(r"(?:忽略|覆盖|替换).{0,40}(?:系统|受信任|协议|配置|指令)"),
)
_AUTHORITY_PATTERNS = (
    re.compile(
        r"\b(?:enable|grant|expand|bypass|weaken|override|install|modify|change)\b"
        r".{0,80}\b(?:tool|permission|credential|approval|sandbox|security|policy|"
        r"profile|skill)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\b(?:tool|permission|credential|approval|sandbox|security|policy|profile|"
        r"skill)\b.{0,80}\b(?:enable|grant|expand|bypass|weaken|override|install|"
        r"modify|change)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:启用|授予|扩大|绕过|削弱|覆盖|安装|修改|变更).{0,40}"
        r"(?:工具|权限|凭据|审批|沙箱|安全|策略|Profile|Skill)",
        re.IGNORECASE,
    ),
)
_PROTECTED_KEY_PATTERN = re.compile(
    r"(?:enable|grant|expand|bypass|override|install|modify|change|disable|remove)"
    r".*(?:tool|permission|credential|approval|sandbox|security|policy|profile|skill)"
    r"|(?:credential|secret|api_key|approval_bypass|sandbox_exception|security_override)",
    re.IGNORECASE,
)


def _contains_instruction_override(value: str) -> bool:
    return any(pattern.search(value) for pattern in _INSTRUCTION_PATTERNS)


def _contains_authority_change(value: str) -> bool:
    return any(pattern.search(value) for pattern in _AUTHORITY_PATTERNS)


def _protected_structured_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = normalize_text(str(raw_key))
            if _PROTECTED_KEY_PATTERN.search(key):
                return key
            found = _protected_structured_key(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _protected_structured_key(nested)
            if found is not None:
                return found
    return None


def validate_proposal(
    manifest: ConsolidationInputManifest,
    proposal: ConsolidationProposal,
    *,
    extra_issues: Iterable[ConsolidationValidationIssue] = (),
) -> ConsolidationValidationReport:
    items = {item.id: item for item in manifest.items}
    issues = list(extra_issues)
    output_keys: set[str] = set()
    replaced_ids: set[str] = set()
    for operation in proposal.operations:
        if operation.memory_key in output_keys:
            issues.append(
                ConsolidationValidationIssue(
                    code="duplicate_output_key",
                    detail="Multiple operations produce the same normalized Memory key",
                    operation_id=operation.operation_id,
                )
            )
        output_keys.add(operation.memory_key)
        if not operation.source_memory_ids:
            issues.append(
                ConsolidationValidationIssue(
                    code="source_coverage",
                    detail="A consolidation output must cite at least one input Memory",
                    operation_id=operation.operation_id,
                )
            )
        for source_id in operation.source_memory_ids:
            source = items.get(source_id)
            if source is None:
                issues.append(
                    ConsolidationValidationIssue(
                        code="namespace_isolation",
                        detail=(
                            "A consolidation source is outside the frozen namespace working region"
                        ),
                        operation_id=operation.operation_id,
                        memory_id=source_id,
                    )
                )
                continue
            if not any(reference.accessible for reference in source.sources):
                issues.append(
                    ConsolidationValidationIssue(
                        code="source_coverage",
                        detail="An input Memory has no accessible frozen provenance",
                        operation_id=operation.operation_id,
                        memory_id=source_id,
                    )
                )
        for replacement_id in operation.replace_memory_ids:
            if replacement_id in replaced_ids:
                issues.append(
                    ConsolidationValidationIssue(
                        code="duplicate_replacement",
                        detail="An input Memory cannot be replaced more than once",
                        operation_id=operation.operation_id,
                        memory_id=replacement_id,
                    )
                )
            replaced_ids.add(replacement_id)
            replacement = items.get(replacement_id)
            if replacement is None:
                issues.append(
                    ConsolidationValidationIssue(
                        code="namespace_isolation",
                        detail="A replacement target is outside the frozen working region",
                        operation_id=operation.operation_id,
                        memory_id=replacement_id,
                    )
                )
                continue
            if replacement_id not in operation.source_memory_ids:
                issues.append(
                    ConsolidationValidationIssue(
                        code="source_coverage",
                        detail="Every replacement target must also be a cited source",
                        operation_id=operation.operation_id,
                        memory_id=replacement_id,
                    )
                )
            if replacement.kind != operation.kind or replacement.scope != operation.scope:
                issues.append(
                    ConsolidationValidationIssue(
                        code="type_isolation",
                        detail=(
                            "A replacement cannot change the Memory kind or scope of its input"
                        ),
                        operation_id=operation.operation_id,
                        memory_id=replacement_id,
                    )
                )
        if operation.action is ConsolidationAction.replace and not operation.replace_memory_ids:
            issues.append(
                ConsolidationValidationIssue(
                    code="replacement_required",
                    detail="A replace operation must identify replacement targets",
                    operation_id=operation.operation_id,
                )
            )
        if operation.action is ConsolidationAction.add and operation.replace_memory_ids:
            issues.append(
                ConsolidationValidationIssue(
                    code="unexpected_replacement",
                    detail="An add operation cannot identify replacement targets",
                    operation_id=operation.operation_id,
                )
            )
        if operation.scope != manifest.namespace_type:
            issues.append(
                ConsolidationValidationIssue(
                    code="namespace_isolation",
                    detail="Output scope does not match the frozen namespace type",
                    operation_id=operation.operation_id,
                )
            )
        inspection_text = operation.content + "\n" + operation.structured_data_json
        if _contains_instruction_override(inspection_text):
            issues.append(
                ConsolidationValidationIssue(
                    code="instruction_isolation",
                    detail="Output contains an attempt to replace trusted instructions",
                    operation_id=operation.operation_id,
                )
            )
        protected_key = _protected_structured_key(operation.structured_data)
        if _contains_authority_change(inspection_text) or protected_key is not None:
            detail = "Output attempts to change protected runtime authority"
            if protected_key is not None:
                detail += f" through structured field {protected_key}"
            issues.append(
                ConsolidationValidationIssue(
                    code="protected_authority",
                    detail=detail,
                    operation_id=operation.operation_id,
                )
            )
    normalized_issues = tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.code,
                issue.operation_id or "",
                issue.memory_id or "",
                issue.detail,
            ),
        )
    )
    return ConsolidationValidationReport(
        input_hash=manifest.input_hash,
        proposal_hash=proposal.proposal_hash,
        issues=normalized_issues,
    )


def autodream_profile_snapshot(profile: AgentProfile) -> dict[str, Any]:
    from app.agent_profile.profile import ModelOperation

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
    normalized = _json_object(payload)
    return {
        **normalized,
        "snapshot_hash": canonical_digest(normalized),
    }
