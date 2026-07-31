from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from types import MappingProxyType


class AgentProfileConfigurationError(RuntimeError):
    """The trusted Agent Profile is missing, malformed, or internally inconsistent."""


class ModelOperation(StrEnum):
    CONTRACT = "contract"
    PLAN = "plan"
    SYNTHESIS = "synthesis"
    DECISION = "decision"
    DECISION_WITH_ANSWER = "decision_with_answer"
    REFLECTION = "reflection"
    MEMORY = "memory"
    AUTODREAM = "autodream"


LEGACY_COMPOSITION_SCHEMA_VERSION = 1
COMPOSITION_SCHEMA_VERSION = 2
SUPPORTED_COMPOSITION_SCHEMA_VERSIONS = frozenset(
    {LEGACY_COMPOSITION_SCHEMA_VERSION, COMPOSITION_SCHEMA_VERSION}
)
MAX_DOCUMENT_BYTES = 16 * 1024
DOCUMENT_FILES = MappingProxyType(
    {
        "identity": "IDENTITY.md",
        "soul": "SOUL.md",
        "memory": "MEMORY.md",
        "autodream": "AUTODREAM.md",
    }
)
REQUIRED_HEADINGS = MappingProxyType(
    {
        "identity": ("# Astra Identity", "## Identity", "## Mission", "## Goals", "## Boundaries"),
        "soul": ("# Astra Soul", "## Character", "## Communication", "## Epistemics", "## Collaboration"),
        "memory": ("# Astra Memory Protocol", "## Purpose", "## Scope", "## Write Policy", "## Recall Policy", "## Conflict and Forgetting"),
        "autodream": ("# Astra AutoDream Protocol", "## Status", "## Purpose", "## Allowed Work", "## Prohibited Work", "## Future Execution Contract"),
    }
)
ROLE_DOCUMENTS = MappingProxyType(
    {
        ModelOperation.CONTRACT: ("identity",),
        ModelOperation.PLAN: ("identity",),
        ModelOperation.SYNTHESIS: ("identity", "soul"),
        ModelOperation.DECISION: ("identity", "soul"),
        ModelOperation.DECISION_WITH_ANSWER: ("identity", "soul"),
        ModelOperation.REFLECTION: ("identity", "memory"),
        ModelOperation.MEMORY: ("memory",),
        ModelOperation.AUTODREAM: ("identity", "memory", "autodream"),
    }
)
LEGACY_ROLE_DOCUMENTS = MappingProxyType(
    {
        operation: names
        for operation, names in ROLE_DOCUMENTS.items()
        if operation != ModelOperation.AUTODREAM
    }
)
SYNCHRONOUS_MODEL_OPERATIONS = frozenset(
    operation for operation in ModelOperation if operation != ModelOperation.AUTODREAM
)


@dataclass(frozen=True, slots=True)
class AgentProfileDocument:
    name: str
    filename: str
    content: str
    sha256: str
    size_bytes: int
    status: str

    def snapshot(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "content": self.content,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "status": self.status,
        }

    def safe_metadata(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class AgentProfileManifest:
    version: str
    composition_schema_version: int
    documents: tuple[AgentProfileDocument, ...]
    role_documents: tuple[tuple[str, tuple[str, ...]], ...]

    def safe_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "composition_schema_version": self.composition_schema_version,
            "documents": {item.name: item.safe_metadata() for item in self.documents},
            "role_documents": {operation: list(names) for operation, names in self.role_documents},
        }


