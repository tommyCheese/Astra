"""Generate and normalize consolidation proposals."""

from __future__ import annotations

from app.application.memory.consolidation.contracts import (
    ConsolidationAction,
    ConsolidationInputManifest,
    ConsolidationOperation,
    ConsolidationProposal,
    FrozenMemoryInput,
    equivalent_content_key,
    normalize_memory_key,
)


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
