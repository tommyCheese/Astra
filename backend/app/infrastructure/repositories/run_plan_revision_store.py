import uuid
from typing import Any

from sqlalchemy import update

from app.common.schemas.agent.execution_state import AgentState
from app.common.schemas.agent.planning import PlanGraphSnapshotEvent, PlanRevisionEvent
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.plans import PlanRecord
from app.infrastructure.db.models.runs import RunRecord


class RunPlanRevisionStore:
    async def confirm_waiting_plan(
        self,
        run_id: str,
        *,
        continuation_token: str,
        plan_id: str,
        expected_plan_version: int,
        expected_state_version: int,
    ) -> RunRecord:
        from app.common.schemas.agent.execution_state import AgentState
        from app.infrastructure.repositories.plans import PlanRepository, plan_to_view

        run = await self.require_run(run_id)
        waiting = run.waiting_state or {}
        if run.status != "waiting_user" or waiting.get("kind") != "plan_confirmation":
            raise ValueError("Run is not waiting for plan confirmation")
        bindings = {
            "continuation_token": continuation_token,
            "plan_id": plan_id,
            "plan_version": expected_plan_version,
            "state_version": expected_state_version,
        }
        if any(waiting.get(key) != value for key, value in bindings.items()):
            raise ValueError("Invalid or stale plan confirmation")
        if run.state_version != expected_state_version:
            raise ValueError("Invalid or stale plan confirmation")
        plan_repository = PlanRepository(self.session)
        plan = await plan_repository.require(plan_id)
        if plan.run_id != run_id or plan.version != expected_plan_version or plan.status != "planned":
            raise ValueError("Invalid or stale plan confirmation")
        plan = await plan_repository.activate(plan_id, expected_version=expected_plan_version)
        state = AgentState.model_validate(run.agent_state)
        state.active_plan_id = plan.id
        state.active_plan_version = plan.version
        state.active_executions = []
        state.version = expected_state_version + 1
        run.agent_state = state.model_dump(mode="json")
        run.state_version = state.version
        run.plan_graph = plan_to_view(plan).model_dump(mode="json")
        run.waiting_state = None
        run.status = "executing"
        run.completed_at = None
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "plan.confirmed",
            {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "state_version": state.version,
            },
        )
        await self.session.flush()
        return run

    async def claim_plan_revision(
        self,
        run_id: str,
        *,
        continuation_token: str,
        plan_id: str,
        expected_plan_version: int,
        expected_state_version: int,
    ) -> tuple[RunRecord, PlanRecord]:
        from app.infrastructure.repositories.plans import PlanRepository

        run = await self.require_run(run_id)
        waiting = run.waiting_state or {}
        bindings = {
            "continuation_token": continuation_token,
            "plan_id": plan_id,
            "plan_version": expected_plan_version,
            "state_version": expected_state_version,
        }
        if not self._revision_binding_matches(
            run,
            waiting,
            bindings,
            expected_state_version,
        ):
            raise ValueError("Invalid or stale plan revision")
        plan = await PlanRepository(self.session).require(plan_id)
        if not self._revisable_plan_matches(plan, run_id, expected_plan_version):
            raise ValueError("Invalid or stale plan revision")
        claimed = await self.session.execute(
            update(RunRecord)
            .where(
                RunRecord.id == run_id,
                RunRecord.status == "waiting_user",
                RunRecord.state_version == expected_state_version,
            )
            .values(status="planning", waiting_state=None, updated_at=utc_now())
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("Invalid or stale plan revision")
        await self.add_event(
            run_id,
            "plan.revision.started",
            PlanRevisionEvent(
                plan_id=plan.id,
                plan_version=plan.version,
                state_version=run.state_version,
            ).model_dump(mode="json"),
        )
        await self.session.flush()
        return await self.require_run(run_id), plan

    @staticmethod
    def _revision_binding_matches(
        run: RunRecord,
        waiting: dict[str, Any],
        bindings: dict[str, Any],
        expected_state_version: int,
    ) -> bool:
        return (
            run.status == "waiting_user"
            and waiting.get("kind") == "plan_confirmation"
            and all(waiting.get(key) == value for key, value in bindings.items())
            and run.state_version == expected_state_version
        )

    @staticmethod
    def _revisable_plan_matches(
        plan: PlanRecord,
        run_id: str,
        expected_version: int,
    ) -> bool:
        return plan.run_id == run_id and plan.version == expected_version and plan.status == "planned"

    async def reject_plan_revision(
        self,
        run_id: str,
        *,
        plan_id: str,
        plan_version: int,
        state_version: int,
        error_code: str,
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status != "planning" or run.state_version != state_version:
            raise ValueError("Plan revision state changed")
        waiting_state = {
            "kind": "plan_confirmation",
            "plan_id": plan_id,
            "plan_version": plan_version,
            "state_version": state_version,
            "request": "计划调整失败，可修改要求后重试或执行当前版本。",
            "continuation_token": str(uuid.uuid4()),
        }
        run.status = "waiting_user"
        run.waiting_state = waiting_state
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "plan.revision.rejected",
            PlanRevisionEvent(
                plan_id=plan_id,
                plan_version=plan_version,
                state_version=state_version,
                error_code=error_code,
            ).model_dump(mode="json"),
        )
        await self.add_event(run_id, "run.waiting_user", waiting_state)
        await self.session.flush()
        return run

    async def complete_plan_revision(
        self,
        run_id: str,
        *,
        previous_plan: PlanRecord,
        revised_plan: PlanRecord,
    ) -> RunRecord:
        from app.infrastructure.repositories.plans import plan_to_view

        run = await self.require_run(run_id)
        state = AgentState.model_validate(run.agent_state)
        if (
            run.status != "planning"
            or state.active_plan_version != previous_plan.version
            or revised_plan.supersedes_plan_id != previous_plan.id
        ):
            raise ValueError("Plan revision state changed")
        previous_plan.status = "superseded"
        state.active_plan_id = None
        state.active_plan_version = revised_plan.version
        state.active_executions = []
        state.version = run.state_version + 1
        graph = plan_to_view(revised_plan)
        waiting_state = {
            "kind": "plan_confirmation",
            "plan_id": revised_plan.id,
            "plan_version": revised_plan.version,
            "state_version": state.version,
            "request": "调整后的计划已生成，确认后执行。",
            "continuation_token": str(uuid.uuid4()),
        }
        run.agent_state = state.model_dump(mode="json")
        run.state_version = state.version
        run.plan_graph = graph.model_dump(mode="json")
        run.status = "waiting_user"
        run.waiting_state = waiting_state
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "plan.revision.completed",
            PlanRevisionEvent(
                plan_id=previous_plan.id,
                plan_version=previous_plan.version,
                state_version=state.version,
                revised_plan_id=revised_plan.id,
                revised_plan_version=revised_plan.version,
            ).model_dump(mode="json"),
        )
        await self.add_event(
            run_id,
            "plan.graph.snapshot",
            PlanGraphSnapshotEvent(
                plan_id=revised_plan.id,
                plan_version=revised_plan.version,
                graph=graph,
            ).model_dump(mode="json"),
        )
        await self.add_event(run_id, "run.waiting_user", waiting_state)
        await self.session.flush()
        return run
