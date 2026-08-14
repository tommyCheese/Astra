"""Audited tool and delegated-subagent invocation lifecycle."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.application.agent_runtime.services.tooling.authorization import ToolActionInput
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.permissions.effects import workspace_mount_mode
from app.application.skills.activation import SkillActivationService
from app.application.workspaces.artifacts import ArtifactService
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.schemas.permissions import ActionEffectPlan
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


@dataclass
class ToolInvocationStage:
    """Own the full side-effect boundary from execution context to committed result."""

    _run_repository: RunUnitOfWork
    _permission_repository: PermissionRepository
    _workspace_repository: WorkspaceRepository
    _workspace_service: WorkspaceRuntimeService
    _artifact_service: ArtifactService
    _sandbox_service: SandboxJobService
    _skill_activation_service: SkillActivationService
    _plugin_runtime: PluginRuntimeState
    _memory_service: Any

    async def execute(
        self,
        action: ToolActionInput,
        *,
        tool_call: ToolCallRecord,
        step_id: str | None,
        tool: AstraTool,
        effect_plan: ActionEffectPlan,
        runtime_identity_id: str,
    ) -> tuple[dict[str, Any], Path | None, bool]:
        tool_call_id = tool_call.id
        task_id = action.run.task_id
        active_plan_node_id = action.active_node.id if action.active_node else None
        # Authorization and the prepared tool-call record form the durable
        # boundary before any filesystem or external tool work begins.
        await self._run_repository.commit()
        mount_mode = workspace_mount_mode(effect_plan)
        workspace_path = Path(action.workspace_path) if action.workspace_path else None
        if mount_mode != "none" and workspace_path is None:
            workspace_path = await self._workspace_service.prepare(task_id)
        execution_context = self._execution_context(
            action,
            tool_call_id,
            task_id,
            step_id,
            effect_plan,
            runtime_identity_id,
            workspace_path,
            mount_mode,
        )
        await self._record_skill_attribution(action, tool_call_id, active_plan_node_id, execution_context)
        await self._run_repository.commit()
        capture_in_process_write = (
            tool.spec.execution_backend == "in_process" and mount_mode == "read_write" and workspace_path is not None
        )
        before_manifest = self._workspace_service.scan(workspace_path) if capture_in_process_write else None
        before_protected_paths = self._workspace_service.protected_paths(workspace_path) if capture_in_process_write else None
        guard = self._workspace_service.access_guard(workspace_path, mount_mode) if capture_in_process_write else nullcontext()
        try:
            async with guard:
                raw_output = await self._plugin_runtime.execute(
                    tool,
                    action.decision.tool_input,
                    context=execution_context,
                )
                if before_manifest is not None and workspace_path is not None:
                    await self._workspace_service.capture_changes(
                        run_id=action.run_id,
                        tool_call_id=tool_call_id,
                        workspace_dir=workspace_path,
                        before=before_manifest,
                        before_protected_paths=before_protected_paths,
                    )
            tool_output = self._plugin_runtime.adapt_and_validate(
                tool.spec,
                raw_output,
            ).model_dump(mode="json", exclude_none=True)
        except ToolExecutionError as error:
            await self._run_repository.finish_tool_call(
                tool_call_id,
                error=error.to_payload(),
            )
            raise
        tool_output, workspace_changed = await self._attach_workspace_changes(
            tool_call_id,
            tool_output,
        )
        await self._run_repository.finish_tool_call(
            tool_call_id,
            output=self._plugin_runtime.persistence_payload(
                tool.spec,
                tool_output,
            ),
        )
        await self._record_data_flow(action, effect_plan, task_id)
        return tool_output, workspace_path, workspace_changed

    def _execution_context(
        self,
        action: ToolActionInput,
        tool_call_id: str,
        task_id: str,
        step_id: str | None,
        effect_plan: ActionEffectPlan,
        runtime_identity_id: str,
        workspace_path: Path | None,
        mount_mode: str,
    ) -> ToolExecutionContext:
        supervisor = action.subagent_supervisor
        return ToolExecutionContext(
            run_id=action.run_id,
            tool_call_id=tool_call_id,
            step_id=step_id,
            trace_id=f"{action.run_id}:{tool_call_id}",
            artifact_service=self._artifact_service,
            sandbox_service=self._sandbox_service,
            task_id=task_id,
            workspace_path=workspace_path,
            workspace_mode=mount_mode,
            effect_plan=effect_plan.model_dump(mode="json"),
            runtime_identity_id=runtime_identity_id,
            skill_bindings=tuple(action.model_context.get("active_skills", [])),
            skill_draft_test=bool(action.model_context.get("skill_draft_test")),
            skill_input_provider=self._skill_activation_service,
            agent_execution_id=supervisor.parent_execution_id if supervisor else None,
            delegation_context=supervisor,
            memory_service=self._memory_service,
        )

    async def _record_skill_attribution(
        self,
        action: ToolActionInput,
        tool_call_id: str,
        active_plan_node_id: str | None,
        execution_context: ToolExecutionContext,
    ) -> None:
        if not execution_context.skill_bindings:
            return
        await self._run_repository.add_event(
            action.run_id,
            "skill.attributed_action",
            {
                "tool_call_id": tool_call_id,
                "plan_node_id": active_plan_node_id,
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

    async def _record_data_flow(
        self,
        action: ToolActionInput,
        effect_plan: ActionEffectPlan,
        task_id: str,
    ) -> None:
        observed_effects = {effect.kind.value for effect in effect_plan.effects}
        if not observed_effects & {
            "workspace_read",
            "network_read",
            "sensitive_data_read",
        }:
            return
        current = await self._permission_repository.get_data_flow_state(action.run_id)
        trust_sources, data_labels = self._updated_data_flow(
            action,
            effect_plan,
            observed_effects,
            task_id,
            list(current.trust_sources if current else []),
            list(current.data_labels if current else []),
        )
        await self._permission_repository.update_data_flow_state(
            action.run_id,
            expected_version=current.state_version if current else 0,
            trust_sources=trust_sources,
            data_labels=data_labels,
            allowed_destinations=current.allowed_destinations if current else [],
            prohibited_destinations=current.prohibited_destinations if current else [],
        )

    @staticmethod
    def _updated_data_flow(
        action: ToolActionInput,
        effect_plan: ActionEffectPlan,
        observed_effects: set[str],
        task_id: str,
        trust_sources: list[str],
        data_labels: list[str],
    ) -> tuple[list[str], list[str]]:
        if "workspace_read" in observed_effects:
            trust_sources.append(f"workspace:{task_id}")
            data_labels.append("untrusted")
        if "network_read" in observed_effects:
            trust_sources.append("web:public")
            data_labels.append("untrusted")
        for effect in effect_plan.effects:
            data_labels.extend(effect.data_labels)
        if "sensitive_data_read" in observed_effects:
            data_labels.append("sensitive")
        return (
            list(dict.fromkeys(trust_sources)),
            list(dict.fromkeys(data_labels)),
        )
