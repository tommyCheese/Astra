from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.executions import ModelInvocationRecord
from app.infrastructure.db.models.runs import RunEventRecord
from app.infrastructure.db.models.skills import (
    RunSkillSnapshotRecord,
    SkillRecord,
    SkillRevisionRecord,
)


async def build_skill_metrics(session: AsyncSession) -> dict[str, Any]:
    counts = await _entity_counts(session)
    snapshot_rows = list((await session.scalars(select(RunSkillSnapshotRecord))).all())
    catalog_chars = [len(json.dumps(item.catalog, ensure_ascii=False, sort_keys=True)) for item in snapshot_rows]
    mode_counts = {mode: sum(item.answer_mode == mode for item in snapshot_rows) for mode in ("standard", "trusted")}
    startup_ms = await _startup_latencies(session, snapshot_rows)
    event_counts = await _event_counts(session)
    return (
        counts
        | _snapshot_metrics(snapshot_rows, catalog_chars, mode_counts, startup_ms)
        | {
            "activation_conflicts": int(event_counts.get("skill.activation_conflict", 0)),
            "attributed_actions": int(event_counts.get("skill.attributed_action", 0)),
        }
    )


async def _entity_counts(session: AsyncSession) -> dict[str, int]:
    async def count(model, *criteria) -> int:
        statement = select(func.count()).select_from(model)
        if criteria:
            statement = statement.where(*criteria)
        return int(await session.scalar(statement) or 0)

    return {
        "skills": await count(SkillRecord),
        "published_revisions": await count(SkillRevisionRecord, SkillRevisionRecord.test_only.is_(False)),
        "draft_tests": await count(SkillRevisionRecord, SkillRevisionRecord.test_only.is_(True)),
        "run_snapshots": await count(RunSkillSnapshotRecord),
    }


async def _startup_latencies(session: AsyncSession, snapshot_rows: list[RunSkillSnapshotRecord]) -> dict[str, list[int]]:
    first_invocations = dict(
        (
            await session.execute(
                select(
                    ModelInvocationRecord.run_id,
                    func.min(ModelInvocationRecord.started_at),
                ).group_by(ModelInvocationRecord.run_id)
            )
        ).all()
    )
    startup_ms: dict[str, list[int]] = {"standard": [], "trusted": []}
    for item in snapshot_rows:
        first = first_invocations.get(item.run_id)
        if first is not None:
            startup_ms.setdefault(item.answer_mode, []).append(max(0, int((first - item.created_at).total_seconds() * 1000)))
    return startup_ms


async def _event_counts(session: AsyncSession) -> dict[str, int]:
    return dict(
        (
            await session.execute(
                select(RunEventRecord.type, func.count())
                .where(RunEventRecord.type.like("skill.%"))
                .group_by(RunEventRecord.type)
            )
        ).all()
    )


def _snapshot_metrics(
    snapshot_rows: list[RunSkillSnapshotRecord],
    catalog_chars: list[int],
    mode_counts: dict[str, int],
    startup_ms: dict[str, list[int]],
) -> dict[str, Any]:
    return {
        "catalog_entries": int(sum(len(items) for items in (item.catalog for item in snapshot_rows))),
        "activations": int(sum(len(items) for items in (item.activations for item in snapshot_rows))),
        "resource_bytes": int(
            sum(
                sum(int(item.get("size_bytes", 0)) for item in items)
                for items in (item.resource_reads for item in snapshot_rows)
            )
        ),
        "catalog_metadata_chars": {
            "total": sum(catalog_chars),
            "max": max(catalog_chars, default=0),
        },
        "answer_modes": mode_counts,
        "catalog_to_first_model_ms": {
            mode: {
                "samples": len(values),
                "average": round(sum(values) / len(values), 2) if values else None,
                "max": max(values, default=None),
            }
            for mode, values in startup_ms.items()
        },
    }
