"""Narrow persistence ports owned by Run management.

The protocols describe business capabilities instead of exposing one repository
object whose public surface grows with every Run-related table.
"""

from __future__ import annotations

from typing import Protocol

from app.contracts.json_values import JsonObject, JsonValue
from app.db.models.executions import NodeExecutionRecord
from app.db.models.permissions import ApprovalGrantRecord, ApprovalRequestRecord, ToolCallRecord
from app.db.models.runs import AgentTurnRecord, RunEventRecord, RunRecord, StepRecord
from app.db.models.workspaces import ArtifactRecord, SandboxJobRecord
from app.repositories.approval_contracts import ApprovalRequestCreate


class RunReader(Protocol):
    async def get_run(self, run_id: str) -> RunRecord | None: ...

    async def get_run_initial(self, run_id: str) -> tuple[RunRecord | None, bool]: ...

    async def require_run(self, run_id: str) -> RunRecord: ...

    async def require_run_core(self, run_id: str) -> RunRecord: ...

    async def get_run_status(self, run_id: str) -> str | None: ...

    async def list_recent_runs(self, limit: int = 100) -> list[RunRecord]: ...


class RunLifecyclePort(RunReader, Protocol):
    async def create_task_run(
        self,
        description: str,
        model_policy: JsonObject,
        **options: JsonValue,
    ) -> RunRecord: ...

    async def freeze_agent_profile_snapshot(
        self,
        run_id: str,
        profile_snapshot: JsonObject,
    ) -> RunRecord: ...

    async def initialize_reasoning_state(
        self,
        run_id: str,
        reasoning_policy: JsonObject,
        plan_graph: JsonObject,
        agent_state: JsonObject,
    ) -> RunRecord: ...

    async def update_reasoning_state(
        self,
        run_id: str,
        *,
        agent_state: JsonObject | None = None,
        plan_graph: JsonObject | None = None,
        state_version: int | None = None,
    ) -> RunRecord: ...

    async def set_waiting_state(
        self,
        run_id: str,
        waiting_state: JsonObject,
    ) -> RunRecord: ...

    async def resume_waiting_run(
        self,
        run_id: str,
        continuation_token: str,
        observation: JsonObject,
    ) -> RunRecord: ...

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        summary: str | None = None,
        result: JsonObject | None = None,
        loaded_run: RunRecord | None = None,
    ) -> None: ...

    async def cancel_run(self, run_id: str) -> RunRecord: ...


class TurnStepPort(Protocol):
    async def create_step(
        self,
        run_id: str,
        index: int,
        title: str,
        intent: str | None = None,
        depends_on: list[str] | None = None,
    ) -> StepRecord: ...

    async def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        evidence: JsonObject | None = None,
    ) -> StepRecord: ...

    async def create_agent_turn(
        self,
        run_id: str,
        turn_index: int,
        decision_type: str,
        reasoning_summary: str,
        **details: JsonValue,
    ) -> AgentTurnRecord: ...

    async def update_agent_turn(
        self,
        turn_id: str,
        **updates: JsonValue,
    ) -> AgentTurnRecord: ...


class NodeExecutionPort(Protocol):
    async def require(self, execution_id: str) -> NodeExecutionRecord: ...

    async def transition(
        self,
        execution_id: str,
        *,
        expected_version: int,
        phase: object | None = None,
        status: object | None = None,
        **updates: JsonValue,
    ) -> NodeExecutionRecord: ...


class ToolCallApprovalPort(Protocol):
    async def start_tool_call(
        self,
        run_id: str,
        step_id: str | None,
        tool_name: str,
        tool_version: str,
        tool_input: JsonObject,
        permission: str,
        side_effect_level: str,
        **lineage: JsonValue,
    ) -> ToolCallRecord: ...

    async def transition_tool_call(
        self,
        tool_call_id: str,
        status: str,
    ) -> ToolCallRecord: ...

    async def finish_tool_call(
        self,
        tool_call_id: str,
        status: str,
        output: JsonObject | None,
        error: JsonObject | None = None,
    ) -> ToolCallRecord: ...

    async def create_approval_request(
        self,
        command: ApprovalRequestCreate,
    ) -> ApprovalRequestRecord: ...

    async def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        decision: str,
        *,
        continuation_token: str,
        reviewer_identity: JsonObject | None = None,
        rejection_guidance: str | None = None,
    ) -> tuple[ApprovalRequestRecord, ToolCallRecord]: ...

    async def consume_approval_grants(
        self,
        grant_ids: list[str] | tuple[str, ...],
    ) -> list[ApprovalGrantRecord]: ...


class RunEventPort(Protocol):
    async def add_event(
        self,
        run_id: str,
        event_type: str,
        payload: JsonObject,
        *,
        flush: bool = True,
        agent_execution_id: str | None = None,
    ) -> RunEventRecord: ...

    async def list_events(
        self,
        run_id: str,
        after_id: int = 0,
    ) -> list[RunEventRecord]: ...

    async def list_events_with_status(
        self,
        run_id: str,
        after_id: int = 0,
    ) -> tuple[list[RunEventRecord], str | None]: ...


class ArtifactSandboxPort(Protocol):
    async def create_artifact(
        self,
        run_id: str,
        artifact_type: str,
        **attributes: JsonValue,
    ) -> ArtifactRecord: ...

    async def create_sandbox_job(
        self,
        run_id: str,
        *,
        tool_call_id: str | None,
        executor: str,
        runtime_profile: JsonObject,
        resource_limits: JsonObject,
        input_artifact_ids: list[str] | None = None,
    ) -> SandboxJobRecord: ...

    async def transition_sandbox_job(
        self,
        job_id: str,
        status: str,
        **updates: JsonValue,
    ) -> SandboxJobRecord: ...


class AgentRuntimeRepositoryPort(
    RunLifecyclePort,
    TurnStepPort,
    ToolCallApprovalPort,
    RunEventPort,
    ArtifactSandboxPort,
    Protocol,
):
    """Temporary composition used while runtime collaborators become narrower."""
