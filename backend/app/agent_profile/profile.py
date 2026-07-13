from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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


COMPOSITION_SCHEMA_VERSION = 1
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
    }
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

    def safe_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "composition_schema_version": self.composition_schema_version,
            "documents": {item.name: item.safe_metadata() for item in self.documents},
            "role_documents": {
                operation.value: list(names) for operation, names in ROLE_DOCUMENTS.items()
            },
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
        try:
            names = ROLE_DOCUMENTS[operation]
        except KeyError as exc:
            raise AgentProfileConfigurationError(
                f"Unsupported Agent Profile operation: {operation}"
            ) from exc
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
        profile = AgentProfileLoader().load(contents)
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

    def load(self, contents: Mapping[str, str] | None = None) -> AgentProfile:
        documents = []
        for name, filename in DOCUMENT_FILES.items():
            content = contents.get(name) if contents is not None else self._read(filename)
            if not isinstance(content, str):
                raise AgentProfileConfigurationError(
                    f"Agent Profile document is missing: {filename}"
                )
            documents.append(self._document(name, filename, content))
        version = _profile_version(tuple(documents))
        return AgentProfile(
            AgentProfileManifest(
                version=version,
                composition_schema_version=COMPOSITION_SCHEMA_VERSION,
                documents=tuple(documents),
            )
        )

    def _read(self, filename: str) -> str:
        try:
            return resources.files(self.package).joinpath(filename).read_text(encoding="utf-8")
        except (FileNotFoundError, ModuleNotFoundError, UnicodeDecodeError) as exc:
            raise AgentProfileConfigurationError(
                f"Unable to load Agent Profile document: {filename}"
            ) from exc

    def _document(self, name: str, filename: str, content: str) -> AgentProfileDocument:
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
        expected_status = "disabled" if name == "autodream" else "active"
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


def _profile_version(documents: tuple[AgentProfileDocument, ...]) -> str:
    payload = json.dumps(
        {
            "composition_schema_version": COMPOSITION_SCHEMA_VERSION,
            "documents": {item.name: item.sha256 for item in documents},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"profile-{hashlib.sha256(payload).hexdigest()}"


@lru_cache(maxsize=1)
def load_agent_profile() -> AgentProfile:
    return AgentProfileLoader().load()