@dataclass(frozen=True, slots=True)
class AgentProfile:
    manifest: AgentProfileManifest

    def document(self, name: str) -> AgentProfileDocument:
        for document in self.manifest.documents:
            if document.name == name:
                return document
        raise AgentProfileConfigurationError(f"Agent Profile document is unavailable: {name}")

    def documents_for(self, operation: ModelOperation) -> tuple[AgentProfileDocument, ...]:
        names = next(
            (names for role, names in self.manifest.role_documents if role == operation.value),
            None,
        )
        if names is None:
            raise AgentProfileConfigurationError(
                f"Unsupported Agent Profile operation: {operation}"
            )
        return tuple(self.document(name) for name in names)

    def snapshot(self) -> dict[str, object]:
        return {
            **self.manifest.safe_dict(),
            "source": "packaged",
            "documents": {item.name: item.snapshot() for item in self.manifest.documents},
        }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object]) -> AgentProfile:
        if snapshot.get("version") == "legacy-unversioned":
            raise AgentProfileConfigurationError(
                "Legacy Run does not contain a reconstructable Agent Profile"
            )
        raw_documents = snapshot.get("documents")
        if not isinstance(raw_documents, Mapping):
            raise AgentProfileConfigurationError("Agent Profile snapshot has no documents")
        contents: dict[str, str] = {}
        for name in DOCUMENT_FILES:
            value = raw_documents.get(name)
            if not isinstance(value, Mapping) or not isinstance(value.get("content"), str):
                raise AgentProfileConfigurationError(
                    f"Agent Profile snapshot document is invalid: {name}"
                )
            contents[name] = value["content"]
        composition_version = snapshot.get("composition_schema_version")
        role_documents = snapshot.get("role_documents")
        if not isinstance(composition_version, int) or not isinstance(role_documents, Mapping):
            raise AgentProfileConfigurationError("Agent Profile snapshot composition is invalid")
        profile = AgentProfileLoader().load(
            contents,
            composition_schema_version=composition_version,
            role_documents=role_documents,
        )
        if profile.manifest.version != snapshot.get("version"):
            raise AgentProfileConfigurationError("Agent Profile snapshot checksum mismatch")
        return profile


class AgentProfileLoader:
    def __init__(
        self,
        *,
        package: str = "app.agent_profile",
        max_document_bytes: int = MAX_DOCUMENT_BYTES,
    ):
        self.package = package
        self.max_document_bytes = max_document_bytes

    def load(
        self,
        contents: Mapping[str, str] | None = None,
        *,
        composition_schema_version: int = COMPOSITION_SCHEMA_VERSION,
        role_documents: Mapping[object, object] | None = None,
    ) -> AgentProfile:
        if composition_schema_version not in SUPPORTED_COMPOSITION_SCHEMA_VERSIONS:
            raise AgentProfileConfigurationError(
                "Agent Profile composition schema version is unsupported: "
                f"{composition_schema_version}"
            )
        documents = []
        for name, filename in DOCUMENT_FILES.items():
            content = contents.get(name) if contents is not None else self._read(filename)
            if not isinstance(content, str):
                raise AgentProfileConfigurationError(
                    f"Agent Profile document is missing: {filename}"
                )
            documents.append(
                self._document(
                    name,
                    filename,
                    content,
                    composition_schema_version=composition_schema_version,
                )
            )
        selected_roles = (
            role_documents
            if role_documents is not None
            else _default_role_documents(composition_schema_version)
        )
        normalized_roles = _normalize_role_documents(
            selected_roles,
            composition_schema_version=composition_schema_version,
        )
        version = _profile_version(
            tuple(documents), composition_schema_version, normalized_roles
        )
        return AgentProfile(
            AgentProfileManifest(
                version=version,
                composition_schema_version=composition_schema_version,
                documents=tuple(documents),
                role_documents=normalized_roles,
            )
        )

    def _read(self, filename: str) -> str:
        try:
            return resources.files(self.package).joinpath(filename).read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, UnicodeDecodeError) as exc:
            raise AgentProfileConfigurationError(
                f"Unable to load Agent Profile document: {filename}"
            ) from exc

    def _document(
        self,
        name: str,
        filename: str,
        content: str,
        *,
        composition_schema_version: int,
    ) -> AgentProfileDocument:
        normalized = normalize_document(content)
        encoded = normalized.encode("utf-8")
        if len(encoded) > self.max_document_bytes:
            raise AgentProfileConfigurationError(
                f"Agent Profile document exceeds {self.max_document_bytes} bytes: {filename}"
            )
        metadata = parse_frontmatter(normalized, filename)
        if metadata.get("schema_version") != "1" or metadata.get("document") != name:
            raise AgentProfileConfigurationError(
                f"Agent Profile metadata is invalid: {filename}"
            )
        expected_status = (
            "disabled"
            if (
                composition_schema_version == LEGACY_COMPOSITION_SCHEMA_VERSION
                and name == "autodream"
            )
            else "active"
        )
        if metadata.get("status") != expected_status:
            raise AgentProfileConfigurationError(
                f"Agent Profile status must be {expected_status}: {filename}"
            )
        for heading in REQUIRED_HEADINGS[name]:
            if heading not in normalized.splitlines():
                raise AgentProfileConfigurationError(
                    f"Agent Profile required section is missing in {filename}: {heading}"
                )
        return AgentProfileDocument(
            name=name,
            filename=filename,
            content=normalized,
            sha256=hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
            status=expected_status,
        )


