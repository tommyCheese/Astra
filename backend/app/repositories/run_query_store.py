from typing import Any

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import joinedload, selectinload

from app.db.model_base import utc_now
from app.db.models.conversations import TaskRecord
from app.db.models.memory import MemoryRecord
from app.db.models.runs import AgentTurnRecord, RunRecord
from app.db.models.skills import RunSkillSnapshotRecord
from app.repositories.run_store_support import run_detail_options


class RunQueryStore:
    async def get_run(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
            .options(*run_detail_options())
        )
        return result.scalar_one_or_none()

    async def get_run_initial(self, run_id: str) -> tuple[RunRecord | None, bool]:
        """Load a one-query optimistic snapshot unless the Run is already terminal."""
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
        )
        run = result.scalar_one_or_none()
        if run is None or run.status not in self.TERMINAL_STATUSES:
            return run, False
        return await self.get_run(run_id), True

    async def get_run_status(self, run_id: str) -> str | None:
        result = await self.session.execute(select(RunRecord.status).where(RunRecord.id == run_id))
        return result.scalar_one_or_none()

    async def list_recent_runs(self, limit: int = 100) -> list[RunRecord]:
        result = await self.session.execute(
            select(RunRecord)
            .order_by(RunRecord.created_at.desc())
            .limit(limit)
            .options(*run_detail_options())
        )
        return list(result.scalars().all())

    async def require_run(self, run_id: str) -> RunRecord:
        run = await self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    async def require_run_core(self, run_id: str) -> RunRecord:
        """Load only the Run row for latency-sensitive runtime decisions."""
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    async def require_run_startup(
        self,
        run_id: str,
        *,
        include_skills: bool,
    ) -> tuple[RunRecord, RunSkillSnapshotRecord | None]:
        """Load a Run and its optional Skill snapshot in one startup query."""
        result = await self.session.execute(
            select(RunRecord, RunSkillSnapshotRecord)
            .outerjoin(
                RunSkillSnapshotRecord,
                and_(
                    RunSkillSnapshotRecord.run_id == RunRecord.id,
                    include_skills,
                ),
            )
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        if row is None:
            raise ValueError(f"Run not found: {run_id}")
        return row[0], row[1]

    async def require_run_runtime(self, run_id: str) -> RunRecord:
        """Load the small relationship set required to resume the Agent loop."""
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(RunRecord.tool_calls),
                joinedload(RunRecord.turns),
            )
        )
        run = result.unique().scalar_one_or_none()
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    async def require_run_quick_context(
        self,
        run_id: str,
        *,
        include_skills: bool,
        memory_limit: int = 8,
    ) -> tuple[RunRecord, list[MemoryRecord], RunSkillSnapshotRecord | None]:
        """Load standard-mode context inputs in one database round trip."""
        query = (
            select(RunRecord, MemoryRecord, RunSkillSnapshotRecord)
            .outerjoin(
                MemoryRecord,
                and_(
                    MemoryRecord.run_id == RunRecord.id,
                    MemoryRecord.confidence >= 0.0,
                ),
            )
            .outerjoin(
                RunSkillSnapshotRecord,
                and_(
                    RunSkillSnapshotRecord.run_id == RunRecord.id,
                    include_skills,
                ),
            )
            .where(RunRecord.id == run_id)
            .order_by(MemoryRecord.updated_at.desc())
            .limit(memory_limit)
        )
        rows = (await self.session.execute(query)).all()
        if not rows:
            raise ValueError(f"Run not found: {run_id}")
        run = rows[0][0]
        memories = list(dict.fromkeys(memory for _, memory, _ in rows if memory is not None))
        skill_snapshot = next(
            (snapshot for _, _, snapshot in rows if snapshot is not None),
            None,
        )
        return run, memories, skill_snapshot

    async def count_agent_turns(self, run_id: str) -> int:
        count = await self.session.scalar(
            select(func.count(AgentTurnRecord.id)).where(AgentTurnRecord.run_id == run_id)
        )
        return int(count or 0)

    async def list_task_runs(self, task_id: str) -> list[RunRecord]:
        result = await self.session.execute(
            select(RunRecord).where(RunRecord.task_id == task_id).order_by(RunRecord.created_at)
        )
        return list(result.scalars().all())

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        summary: str | None = None,
        result: dict[str, Any] | None = None,
        loaded_run: RunRecord | None = None,
    ) -> None:
        run = loaded_run or await self._run_for_status_update(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        if run.status == "cancelled" and status != "cancelled":
            return
        await self._apply_run_status(run, status, summary, result, loaded_run is not None)
        await self.add_event(run_id, "run.status_changed", {"status": status})
        await self.session.flush()

    async def _run_for_status_update(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
            .options(selectinload(RunRecord.task))
        )
        return result.scalar_one_or_none()

    async def _apply_run_status(
        self,
        run: RunRecord,
        status: str,
        summary: str | None,
        result: dict[str, Any] | None,
        was_loaded: bool,
    ) -> None:
        run.status = status
        run.updated_at = utc_now()
        if not was_loaded:
            run.task.updated_at = run.updated_at
        else:
            await self.session.execute(
                update(TaskRecord)
                .where(TaskRecord.id == run.task_id)
                .values(updated_at=run.updated_at)
            )
        if status == "planning" and run.started_at is None:
            run.started_at = utc_now()
        if status in {"completed", "completed_with_warnings", "failed", "blocked", "cancelled"}:
            run.completed_at = utc_now()
        if summary is not None:
            run.summary = summary
        if result is not None:
            run.result = result
