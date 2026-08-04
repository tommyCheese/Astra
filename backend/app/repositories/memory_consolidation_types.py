from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.models.memory import MemoryConsolidationJobRecord, MemoryRecord
from app.memory.consolidation import ConsolidationInputManifest, ConsolidationProposal


@dataclass(frozen=True)
class PublicationContext:
    job: MemoryConsolidationJobRecord
    manifest: ConsolidationInputManifest
    proposal: ConsolidationProposal
    source_by_id: dict[str, MemoryRecord]
    published_at: datetime


@dataclass(frozen=True)
class RollbackManifest:
    original: MemoryConsolidationJobRecord
    outputs: list[dict[str, Any]]
    replacements: list[dict[str, Any]]
    rolled_back_at: datetime
