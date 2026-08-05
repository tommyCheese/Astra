from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MemoryKind(str, Enum):
    semantic_fact = "semantic_fact"
    user_preference = "user_preference"
    episodic_experience = "episodic_experience"
    procedure = "procedure"
    failure_pattern = "failure_pattern"
    evaluation_feedback = "evaluation_feedback"


class MemoryStatus(str, Enum):
    candidate = "candidate"
    active = "active"
    superseded = "superseded"
    revoked = "revoked"
    expired = "expired"
    quarantined = "quarantined"


class MemoryNamespaceType(str, Enum):
    run = "run"
    task = "task"
    session = "session"
    user = "user"


@dataclass(frozen=True)
class MemoryNamespace:
    type: MemoryNamespaceType
    id: str

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise ValueError("Memory namespace identity must be non-empty")

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type.value, "id": self.id}


class MemoryValidationError(ValueError):
    pass


class MemoryConflictError(RuntimeError):
    pass


CROSS_SESSION_MEMORY_KINDS = frozenset(kind.value for kind in MemoryKind)
ACTIVE_MEMORY_STATUSES = frozenset({MemoryStatus.active.value})
TERMINAL_MEMORY_STATUSES = frozenset(
    {
        MemoryStatus.superseded.value,
        MemoryStatus.revoked.value,
        MemoryStatus.expired.value,
    }
)

MEMORY_LIFECYCLE_TRANSITIONS: dict[MemoryStatus, frozenset[MemoryStatus]] = {
    MemoryStatus.candidate: frozenset(
        {
            MemoryStatus.active,
            MemoryStatus.quarantined,
            MemoryStatus.revoked,
        }
    ),
    MemoryStatus.active: frozenset(
        {
            MemoryStatus.superseded,
            MemoryStatus.revoked,
            MemoryStatus.expired,
            MemoryStatus.quarantined,
        }
    ),
    MemoryStatus.quarantined: frozenset(
        {
            MemoryStatus.candidate,
            MemoryStatus.revoked,
        }
    ),
    MemoryStatus.superseded: frozenset(),
    MemoryStatus.revoked: frozenset(),
    MemoryStatus.expired: frozenset(),
}


def normalize_memory_kind(value: str | MemoryKind) -> MemoryKind | None:
    if isinstance(value, MemoryKind):
        return value
    normalized = str(value or "").strip().lower().replace("-", "_")
    try:
        return MemoryKind(normalized)
    except ValueError:
        return None


def validate_memory_transition(
    current: str | MemoryStatus,
    target: str | MemoryStatus,
) -> tuple[MemoryStatus, MemoryStatus]:
    current_status = MemoryStatus(current)
    target_status = MemoryStatus(target)
    if target_status not in MEMORY_LIFECYCLE_TRANSITIONS[current_status]:
        raise ValueError(
            f"Invalid Memory lifecycle transition: {current_status.value} -> {target_status.value}"
        )
    return current_status, target_status