def normalize_document(content: str) -> str:
    if not content or not content.strip():
        raise AgentProfileConfigurationError("Agent Profile document is empty")
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip() + "\n"


def parse_frontmatter(content: str, filename: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise AgentProfileConfigurationError(f"Agent Profile metadata is missing: {filename}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise AgentProfileConfigurationError(
            f"Agent Profile metadata is unterminated: {filename}"
        ) from exc
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise AgentProfileConfigurationError(
                f"Agent Profile metadata entry is invalid: {filename}"
            )
        metadata[key.strip()] = value.strip()
    return metadata


def _normalize_role_documents(
    role_documents: Mapping[object, object],
    *,
    composition_schema_version: int,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    expected_operations = {
        operation.value
        for operation in _default_role_documents(composition_schema_version)
    }
    normalized = []
    for raw_operation, raw_names in role_documents.items():
        operation = (
            raw_operation.value if isinstance(raw_operation, ModelOperation) else str(raw_operation)
        )
        if operation not in expected_operations:
            raise AgentProfileConfigurationError(
                f"Agent Profile role operation is invalid: {operation}"
            )
        if not isinstance(raw_names, (list, tuple)):
            raise AgentProfileConfigurationError(
                f"Agent Profile role document selection is invalid: {operation}"
            )
        names = tuple(str(name) for name in raw_names)
        if not names or len(names) != len(set(names)) or any(
            name not in DOCUMENT_FILES for name in names
        ):
            raise AgentProfileConfigurationError(
                f"Agent Profile role document selection is unsafe: {operation}"
            )
        if operation == ModelOperation.AUTODREAM.value:
            if names != ROLE_DOCUMENTS[ModelOperation.AUTODREAM]:
                raise AgentProfileConfigurationError(
                    "Agent Profile AutoDream document selection is unsafe"
                )
        elif "autodream" in names:
            raise AgentProfileConfigurationError(
                f"Agent Profile role document selection is unsafe: {operation}"
            )
        normalized.append((operation, names))
    if {operation for operation, _ in normalized} != expected_operations:
        raise AgentProfileConfigurationError("Agent Profile role matrix is incomplete")
    return tuple(sorted(normalized))


def _default_role_documents(
    composition_schema_version: int,
) -> Mapping[ModelOperation, tuple[str, ...]]:
    if composition_schema_version == LEGACY_COMPOSITION_SCHEMA_VERSION:
        return LEGACY_ROLE_DOCUMENTS
    if composition_schema_version == COMPOSITION_SCHEMA_VERSION:
        return ROLE_DOCUMENTS
    raise AgentProfileConfigurationError(
        "Agent Profile composition schema version is unsupported: "
        f"{composition_schema_version}"
    )


def _profile_version(
    documents: tuple[AgentProfileDocument, ...],
    composition_schema_version: int,
    role_documents: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    payload = json.dumps(
        {
            "composition_schema_version": composition_schema_version,
            "documents": {item.name: item.sha256 for item in documents},
            "role_documents": role_documents,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"profile-{hashlib.sha256(payload).hexdigest()}"


@lru_cache(maxsize=1)
def _load_packaged_agent_profile() -> AgentProfile:
    return AgentProfileLoader().load()


_runtime_profile_resolver: Callable[[], AgentProfile] | None = None


def configure_agent_profile_resolver(
    resolver: Callable[[], AgentProfile] | None,
) -> None:
    """Bind the process-wide active Profile source used by new runtime work."""

    global _runtime_profile_resolver
    _runtime_profile_resolver = resolver


def load_agent_profile() -> AgentProfile:
    resolver = _runtime_profile_resolver
    return resolver() if resolver is not None else _load_packaged_agent_profile()
