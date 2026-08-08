from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.application.agent_runtime.services.tooling.approval import ApprovalRoutingStage, ApprovalStageInput
from app.application.agent_runtime.services.tooling.authorization import AuthorizationStageInput, PermissionAuthorizationStage
from app.application.agent_runtime.services.tooling.invocation import InvocationStageInput, ToolInvocationStage
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.memory.tool_service import MemoryToolService
from app.application.skills.activation import SkillActivationService
from app.application.workspaces.artifacts import ArtifactService, LocalArtifactStore
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.fast_runtime import FastAgentAction, FastObservation
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.workspaces import WorkspaceRepository
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.sandbox.docker_provider import build_sandbox_provider
from app.infrastructure.sandbox.runtime import SandboxJobService, SandboxSupervisor
from app.infrastructure.tools.base import ToolExecutionError
from app.infrastructure.tools.router import ToolRouter


@dataclass(frozen=True)
class FastToolStageResult:
    observation: FastObservation
    waiting_for_approval: bool = False


class FastToolStage:
    """Fast adapter over the platform-owned authorization and invocation boundary."""

    def __init__(self, settings: AstraRuntimeSettings, router: ToolRouter) -> None:
        self._settings = settings
        self._router = router
        self._plugins = PluginRuntimeState.from_registry(router.registry)

    async def execute(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        turn_index: int,
        action: FastAgentAction,
        *,
        approved_tool_call: ToolCallRecord | None = None,
        on_prepared: Callable[[ToolCallRecord, bool, bool], Awaitable[None]] | None = None,
    ) -> FastToolStageResult:
        turn = None
        try:
            run = await repo.require_run_core(run_id)
            profile = RunExecutionProfile.model_validate(run.execution_profile or {})
            decision = AgentDecision(
                decision_type="call_tool",
                reasoning_summary=action.reason or "Fast Runtime selected a tool.",
                tool_name=action.tool_name,
                tool_input=action.tool_input,
            )
            turn = await repo.create_agent_turn(
                run_id,
                turn_index,
                "call_tool",
                decision.reasoning_summary,
                selected_tool=action.tool_name,
                decision=decision.model_dump(mode="json"),
                phase="authorizing",
            )
            permissions = PermissionRepository(repo.session)
            main_identity = await permissions.get_or_create_identity(
                identity_type="main_agent",
                principal="astra.fast-agent",
                task_id=run.task_id,
                run_id=run.id,
                trust_level="platform",
                attributes={"runtime": "fast-v1"},
            )
            authorization = await PermissionAuthorizationStage(
                self._settings,
                repo,
                permissions,
                self._router,
                self._plugins,
            ).execute(
                AuthorizationStageInput(
                    run=run,
                    decision=decision,
                    main_identity=main_identity,
                    execution_mode=profile.reasoning_policy.effective.execution_mode,
                    tool_call_count=len((run.fast_runtime_snapshot or {}).get("recent_observations", [])),
                    is_approved_resume=approved_tool_call is not None,
                    approved_request_snapshot=self._approval_snapshot(approved_tool_call),
                    approved_tool_call_id=approved_tool_call.id if approved_tool_call else None,
                )
            )
            tool, _, runtime_identity, effect_plan, effect_hash, permission = authorization
            tool_call, waiting_summary = await ApprovalRoutingStage(
                self._settings,
                repo,
                self._plugins,
            ).execute(
                ApprovalStageInput(
                    run_id=run_id,
                    turn=turn,
                    decision=decision,
                    tool=tool,
                    effect_plan=effect_plan,
                    effect_plan_hash=effect_hash,
                    authorization=permission,
                    step=None,
                    active_node_execution_id=None,
                    has_canonical_plan=False,
                    is_approved_resume=approved_tool_call is not None,
                    approved_tool_call=approved_tool_call,
                )
            )
            if waiting_summary is not None:
                assert tool_call is not None
                if on_prepared is not None:
                    await on_prepared(tool_call, tool.spec.idempotent, True)
                await repo.add_event(
                    run_id,
                    "fast.approval.waiting",
                    {"tool_call_id": tool_call.id, "tool_name": tool.spec.name},
                )
                await repo.session.commit()
                return FastToolStageResult(
                    FastObservation(
                        kind="system",
                        status="denied",
                        summary=waiting_summary,
                        tool_name=tool.spec.name,
                        tool_call_id=tool_call.id,
                        data={"category": "approval_required"},
                    ),
                    waiting_for_approval=True,
                )
            assert tool_call is not None
            if on_prepared is not None:
                await on_prepared(tool_call, tool.spec.idempotent, False)
            invocation = await self._invocation(repo).execute(
                InvocationStageInput(
                    run_id=run_id,
                    task_id=run.task_id,
                    tool_call=tool_call,
                    step_id=None,
                    plan_node_id=None,
                    tool=tool,
                    tool_input=action.tool_input,
                    effect_plan=effect_plan,
                    runtime_identity_id=runtime_identity.id,
                    active_skills=(),
                    is_skill_draft_test=False,
                    workspace_path=None,
                    subagent_supervisor=None,
                )
            )
            output = invocation.tool_output
            await repo.update_agent_turn(
                turn.id,
                status="completed",
                phase="observed",
                tool_call_id=tool_call.id,
                observation={"kind": "tool_result", "status": output["status"]},
            )
            await repo.add_event(
                run_id,
                "fast.tool.completed",
                {"tool_call_id": tool_call.id, "tool_name": tool.spec.name, "status": output["status"]},
            )
            await repo.session.commit()
            return FastToolStageResult(
                FastObservation(
                    kind="tool_result",
                    status="succeeded" if output["status"] == "succeeded" else "failed",
                    summary=f"Tool {tool.spec.name} returned {output['status']}.",
                    tool_name=tool.spec.name,
                    tool_call_id=tool_call.id,
                    data=output.get("data", {}),
                    artifacts=output.get("artifacts", []),
                )
            )
        except ToolExecutionError as error:
            if turn is not None:
                await repo.update_agent_turn(
                    turn.id,
                    status="failed",
                    phase="observed",
                    observation={"kind": "tool_error", "status": "failed", "error": error.to_payload()},
                )
            await repo.add_event(
                run_id,
                "fast.tool.failed",
                {"tool_name": action.tool_name, "category": error.category},
            )
            await repo.session.commit()
            return FastToolStageResult(
                FastObservation(
                    kind="tool_error",
                    status="denied" if error.category in {"permission_denied", "tool_not_allowed"} else "failed",
                    summary=f"Tool {action.tool_name or 'unknown'} failed.",
                    tool_name=action.tool_name,
                    data={"category": error.category},
                )
            )

    def _invocation(self, repo: RunUnitOfWork) -> ToolInvocationStage:
        artifacts = ArtifactService(
            repo,
            LocalArtifactStore(self._settings.artifact_store_path),
            max_files=self._settings.artifact_max_files,
            max_bytes=self._settings.artifact_max_bytes,
        )
        workspaces = WorkspaceRepository(repo.session)
        workspace_runtime = WorkspaceRuntimeService(
            workspaces,
            self._settings.task_workspace_store_path,
            max_files=self._settings.task_workspace_max_files,
            max_bytes=self._settings.task_workspace_max_bytes,
            max_file_bytes=self._settings.task_workspace_max_file_bytes,
            artifact_store_path=self._settings.artifact_store_path,
        )
        sandbox = SandboxJobService(
            repo,
            SandboxSupervisor(build_sandbox_provider(self._settings)),
            artifacts,
            workspace_runtime,
        )
        return ToolInvocationStage(
            repo,
            PermissionRepository(repo.session),
            workspaces,
            workspace_runtime,
            artifacts,
            sandbox,
            SkillActivationService(
                repo.session,
                max_active=self._settings.skills_max_active,
                max_resource_bytes=self._settings.skills_max_resource_bytes_per_run,
            ),
            self._plugins,
            MemoryToolService(repo, writes_enabled=False),
        )

    @staticmethod
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
