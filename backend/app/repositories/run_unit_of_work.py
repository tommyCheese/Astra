from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.run_artifact_sandbox_store import RunArtifactSandboxStore
from app.repositories.run_event_store import RunEventStore
from app.repositories.run_lifecycle_store import RunLifecycleStore
from app.repositories.run_memory_store import RunMemoryStore
from app.repositories.run_step_turn_store import RunStepTurnStore
from app.repositories.run_tool_approval_store import RunToolApprovalStore


class RunUnitOfWork(
    RunLifecycleStore,
    RunStepTurnStore,
    RunToolApprovalStore,
    RunArtifactSandboxStore,
    RunMemoryStore,
    RunEventStore,
):
    """Explicit transaction boundary for one Run persistence use case.

    Store methods stage and flush changes; application services decide when the
    complete cross-store use case commits or rolls back.
    """

    TERMINAL_STATUSES = frozenset(
        {
            "completed",
            "completed_with_warnings",
            "failed",
            "blocked",
            "waiting_user",
            "cancelled",
        }
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def __aenter__(self) -> RunUnitOfWork:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()
