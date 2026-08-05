"""Audited tool and delegated-subagent invocation lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.permissions.effects import workspace_mount_mode
from app.application.skills.activation import SkillActivationService
from app.application.workspaces.artifacts import ArtifactService
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.schemas.permissions import ActionEffectPlan
from app.domain.execution.contracts import SubagentSupervisorPort
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.workspaces import WorkspaceRepository
from app.infrastructure.sandbox.runtime import SandboxJobService
from app.infrastructure.tools.base import (
    AstraTool,
    ToolExecutionContext,
    ToolExecutionError,
)


@dataclass(frozen=True)
class InvocationStageInput:
    run_id: str
    task_id: str
    tool_call: ToolCallRecord
    step_id: str | None
    plan_node_id: str | None
    tool: AstraTool
    tool_input: dict[str, Any]
    effect_plan: ActionEffectPlan
    runtime_identity_id: str
    active_skills: tuple[dict[str, Any], ...]
    is_skill_draft_test: bool
    workspace_path: Path | None
    subagent_supervisor: SubagentSupervisorPort | None


@dataclass(frozen=True)
class InvocationStageResult:
    tool_output: dict[str, Any]
    workspace_path: Path | None
    workspace_changed: bool


class ToolInvocationStage:
    """Own the full side-effect boundary from execution context to committed result."""

    def __init__(
        self,
        run_repository: RunUnitOfWork,
        permission_repository: PermissionRepository,
        workspace_repository: WorkspaceRepository,
        workspace_service: WorkspaceRuntimeService,
        artifact_service: ArtifactService,
        sandbox_service: SandboxJobService,
        skill_activation_service: SkillActivationService,
    ) -> None:
        self._run_repository = run_repository
        self._permission_repository = permission_repository
        self._workspace_repository = workspace_repository
        self._workspace_service = workspace_service
        self._artifact_service = artifact_service
        self._sandbox_service = sandbox_service
        self._skill_activation_service = skill_activation_service

    async def execute(self, stage_input: InvocationStageInput) -> InvocationStageResult:
        # Authorization and the prepared tool-call record form the durable
        # boundary before any filesystem or external tool work begins.
        await self._run_repository.commit()
        mount_mode = workspace_mount_mode(stage_input.effect_plan)
        workspace_path = stage_input.workspace_path
        if mount_mode != "none" and workspace_path is None:
            workspace_path = await self._workspace_service.prepare(stage_input.task_id)
        execution_context = self._execution_context(stage_input, workspace_path, mount_mode)
        await self._record_skill_attribution(stage_input, execution_context)
        await self._run_repository.commit()
        try:
            tool_output = await stage_input.tool.run(
                stage_input.tool_input,
                context=execution_context,
            )
        except ToolExecutionError as error:
            await self._run_repository.finish_tool_call(
                stage_input.tool_call.id,
                error=error.to_payload(),
            )
            raise
        tool_output, workspace_changed = await self._attach_workspace_changes(
            stage_input.tool_call.id,
            tool_output,
        )
        await self._run_repository.finish_tool_call(
            stage_input.tool_call.id,
            output=tool_output,
        )
        await self._record_data_flow(stage_input)
        return InvocationStageResult(tool_output, workspace_path, workspace_changed)

    def _execution_context(
        self,
        stage_input: InvocationStageInput,
        workspace_path: Path | None,
        mount_mode: str,
    ) -> ToolExecutionContext:
        supervisor = stage_input.subagent_supervisor
        return ToolExecutionContext(
            run_id=stage_input.run_id,
            tool_call_id=stage_input.tool_call.id,
            step_id=stage_input.step_id,
            trace_id=f"{stage_input.run_id}:{stage_input.tool_call.id}",
            artifact_service=self._artifact_service,
            sandbox_service=self._sandbox_service,
            task_id=stage_input.task_id,
            workspace_path=workspace_path,
            workspace_mode=mount_mode,
            effect_plan=stage_input.effect_plan.model_dump(mode="json"),
            runtime_identity_id=stage_input.runtime_identity_id,
            skill_bindings=stage_input.active_skills,
            skill_draft_test=stage_input.is_skill_draft_test,
            skill_input_provider=self._skill_activation_service,
            agent_execution_id=supervisor.parent_execution_id if supervisor else None,
            delegation_context=supervisor,
        )

    async def _record_skill_attribution(
        self,
        stage_input: InvocationStageInput,
        execution_context: ToolExecutionContext,
    ) -> None:
        if not execution_context.skill_bindings:
            return
        await self._run_repository.add_event(
            stage_input.run_id,
            "skill.attributed_action",
            {
                "tool_call_id": stage_input.tool_call.id,
                "plan_node_id": stage_input.plan_node_id,
                "skills": list(execution_context.skill_bindings),
                "effect_plan": execution_context.effect_plan,
            },
        )

    async def _attach_workspace_changes(
        self,
        tool_call_id: str,
        tool_output: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        changes = await self._workspace_repository.list_changes_for_tool_call(tool_call_id)
        if not changes:
            return tool_output, False
        return {
            **tool_output,
            "data": {
                **dict(tool_output.get("data") or {}),
                "workspace_changes": [
                    {
                        "kind": change.change_kind,
                        "path": change.relative_path,
                        "size_bytes": change.size_bytes,
                        "mime_type": change.mime_type,
                    }
                    for change in changes
                ],
            },
        }, True

    async def _record_data_flow(self, stage_input: InvocationStageInput) -> None:
        observed_effects = {effect.kind.value for effect in stage_input.effect_plan.effects}
        if not observed_effects & {
            "workspace_read",
            "network_read",
            "sensitive_data_read",
        }:
            return
        current = await self._permission_repository.get_data_flow_state(stage_input.run_id)
        trust_sources, data_labels = self._updated_data_flow(
            stage_input,
            observed_effects,
            list(current.trust_sources if current else []),
            list(current.data_labels if current else []),
        )
        await self._permission_repository.update_data_flow_state(
            stage_input.run_id,
            expected_version=current.state_version if current else 0,
            trust_sources=trust_sources,
            data_labels=data_labels,
            allowed_destinations=current.allowed_destinations if current else [],
            prohibited_destinations=current.prohibited_destinations if current else [],
        )

    @staticmethod
    def _updated_data_flow(
        stage_input: InvocationStageInput,
        observed_effects: set[str],
        trust_sources: list[str],
        data_labels: list[str],
    ) -> tuple[list[str], list[str]]:
        if "workspace_read" in observed_effects:
            trust_sources.append(f"workspace:{stage_input.task_id}")
            data_labels.append("untrusted")
        if "network_read" in observed_effects:
            trust_sources.append("web:public")
            data_labels.append("untrusted")
        for effect in stage_input.effect_plan.effects:
            data_labels.extend(effect.data_labels)
        if "sensitive_data_read" in observed_effects:
            data_labels.append("sensitive")
        return (
            list(dict.fromkeys(trust_sources)),
            list(dict.fromkeys(data_labels)),
        )
