import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select

from app.db.model_base import utc_now
from app.db.models.conversations import TaskRecord
from app.db.models.executions import AgentExecutionRecord
from app.db.models.runs import RunRecord
from app.repositories.run_store_support import safe_agent_profile_manifest
from app.run_management.run_configuration import (
    PreparedRunConfiguration,
    prepare_run_configuration,
)


class RunCoreStore:
    async def create_task_run(
        self,
        goal: str,
        model_policy: dict[str, Any],
        task_id: str | None = None,
        *,
        reasoning_policy: dict[str, Any] | None = None,
        answer_mode: str = "standard",
        execution_profile: dict[str, Any] | None = None,
        agent_profile_snapshot: dict[str, Any] | None = None,
        session_id: str | None = None,
        commit: bool = True,
    ) -> RunRecord:
        now = utc_now()
        prepared = prepare_run_configuration(
            model_policy=model_policy,
            reasoning_policy=reasoning_policy,
            answer_mode=answer_mode,
            execution_profile=execution_profile,
            agent_profile_snapshot=agent_profile_snapshot,
        )
        task = await self._task_for_run(goal, task_id, prepared, now)
        run = self._new_run(goal, task_id, session_id, task, prepared, now)
        self.session.add_all([task, run])
        await self.session.flush()
        self.session.add(self._root_execution(run, task, prepared, now))
        await self.session.flush()
        await self._record_created_run(run, goal, prepared, commit)
        return run

    async def _task_for_run(
        self,
        goal: str,
        task_id: str | None,
        prepared: PreparedRunConfiguration,
        now,
    ) -> TaskRecord:
        task = await self.session.get(TaskRecord, task_id) if task_id else None
        if task_id and task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task is None:
            return TaskRecord(
                title=goal[:240],
                description=goal,
                status="created",
                risk_level="low",
                preferred_answer_mode=prepared.answer_mode,
                created_at=now,
                updated_at=now,
            )
        task.preferred_answer_mode = prepared.answer_mode
        task.updated_at = now
        return task

    @staticmethod
    def _new_run(
        goal: str,
        task_id: str | None,
        session_id: str | None,
        task: TaskRecord,
        prepared: PreparedRunConfiguration,
        now,
    ) -> RunRecord:
        policy = {
            **prepared.model_policy,
            "conversation_goal": goal,
            "conversation_context_required": task_id is not None,
        }
        return RunRecord(
            task=task,
            memory_session_id=session_id,
            status="created",
            mode="web_agent",
            answer_mode=prepared.answer_mode,
            execution_profile=deepcopy(prepared.execution_profile),
            model_policy=policy,
            agent_profile_snapshot=deepcopy(prepared.agent_profile_snapshot),
            reasoning_policy=prepared.reasoning_policy,
            task_adapter="web",
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _root_execution(
        run: RunRecord,
        task: TaskRecord,
        prepared: PreparedRunConfiguration,
        now,
    ) -> AgentExecutionRecord:
        budgets = ((prepared.reasoning_policy.get("effective") or {}).get("subagents") or {}).get(
            "budgets"
        ) or {}
        return AgentExecutionRecord(
            run_id=run.id,
            task_id=task.id,
            execution_type="root",
            root_slot="root",
            request_id="root",
            depth=0,
            ordinal=0,
            contract={},
            context_manifest={},
            catalog_snapshot={},
            budget_envelope=deepcopy(budgets),
            budget_usage={},
            status="queued",
            phase="planning",
            checkpoint={},
            created_at=now,
            queued_at=now,
            updated_at=now,
        )

    async def _record_created_run(
        self,
        run: RunRecord,
        goal: str,
        prepared: PreparedRunConfiguration,
        commit: bool,
    ) -> None:
        await self.add_event(run.id, "run.created", {"goal": goal, "status": run.status})
        if prepared.agent_profile_snapshot:
            await self.add_event(
                run.id,
                "agent_profile.frozen",
                {"profile": safe_agent_profile_manifest(prepared.agent_profile_snapshot)},
            )
        if commit:
            await self.session.flush()
        else:
            await self.session.flush()

    async def freeze_agent_profile_snapshot(
        self,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> RunRecord:
        run = await self.require_run(run_id)
        current = run.agent_profile_snapshot or {}
        if current:
            if current != snapshot:
                raise ValueError("Agent Profile snapshot is immutable")
            return run
        run.agent_profile_snapshot = deepcopy(snapshot)
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "agent_profile.frozen",
            {"profile": safe_agent_profile_manifest(snapshot)},
        )
        await self.session.flush()
        return run

    async def initialize_reasoning_state(
        self,
        run_id: str,
        *,
        task_contract: dict[str, Any],
        plan_graph: dict[str, Any],
        agent_state: dict[str, Any],
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.state_version:
            raise ValueError("Reasoning state is already initialized")
        run.task_contract = task_contract
        run.plan_graph = plan_graph
        run.agent_state = agent_state
        run.state_version = int(agent_state.get("version", 1))
        run.updated_at = utc_now()
        root_execution = await self.session.scalar(
            select(AgentExecutionRecord).where(
                AgentExecutionRecord.run_id == run_id,
                AgentExecutionRecord.root_slot == "root",
            )
        )
        if root_execution is not None:
            root_execution.contract = deepcopy(task_contract)
            root_execution.checkpoint = deepcopy(agent_state)
            root_execution.budget_usage = deepcopy(agent_state.get("budget_usage") or {})
            root_execution.status = "running"
            root_execution.phase = "planning"
            root_execution.state_version += 1
            root_execution.heartbeat_at = utc_now()
            root_execution.updated_at = utc_now()
        await self.add_event(
            run_id,
            "reasoning.state_initialized",
            {"state_version": run.state_version, "plan_version": plan_graph.get("version", 1)},
        )
        await self.session.flush()
        return run

    async def update_reasoning_state(
        self,
        run_id: str,
        *,
        expected_version: int,
        agent_state: dict[str, Any],
        plan_graph: dict[str, Any] | None = None,
        terminal_reason: dict[str, Any] | None = None,
        waiting_state: dict[str, Any] | None = None,
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.state_version != expected_version:
            raise ValueError(
                f"State version conflict: expected {expected_version}, got {run.state_version}"
            )
        next_version = int(agent_state.get("version", expected_version + 1))
        if next_version <= expected_version:
            raise ValueError("State version must increase")
        run.agent_state = agent_state
        run.state_version = next_version
        if plan_graph is not None:
            run.plan_graph = plan_graph
        if terminal_reason is not None:
            run.terminal_reason = terminal_reason
        run.waiting_state = waiting_state
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "reasoning.state_updated",
            {"previous_version": expected_version, "state_version": next_version},
        )
        await self.session.flush()
        return run

    async def set_waiting_state(self, run_id: str, waiting_state: dict[str, Any]) -> RunRecord:
        run = await self.require_run(run_id)
        waiting_state = {
            **waiting_state,
            "continuation_token": waiting_state.get("continuation_token") or str(uuid.uuid4()),
        }
        run.waiting_state = waiting_state
        run.status = "waiting_user"
        run.updated_at = utc_now()
        await self.add_event(run_id, "run.waiting_user", waiting_state)
        await self.session.flush()
        return run

    async def resume_waiting_run(
        self, run_id: str, observation: dict[str, Any], *, continuation_token: str | None = None
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status != "waiting_user" or not run.waiting_state:
            raise ValueError("Run is not waiting for user input")
        expected_token = run.waiting_state.get("continuation_token")
        if expected_token and continuation_token != expected_token:
            raise ValueError("Invalid continuation token")
        state = dict(run.agent_state or {})
        observations = list(state.get("observations", []))
        observations.append(observation)
        state["observations"] = observations
        state["version"] = int(state.get("version", run.state_version)) + 1
        contract = dict(state.get("task_contract", run.task_contract or {}))
        contract["ambiguity_status"] = "clear"
        contract["clarification_question"] = None
        state["task_contract"] = contract
        run.task_contract = contract
        run.agent_state = state
        run.state_version = state["version"]
        run.waiting_state = None
        run.status = "executing"
        run.completed_at = None
        run.updated_at = utc_now()
        await self.add_event(
            run_id, "run.resumed", {"observation": observation, "state_version": run.state_version}
        )
        await self.session.flush()
        return run
