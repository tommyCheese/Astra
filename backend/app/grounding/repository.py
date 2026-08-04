from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.runs import EvidenceRecord
from app.grounding.ledger import EvidenceConflictError, EvidenceLedger
from app.grounding.schemas import EvidenceFragment, EvidenceLineage


class EvidenceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def append(
        self,
        run_id: str,
        fragment: EvidenceFragment,
        *,
        agent_execution_id: str | None = None,
    ) -> EvidenceRecord:
        if fragment.lineage.run_id not in {None, run_id}:
            raise EvidenceConflictError("evidence Run lineage does not match persistence scope")
        normalized = fragment.model_copy(
            update={
                "lineage": fragment.lineage.model_copy(update={"run_id": run_id})
            }
        )
        existing = await self.session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.run_id == run_id,
                EvidenceRecord.evidence_key == normalized.evidence_key,
            )
        )
        if existing is not None:
            if existing.payload_digest != normalized.payload_digest:
                raise EvidenceConflictError(
                    f"conflicting evidence replay for {normalized.evidence_key}"
                )
            if (
                agent_execution_id is not None
                and existing.agent_execution_id not in {None, agent_execution_id}
            ):
                raise EvidenceConflictError(
                    "evidence replay crosses AgentExecution isolation"
                )
            return existing
        record = EvidenceRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            evidence_id=normalized.id,
            evidence_key=normalized.evidence_key,
            kind=normalized.kind.value,
            payload_digest=normalized.payload_digest,
            fragment=normalized.model_dump(mode="json", exclude_none=True),
            plan_node_id=normalized.lineage.plan_node_id,
            node_execution_id=normalized.lineage.node_execution_id,
            tool_call_id=normalized.lineage.tool_call_id,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
            return record
        except IntegrityError as exc:
            # Another node may have committed the same deterministic key while
            # this invocation was normalizing its result.
            existing = await self.session.scalar(
                select(EvidenceRecord).where(
                    EvidenceRecord.run_id == run_id,
                    EvidenceRecord.evidence_key == normalized.evidence_key,
                )
            )
            if existing is None:
                raise
            if existing.payload_digest != normalized.payload_digest:
                raise EvidenceConflictError(
                    f"conflicting evidence replay for {normalized.evidence_key}"
                ) from exc
            if (
                agent_execution_id is not None
                and existing.agent_execution_id not in {None, agent_execution_id}
            ):
                raise EvidenceConflictError(
                    "evidence replay crosses AgentExecution isolation"
                ) from exc
            return existing

    async def append_many(
        self,
        run_id: str,
        fragments: Iterable[EvidenceFragment],
    ) -> list[EvidenceRecord]:
        return [await self.append(run_id, fragment) for fragment in fragments]

    async def list_for_run(self, run_id: str) -> list[EvidenceRecord]:
        result = await self.session.execute(
            select(EvidenceRecord)
            .where(EvidenceRecord.run_id == run_id)
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
        )
        return list(result.scalars().all())

    async def ledger_for_run(self, run_id: str) -> EvidenceLedger:
        records = await self.list_for_run(run_id)
        return EvidenceLedger(
            EvidenceFragment.model_validate(record.fragment) for record in records
        )


class EvidenceWriter:
    def __init__(self, repository: EvidenceRepository):
        self.repository = repository

    async def write(
        self,
        run_id: str,
        fragments: Iterable[EvidenceFragment],
        *,
        plan_node_id: str | None = None,
        node_execution_id: str | None = None,
        tool_call_id: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> list[EvidenceRecord]:
        bound = [
            fragment.model_copy(
                update={
                    "lineage": EvidenceLineage(
                        run_id=run_id,
                        plan_node_id=plan_node_id
                        or fragment.lineage.plan_node_id,
                        node_execution_id=node_execution_id
                        or fragment.lineage.node_execution_id,
                        tool_call_id=tool_call_id or fragment.lineage.tool_call_id,
                        artifact_ids=list(
                            dict.fromkeys(
                                [
                                    *fragment.lineage.artifact_ids,
                                    *(artifact_ids or []),
                                ]
                            )
                        ),
                    )
                }
            )
            for fragment in fragments
        ]
        return await self.repository.append_many(run_id, bound)
