import pytest

from app.memory.domain import (
    MemoryKind,
    MemoryStatus,
    normalize_memory_kind,
    validate_memory_transition,
)


def test_memory_kind_normalization_supports_typed_and_legacy_values():
    assert normalize_memory_kind("semantic_fact") == MemoryKind.semantic_fact
    assert normalize_memory_kind("source-summary") == MemoryKind.episodic_experience
    assert normalize_memory_kind("unknown") is None
    assert normalize_memory_kind("source_summary", allow_legacy_alias=False) is None


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (MemoryStatus.candidate, MemoryStatus.active),
        (MemoryStatus.candidate, MemoryStatus.quarantined),
        (MemoryStatus.quarantined, MemoryStatus.candidate),
        (MemoryStatus.active, MemoryStatus.superseded),
        (MemoryStatus.active, MemoryStatus.revoked),
        (MemoryStatus.active, MemoryStatus.expired),
    ],
)
def test_valid_memory_lifecycle_transitions(current, target):
    assert validate_memory_transition(current, target) == (current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (MemoryStatus.revoked, MemoryStatus.active),
        (MemoryStatus.expired, MemoryStatus.active),
        (MemoryStatus.superseded, MemoryStatus.active),
        (MemoryStatus.candidate, MemoryStatus.expired),
    ],
)
def test_terminal_or_unsupported_memory_transitions_are_rejected(current, target):
    with pytest.raises(ValueError, match="Invalid Memory lifecycle transition"):
        validate_memory_transition(current, target)
