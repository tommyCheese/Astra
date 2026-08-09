import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, select

from app.application.agent_runtime.policies.reasoning import resolve_run_profile
from app.common.contracts.json_values import JsonObject
from app.common.schemas.agent.run_policy import (
    ReasoningPolicySnapshot,
    RequestedReasoningPolicy,
    RunExecutionProfile,
)
from app.common.schemas.agent.types import AnswerMode, PlanExecution
from app.domain.agent_profile import AgentProfile, load_agent_profile
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.db.models.runs import RunEventRecord, RunRecord
from app.infrastructure.model_clients.reasoning import normalize_model_thinking
from app.infrastructure.repositories.run_query_store import safe_agent_profile_manifest


def _empty_runtime_checkpoint() -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "snapshot_version": 0,
        "turn_index": 0,
        "messages": [],
        "recent_observations": [],
        "pending_action": None,
        "terminal_intent": None,
    }


@dataclass(frozen=True)
class PreparedRunConfiguration:
    answer_mode: str
    runtime_kind: str
    runtime_version: int
    execution_profile: JsonObject
    reasoning_policy: JsonObject
    model_policy: JsonObject
    agent_profile_snapshot: JsonObject


def prepare_run_configuration(
    *,
    model_policy: JsonObject,
    reasoning_policy: JsonObject | None,
    answer_mode: str,
    execution_profile: JsonObject | None,
    agent_profile_snapshot: JsonObject | None,
) -> PreparedRunConfiguration:
    profile = _execution_profile(reasoning_policy, answer_mode, execution_profile)
    frozen_reasoning = (
        ReasoningPolicySnapshot.model_validate(reasoning_policy) if reasoning_policy is not None else profile.reasoning_policy
    )
    snapshot = agent_profile_snapshot or load_agent_profile().snapshot()
    AgentProfile.from_snapshot(snapshot)
    return PreparedRunConfiguration(
        answer_mode=profile.answer_mode.value,
        runtime_kind=profile.runtime_kind.value,
        runtime_version=profile.runtime_version,
        execution_profile=profile.model_dump(mode="json"),
        reasoning_policy=frozen_reasoning.model_dump(mode="json"),
        model_policy=_model_policy(model_policy),
        agent_profile_snapshot=deepcopy(snapshot),
    )


def _execution_profile(
    reasoning_policy: JsonObject | None,
    answer_mode: str,
    execution_profile: JsonObject | None,
) -> RunExecutionProfile:
    if execution_profile is not None:
        return RunExecutionProfile.model_validate(execution_profile)
    resolved_mode = AnswerMode.trusted if reasoning_policy is not None else AnswerMode(answer_mode)
    profile = resolve_run_profile(
        resolved_mode,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto if resolved_mode == AnswerMode.trusted else None,
    )
    if reasoning_policy is None:
        return profile
    return profile.model_copy(update={"reasoning_policy": ReasoningPolicySnapshot.model_validate(reasoning_policy)})


