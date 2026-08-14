from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.core.errors import AstraResourceNotFoundError, AstraStateConflictError
from app.infrastructure.db.model_base import as_utc, utc_now
from app.infrastructure.db.models.ag_ui import AgUiInterruptBindingRecord, AgUiRunBindingRecord


@dataclass(frozen=True)
class RunBindingCreate:
    principal_id: str
    thread_id: str
    protocol_run_id: str
    internal_task_id: str
    internal_run_id: str
    profile_version: str
    input_fingerprint: str
    parent_protocol_run_id: str | None = None


@dataclass(frozen=True)
class InterruptBindingCreate:
    interrupt_id: str
    run_binding_id: str
    internal_run_id: str
    waiting_kind: str
    response_schema: dict[str, Any]
    server_binding: dict[str, Any]
    approval_id: str | None = None
    expected_state_version: int | None = None
    expires_at: datetime | None = None


class AgUiBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_run_binding(
        self,
        principal_id: str,
        thread_id: str,
        protocol_run_id: str,
    ) -> AgUiRunBindingRecord | None:
        return await self.session.scalar(
            select(AgUiRunBindingRecord).where(
                AgUiRunBindingRecord.principal_id == principal_id,
                AgUiRunBindingRecord.thread_id == thread_id,
                AgUiRunBindingRecord.protocol_run_id == protocol_run_id,
            )
        )

    async def get_run_binding_by_internal(self, internal_run_id: str) -> AgUiRunBindingRecord | None:
        return await self.session.scalar(
            select(AgUiRunBindingRecord)
            .where(AgUiRunBindingRecord.internal_run_id == internal_run_id)
            .order_by(AgUiRunBindingRecord.created_at.desc())
            .limit(1)
        )

    async def require_interrupt_for_principal(
        self,
        interrupt_id: str,
        principal_id: str,
        thread_id: str,
    ) -> tuple[AgUiInterruptBindingRecord, AgUiRunBindingRecord]:
        row = (
            await self.session.execute(
                select(AgUiInterruptBindingRecord, AgUiRunBindingRecord)
                .join(AgUiRunBindingRecord, AgUiInterruptBindingRecord.run_binding_id == AgUiRunBindingRecord.id)
                .where(
                    AgUiInterruptBindingRecord.interrupt_id == interrupt_id,
                    AgUiRunBindingRecord.principal_id == principal_id,
                    AgUiRunBindingRecord.thread_id == thread_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise AstraResourceNotFoundError("AG_UI_INTERRUPT_NOT_FOUND", "找不到指定 AG-UI 中断。")
        return row

    async def create_run_binding(self, command: RunBindingCreate) -> tuple[AgUiRunBindingRecord, bool]:
        existing = await self.get_run_binding(command.principal_id, command.thread_id, command.protocol_run_id)
        if existing is not None:
            self._verify_duplicate_run(existing, command)
            return existing, False
        record = AgUiRunBindingRecord(**command.__dict__, lifecycle_status="created")
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
            return record, True
        except IntegrityError:
            existing = await self.get_run_binding(command.principal_id, command.thread_id, command.protocol_run_id)
            if existing is None:
                raise
            self._verify_duplicate_run(existing, command)
            return existing, False

    async def require_run_binding(
        self,
        principal_id: str,
        thread_id: str,
        protocol_run_id: str,
    ) -> AgUiRunBindingRecord:
        binding = await self.get_run_binding(principal_id, thread_id, protocol_run_id)
        if binding is None:
            raise AstraResourceNotFoundError("AG_UI_RUN_NOT_FOUND", "找不到指定 AG-UI 运行。")
        return binding

    async def set_run_status(self, binding_id: str, status: str) -> None:
        await self.session.execute(
            update(AgUiRunBindingRecord)
            .where(AgUiRunBindingRecord.id == binding_id)
            .values(lifecycle_status=status, updated_at=utc_now())
        )

    async def get_interrupt(self, interrupt_id: str) -> AgUiInterruptBindingRecord | None:
        return await self.session.scalar(
            select(AgUiInterruptBindingRecord).where(AgUiInterruptBindingRecord.interrupt_id == interrupt_id)
        )

    async def update_interrupt_server_binding(self, interrupt_id: str, patch: dict[str, Any]) -> None:
        binding = await self.get_interrupt(interrupt_id)
        if binding is None:
            return
        binding.server_binding = {**(binding.server_binding or {}), **patch}
        binding.updated_at = utc_now()
        await self.session.flush()

    async def create_interrupt(
        self,
        command: InterruptBindingCreate,
    ) -> tuple[AgUiInterruptBindingRecord, bool]:
        existing = await self.get_interrupt(command.interrupt_id)
        if existing is not None:
            self._verify_duplicate_interrupt(existing, command)
            return existing, False
        record = AgUiInterruptBindingRecord(**command.__dict__, status="open", version=1)
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
            return record, True
        except IntegrityError:
            existing = await self.get_interrupt(command.interrupt_id)
            if existing is None:
                raise
            self._verify_duplicate_interrupt(existing, command)
            return existing, False

    async def consume_interrupt(
        self,
        *,
        interrupt_id: str,
        run_binding_id: str,
        expected_version: int,
        outcome: dict[str, Any],
        now: datetime | None = None,
    ) -> tuple[AgUiInterruptBindingRecord, bool]:
        timestamp = now or utc_now()
        result = await self.session.execute(
            update(AgUiInterruptBindingRecord)
            .where(
                AgUiInterruptBindingRecord.interrupt_id == interrupt_id,
                AgUiInterruptBindingRecord.run_binding_id == run_binding_id,
                AgUiInterruptBindingRecord.status == "open",
                AgUiInterruptBindingRecord.version == expected_version,
                or_(AgUiInterruptBindingRecord.expires_at.is_(None), AgUiInterruptBindingRecord.expires_at > timestamp),
            )
            .values(
                status="consumed",
                version=AgUiInterruptBindingRecord.version + 1,
                consumed_outcome=outcome,
                consumed_at=timestamp,
                updated_at=timestamp,
            )
        )
        await self.session.flush()
        binding = await self.get_interrupt(interrupt_id)
        if binding is None or binding.run_binding_id != run_binding_id:
            raise AstraResourceNotFoundError("AG_UI_INTERRUPT_NOT_FOUND", "找不到指定 AG-UI 中断。")
        if result.rowcount == 1:
            return binding, True
        if binding.status == "consumed" and binding.consumed_outcome == outcome:
            return binding, False
        if binding.expires_at is not None and as_utc(binding.expires_at) <= as_utc(timestamp):
            raise AstraStateConflictError("AG_UI_INTERRUPT_EXPIRED", "AG-UI 中断已经过期。")
        raise AstraStateConflictError("AG_UI_INTERRUPT_STALE", "AG-UI 中断版本无效或已经处理。")

    @staticmethod
    def _verify_duplicate_run(existing: AgUiRunBindingRecord, command: RunBindingCreate) -> None:
        if existing.input_fingerprint != command.input_fingerprint or existing.internal_task_id != command.internal_task_id:
            raise AstraStateConflictError("AG_UI_RUN_CONFLICT", "协议 Run 标识已绑定到其他请求。")

    @staticmethod
    def _verify_duplicate_interrupt(
        existing: AgUiInterruptBindingRecord,
        command: InterruptBindingCreate,
    ) -> None:
        if existing.run_binding_id != command.run_binding_id or existing.internal_run_id != command.internal_run_id:
            raise AstraStateConflictError("AG_UI_INTERRUPT_CONFLICT", "Interrupt 标识已绑定到其他运行。")
