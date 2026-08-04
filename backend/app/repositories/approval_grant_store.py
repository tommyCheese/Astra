"""Persist reusable approval grants and their atomic consumption."""

from __future__ import annotations

from sqlalchemy import select, update

from app.db.model_base import utc_now
from app.db.models.permissions import ApprovalGrantRecord


class ApprovalGrantStore:
    async def list_approval_grants(
        self, run_id: str, tool_name: str, tool_version: str
    ) -> list[ApprovalGrantRecord]:
        run = await self.require_run(run_id)
        now = utc_now()
        result = await self.session.execute(
            select(ApprovalGrantRecord).where(
                ApprovalGrantRecord.tool_name == tool_name,
                ApprovalGrantRecord.tool_version == tool_version,
                ApprovalGrantRecord.status == "active",
                ApprovalGrantRecord.revoked_at.is_(None),
                (ApprovalGrantRecord.expires_at.is_(None) | (ApprovalGrantRecord.expires_at > now)),
                (
                    (ApprovalGrantRecord.scope == "run") & (ApprovalGrantRecord.run_id == run_id)
                    | (ApprovalGrantRecord.scope == "task")
                    & (ApprovalGrantRecord.task_id == run.task_id)
                ),
            )
        )
        return [
            grant
            for grant in result.scalars().all()
            if grant.max_uses is None or grant.use_count < grant.max_uses
        ]

    async def consume_approval_grant(self, grant_id: str) -> ApprovalGrantRecord:
        return (await self.consume_approval_grants([grant_id]))[0]

    async def consume_approval_grants(
        self, grant_ids: list[str] | tuple[str, ...]
    ) -> list[ApprovalGrantRecord]:
        ordered_ids = sorted(set(grant_ids))
        if not ordered_ids:
            return []
        grants = await self._approval_grants_in_order(ordered_ids)
        now = utc_now()
        for grant in grants:
            self._validate_consumable_grant(grant, now)
        for grant in grants:
            await self._claim_grant_use(grant, now)
        await self.session.flush()
        for grant in grants:
            await self.session.refresh(grant)
        return grants

    async def _approval_grants_in_order(
        self,
        ordered_ids: list[str],
    ) -> list[ApprovalGrantRecord]:
        result = await self.session.execute(
            select(ApprovalGrantRecord).where(ApprovalGrantRecord.id.in_(ordered_ids))
        )
        by_id = {grant.id: grant for grant in result.scalars().all()}
        missing = [grant_id for grant_id in ordered_ids if grant_id not in by_id]
        if missing:
            raise ValueError(f"Approval Grant not found: {missing[0]}")
        return [by_id[grant_id] for grant_id in ordered_ids]

    def _validate_consumable_grant(self, grant: ApprovalGrantRecord, now) -> None:
        if grant.status != "active" or grant.revoked_at is not None:
            raise ValueError("Approval Grant is not active")
        if grant.expires_at is not None:
            expires_at = grant.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=now.tzinfo)
            if expires_at <= now:
                raise ValueError("Approval Grant has expired")
        if grant.max_uses is not None and grant.use_count >= grant.max_uses:
            raise ValueError("Approval Grant usage limit is exhausted")

    async def _claim_grant_use(self, grant: ApprovalGrantRecord, now) -> None:
        consumed = await self.session.execute(
            update(ApprovalGrantRecord)
            .where(
                ApprovalGrantRecord.id == grant.id,
                ApprovalGrantRecord.status == "active",
                ApprovalGrantRecord.revoked_at.is_(None),
                ApprovalGrantRecord.use_count == grant.use_count,
            )
            .values(use_count=grant.use_count + 1, last_used_at=now)
        )
        if consumed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("Approval Grant changed while being consumed")

    async def revoke_approval_grant(self, grant_id: str) -> ApprovalGrantRecord:
        grant = await self.session.get(ApprovalGrantRecord, grant_id)
        if grant is None:
            raise ValueError(f"Approval Grant not found: {grant_id}")
        if grant.revoked_at is None:
            grant.status = "revoked"
            grant.revoked_at = utc_now()
            await self.session.flush()
        return grant

    async def invalidate_approval_grants_for_tool_identity(
        self,
        run_id: str,
        *,
        tool_name: str,
        tool_version: str,
        schema_digest: str | None = None,
        analyzer_digest: str | None = None,
    ) -> list[ApprovalGrantRecord]:
        run = await self.require_run(run_id)
        result = await self.session.execute(
            select(ApprovalGrantRecord).where(
                ApprovalGrantRecord.tool_name == tool_name,
                ApprovalGrantRecord.status == "active",
                (
                    (ApprovalGrantRecord.scope == "run") & (ApprovalGrantRecord.run_id == run_id)
                    | (ApprovalGrantRecord.scope == "task")
                    & (ApprovalGrantRecord.task_id == run.task_id)
                ),
            )
        )
        expected_identity = {
            "tool_version": tool_version,
            "schema_digest": schema_digest,
            "analyzer_digest": analyzer_digest,
        }
        invalidated = [
            grant
            for grant in result.scalars().all()
            if self._identity_changed(grant, expected_identity)
        ]
        for grant in invalidated:
            grant.status = "invalidated"
            grant.revoked_at = utc_now()
        if invalidated:
            await self.session.flush()
        return invalidated

    def _identity_changed(
        self,
        grant: ApprovalGrantRecord,
        expected_identity: dict[str, str | None],
    ) -> bool:
        constraints = grant.invocation_constraints or {}
        return any(
            constraints.get(key) is not None and constraints[key] != value
            for key, value in expected_identity.items()
        )
