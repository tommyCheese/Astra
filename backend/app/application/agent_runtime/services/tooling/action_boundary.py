"""Mandatory authorization-to-observation boundary shared by Runtime profiles."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import ClassVar

from app.application.agent_runtime.contracts import (
    ActionProvider,
    LoopAction,
    LoopObservation,
    LoopState,
    PortIdentity,
    SafetyInvariant,
)
from app.application.agent_runtime.services.tooling.approval import (
    ApprovalRoutingStage,
    ApprovalStageInput,
)
from app.application.agent_runtime.services.tooling.authorization import (
    PermissionAuthorizationStage,
    ToolActionInput,
)
from app.application.agent_runtime.services.tooling.invocation import ToolInvocationStage
from app.application.agent_runtime.services.tooling.plugin_runtime import (
    PluginRuntimeState,
)
from app.application.memory.tool_service import MemoryToolService
from app.application.skills.activation import SkillActivationService
from app.application.workspaces.artifacts import ArtifactService, LocalArtifactStore
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.workspaces import WorkspaceRepository
from app.infrastructure.sandbox.docker_provider import build_sandbox_provider
from app.infrastructure.sandbox.runtime import SandboxJobService, SandboxSupervisor
from app.infrastructure.tools.base import ToolExecutionError
from app.infrastructure.tools.router import ToolRouter

PreparedActionCallback = Callable[[ToolCallRecord, bool, bool], Awaitable[None]]


@dataclass
class ActionBoundary:
    settings: AstraRuntimeSettings
    repository: RunUnitOfWork
    router: ToolRouter
    event_namespace: str
    approved_tool_call: ToolCallRecord | None = None
    on_prepared: PreparedActionCallback | None = None
    plugins: PluginRuntimeState = field(init=False)
    identity: ClassVar[PortIdentity] = PortIdentity(
        name="shared-action-boundary",
        version=1,
        digest="a" * 64,
        safety_coverage=frozenset(
            {
                SafetyInvariant.schema_validation,
                SafetyInvariant.effect_analysis,
                SafetyInvariant.authorization,
                SafetyInvariant.approval_integrity,
            }
        ),
    )

    def __post_init__(self) -> None:
        self.plugins = PluginRuntimeState.from_registry(self.router.registry)

    async def execute(
        self,
        state: LoopState,
        action: LoopAction,
        _providers: tuple[ActionProvider, ...],
    ) -> LoopObservation:
        turn = None
        try:
            run = await self.repository.require_run_core(state.run_id)
            profile = RunExecutionProfile.model_validate(run.execution_profile or {})
            decision = _decision(action)
            turn = await self.repository.create_agent_turn(
                state.run_id,
                state.turn_index,
                "call_tool",
                decision.reasoning_summary,
                selected_tool=action.name,
                decision=decision.model_dump(mode="json"),
                phase="authorizing",
            )
            action_context, authorization = await self._authorization(state, run, turn, decision, profile)
            return await self._execute_authorized(state, action, turn, decision, action_context, authorization)
        except ToolExecutionError as error:
            return await self._failed(state.run_id, turn, action, error)

    async def _execute_authorized(
        self,
        state: LoopState,
        action: LoopAction,
        turn,
        decision: AgentDecision,
        action_context: ToolActionInput,
        authorization,
    ) -> LoopObservation:
        tool, _, runtime_identity, effect_plan, effect_hash, permission = authorization
        tool_call, waiting_summary = await ApprovalRoutingStage(self.settings, self.repository, self.plugins).execute(
            ApprovalStageInput(
                run_id=state.run_id,
                turn=turn,
                decision=decision,
                tool=tool,
                effect_plan=effect_plan,
                effect_plan_hash=effect_hash,
                authorization=permission,
                step=None,
                active_node_execution_id=None,
                has_canonical_plan=False,
                is_approved_resume=self.approved_tool_call is not None,
                approved_tool_call=self.approved_tool_call,
            )
        )
        if waiting_summary is not None:
            return await self._waiting(state.run_id, tool_call, tool.spec.idempotent, waiting_summary)
        assert tool_call is not None
        await self._notify_prepared(tool_call, tool.spec.idempotent, False)
        output, _, _ = await self._invocation().execute(
            action_context,
            tool_call=tool_call,
            step_id=None,
            tool=tool,
            effect_plan=effect_plan,
            runtime_identity_id=runtime_identity.id,
        )
        return await self._succeeded(state.run_id, turn.id, tool_call, output)

    async def _authorization(self, state, run, turn, decision, profile):
        permissions = PermissionRepository(self.repository.session)
        identity = await permissions.get_or_create_identity(
            identity_type="main_agent",
            principal="astra.agent",
            task_id=run.task_id,
            run_id=run.id,
            trust_level="platform",
            attributes={"runtime": run.runtime_kind},
        )
        action_context = ToolActionInput(
            run=run,
            run_id=run.id,
            goal="",
            turn_index=state.turn_index,
            turn=turn,
            decision=decision,
            main_identity=identity,
            active_node=None,
            active_node_execution_id=None,
            model_context={},
            execution_mode=profile.reasoning_policy.effective.execution_mode,
            is_approved_resume=self.approved_tool_call is not None,
            approved_request_snapshot=_approval_snapshot(self.approved_tool_call),
            approved_tool_call=self.approved_tool_call,
            workspace_path=None,
            subagent_supervisor=None,
        )
        authorization = await PermissionAuthorizationStage(
            self.settings,
            self.repository,
            permissions,
            self.router,
            self.plugins,
        ).execute(
            action_context,
            tool_call_count=len(run.fast_runtime_snapshot.get("recent_observations", [])),
        )
        return action_context, authorization

    async def _waiting(
        self,
        run_id: str,
        tool_call: ToolCallRecord | None,
        idempotent: bool,
        summary: str,
    ) -> LoopObservation:
        assert tool_call is not None
        await self._notify_prepared(tool_call, idempotent, True)
        await self.repository.add_event(
            run_id,
            f"{self.event_namespace}.approval.waiting",
            {"tool_call_id": tool_call.id, "tool_name": tool_call.tool_name},
        )
        await self.repository.session.commit()
        return LoopObservation(
            kind="system",
            status="waiting",
            summary=summary,
            data={
                "category": "approval_required",
                "tool_name": tool_call.tool_name,
                "tool_call_id": tool_call.id,
            },
        )

    async def _succeeded(
        self,
        run_id: str,
        turn_id: str,
        tool_call: ToolCallRecord,
        output: dict,
    ) -> LoopObservation:
        await self.repository.update_agent_turn(
            turn_id,
            status="completed",
            phase="observed",
            tool_call_id=tool_call.id,
            observation={"kind": "tool_result", "status": output["status"]},
        )
        await self.repository.add_event(
            run_id,
            f"{self.event_namespace}.tool.completed",
            {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.tool_name,
                "status": output["status"],
            },
        )
        await self.repository.session.commit()
        return LoopObservation(
            kind="tool_result",
            status="succeeded" if output["status"] == "succeeded" else "failed",
            summary=f"Tool {tool_call.tool_name} returned {output['status']}.",
            data={
                **output.get("data", {}),
                "tool_name": tool_call.tool_name,
                "tool_call_id": tool_call.id,
                "artifacts": output.get("artifacts", []),
            },
        )

    async def _failed(
        self,
        run_id: str,
        turn,
        action: LoopAction,
        error: ToolExecutionError,
    ) -> LoopObservation:
        if turn is not None:
            await self.repository.update_agent_turn(
                turn.id,
                status="failed",
                phase="observed",
                observation={
                    "kind": "tool_error",
                    "status": "failed",
                    "error": error.to_payload(),
                },
            )
        await self.repository.add_event(
            run_id,
            f"{self.event_namespace}.tool.failed",
            {"tool_name": action.name, "category": error.category},
        )
        await self.repository.session.commit()
        return LoopObservation(
            kind="tool_error",
            status=("rejected" if error.category in {"permission_denied", "tool_not_allowed"} else "failed"),
            summary=f"Tool {action.name or 'unknown'} failed.",
            data={"category": error.category, "tool_name": action.name},
        )

    async def _notify_prepared(self, tool_call: ToolCallRecord, idempotent: bool, waiting: bool) -> None:
        if self.on_prepared is not None:
            await self.on_prepared(tool_call, idempotent, waiting)

    def _invocation(self) -> ToolInvocationStage:
        artifacts = ArtifactService(
            self.repository,
            LocalArtifactStore(self.settings.artifact_store_path),
            max_files=self.settings.artifact_max_files,
            max_bytes=self.settings.artifact_max_bytes,
        )
        workspaces = WorkspaceRepository(self.repository.session)
        workspace_runtime = WorkspaceRuntimeService(
            workspaces,
            self.settings.task_workspace_store_path,
            max_files=self.settings.task_workspace_max_files,
            max_bytes=self.settings.task_workspace_max_bytes,
            max_file_bytes=self.settings.task_workspace_max_file_bytes,
            artifact_store_path=self.settings.artifact_store_path,
        )
        sandbox = SandboxJobService(
            self.repository,
            SandboxSupervisor(build_sandbox_provider(self.settings)),
            artifacts,
            workspace_runtime,
        )
        return ToolInvocationStage(
            self.repository,
            PermissionRepository(self.repository.session),
            workspaces,
            workspace_runtime,
            artifacts,
            sandbox,
            SkillActivationService(
                self.repository.session,
                max_active=self.settings.skills_max_active,
                max_resource_bytes=self.settings.skills_max_resource_bytes_per_run,
            ),
            self.plugins,
            MemoryToolService(self.repository, writes_enabled=False),
        )


def _decision(action: LoopAction) -> AgentDecision:
    return AgentDecision(
        decision_type="call_tool",
        reasoning_summary=action.reason or "Runtime selected a tool.",
        tool_name=action.name,
        tool_input=action.input,
    )


def _approval_snapshot(tool_call: ToolCallRecord | None):
    if tool_call is None or tool_call.approval_request is None:
        return None
    approval = tool_call.approval_request
    return {
        "effect_plan_hash": approval.effect_plan_hash,
        "frozen_effect_plan": dict(approval.frozen_effect_plan or {}),
        "analyzer_version": approval.analyzer_version,
        "analyzer_digest": approval.analyzer_digest,
        "catalog_digest": approval.catalog_digest,
    }