def _model_policy(model_policy: JsonObject) -> JsonObject:
    if isinstance(model_policy.get("thinking"), dict):
        return deepcopy(model_policy)
    thinking = normalize_model_thinking(
        provider=str(model_policy.get("provider") or "mock"),
        model=str(model_policy.get("model") or "mock"),
        selection=None,
    )
    return {**model_policy, "thinking": thinking.model_dump(mode="json")}


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
        await self._record_created_run(run, goal, prepared)
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
            runtime_kind=prepared.runtime_kind,
            runtime_version=prepared.runtime_version,
            fast_runtime_snapshot=(_empty_runtime_checkpoint() if prepared.runtime_kind == "fast-v1" else {}),
            fast_state_version=0,
            execution_profile=deepcopy(prepared.execution_profile),
            model_policy=policy,
            agent_profile_snapshot=deepcopy(prepared.agent_profile_snapshot),
            reasoning_policy=prepared.reasoning_policy,
            task_adapter="web",
            created_at=now,
            updated_at=now,
        )

    async def update_runtime_checkpoint(
        self,
        run_id: str,
        *,
        expected_version: int,
        checkpoint: dict[str, Any],
    ) -> RunRecord:
        run = await self.require_run_core(run_id)
        if run.runtime_kind != "fast-v1":
            raise ValueError("Fast Runtime snapshot is only valid for fast-v1 runs")
        if run.fast_state_version != expected_version:
            raise ValueError(f"Fast state version conflict: expected {expected_version}, got {run.fast_state_version}")
        snapshot_version = int(checkpoint.get("snapshot_version", -1))
        if snapshot_version != expected_version + 1:
            raise ValueError("Runtime checkpoint version must increase by one")
        run.fast_runtime_snapshot = deepcopy(checkpoint)
        run.fast_state_version = snapshot_version
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "fast.snapshot.updated",
            {
                "previous_version": expected_version,
                "snapshot_version": snapshot_version,
                "turn_index": int(checkpoint.get("turn_index", 0)),
                "pending_action_kind": ((checkpoint.get("pending_action") or {}).get("kind")),
                "terminal_intent": checkpoint.get("terminal_intent"),
            },
        )
        await self.session.flush()
        return run

    @staticmethod
    def _root_execution(
        run: RunRecord,
        task: TaskRecord,
        prepared: PreparedRunConfiguration,
        now,
    ) -> AgentExecutionRecord:
        budgets = ((prepared.reasoning_policy.get("effective") or {}).get("subagents") or {}).get("budgets") or {}
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
    ) -> None:
        await self.add_event(run.id, "run.created", {"goal": goal, "status": run.status})
        if prepared.agent_profile_snapshot:
            await self.add_event(
                run.id,
                "agent_profile.frozen",
                {"profile": safe_agent_profile_manifest(prepared.agent_profile_snapshot)},
            )
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
            raise ValueError(f"State version conflict: expected {expected_version}, got {run.state_version}")
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
        await self.add_event(run_id, "run.resumed", {"observation": observation, "state_version": run.state_version})
        await self.session.flush()
        return run

    async def add_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        flush: bool = True,
        agent_execution_id: str | None = None,
    ) -> RunEventRecord:
        event_payload = deepcopy(payload)
        if agent_execution_id is not None:
            execution = await self.session.get(AgentExecutionRecord, agent_execution_id)
            if execution is not None and execution.run_id == run_id:
                event_payload = {
                    **event_payload,
                    "agent_execution_id": execution.id,
                    "parent_execution_id": execution.parent_execution_id,
                    "agent_status": execution.status,
                    "agent_phase": execution.phase,
                    "agent_wait_reason": execution.wait_reason,
                    "agent_budget_usage": deepcopy(execution.budget_usage or {}),
                    "causation_id": next(
                        (
                            str(event_payload[key])
                            for key in (
                                "tool_call_id",
                                "node_execution_id",
                                "approval_id",
                                "request_id",
                            )
                            if event_payload.get(key) is not None
                        ),
                        None,
                    ),
                }
        event = RunEventRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            type=event_type,
            payload=event_payload,
        )
        self.session.add(event)
        if flush:
            await self.session.flush()
        return event

    async def list_events(self, run_id: str, after_id: int = 0) -> list[RunEventRecord]:
        result = await self.session.execute(
            select(RunEventRecord)
            .where(RunEventRecord.run_id == run_id, RunEventRecord.id > after_id)
            .order_by(RunEventRecord.id)
        )
        return list(result.scalars().all())

    async def event_cursor_counts(self, run_id: str, through_id: int = 0) -> tuple[int, dict[str, int]]:
        conditions = [RunEventRecord.run_id == run_id]
        if through_id > 0:
            conditions.append(RunEventRecord.id <= through_id)
        run_sequence = int(await self.session.scalar(select(func.count(RunEventRecord.id)).where(*conditions)) or 0)
        rows = (
            await self.session.execute(
                select(RunEventRecord.agent_execution_id, func.count(RunEventRecord.id))
                .where(*conditions, RunEventRecord.agent_execution_id.is_not(None))
                .group_by(RunEventRecord.agent_execution_id)
            )
        ).all()
        return run_sequence, {str(agent_id): int(count) for agent_id, count in rows}

    async def list_events_with_status(self, run_id: str, after_id: int = 0) -> tuple[list[RunEventRecord], str | None]:
        result = await self.session.execute(
            select(RunRecord.status, RunEventRecord)
            .outerjoin(
                RunEventRecord,
                and_(
                    RunEventRecord.run_id == RunRecord.id,
                    RunEventRecord.id > after_id,
                ),
            )
            .where(RunRecord.id == run_id)
            .order_by(RunEventRecord.id)
        )
        rows = result.all()
        if not rows:
            return [], None
        return [event for _, event in rows if event is not None], rows[0][0]
