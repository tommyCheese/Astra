"""Composition root for the root-agent runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReflectionGate,
)
from app.application.agent_runtime.services.execution.recovery import RunRecoveryStage
from app.application.agent_runtime.services.shared.progress import ExecutionProgress
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.application.memory.tool_service import MemoryToolService
from app.application.permissions.governance import ExtensionTrustPolicy
from app.application.subagents.eligibility import subagent_execution_eligibility
from app.application.subagents.supervisor import SubagentSupervisor
from app.application.workspaces.artifacts import ArtifactService, LocalArtifactStore
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import (
    EffectiveReasoningPolicy,
    ReasoningPolicySnapshot,
    RunExecutionProfile,
)
from app.common.schemas.agent.types import AnswerMode, ReasoningEffort
from app.common.schemas.permissions import ExtensionDescriptor
from app.infrastructure.db.models.permissions import AgentIdentityRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.model_clients.factory import build_model_client
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.tool_settings import (
    ToolSettingsRepository,
    default_tool_states,
)
from app.infrastructure.repositories.workspaces import WorkspaceRepository
from app.infrastructure.runtime.trusted_capabilities import (
    RuntimeBuildValues,
    RuntimeInfrastructure,
    TrustedCapabilityFactory,
)
from app.infrastructure.runtime.trusted_state import TrustedRuntime
from app.infrastructure.sandbox.docker_provider import build_sandbox_provider
from app.infrastructure.sandbox.runtime import SandboxJobService, SandboxSupervisor
from app.infrastructure.tools.base import AstraToolRegistry, ToolExecutionError
from app.infrastructure.tools.router import ToolRouter


@dataclass
class RootPermissionRuntime:
    """Lazily create the root identity and freeze the immutable tool catalog."""

    _repository: RunUnitOfWork
    _permissions: PermissionRepository
    _run: RunRecord
    _run_id: str
    _catalog: list[dict[str, Any]]
    _catalog_digest: str
    _behavioral_catalog: list[dict[str, Any]]
    _behavioral_digest: str
    _display_digest: str
    _identity: AgentIdentityRecord | None = field(default=None, init=False)

    async def ensure(self) -> AgentIdentityRecord:
        if self._identity is not None:
            return self._identity
        self._identity = await self._permissions.get_or_create_identity(
            identity_type="main_agent",
            principal="astra.agent",
            task_id=self._run.task_id,
            run_id=self._run_id,
            trust_level="platform",
            attributes={"permission_scope": self._unrestricted_scope()},
        )
        await self.freeze_catalog()
        return self._identity

    async def freeze_catalog(self) -> None:
        await self._permissions.freeze_tool_catalog(
            self._run_id,
            catalog=self._catalog,
            digest=self._catalog_digest,
            behavioral_catalog=self._behavioral_catalog,
            behavioral_digest=self._behavioral_digest,
            display_digest=self._display_digest,
        )

    @staticmethod
    def _unrestricted_scope() -> dict[str, list[str]]:
        return {
            "actions": ["*"],
            "resources": ["*"],
            "effect_kinds": ["*"],
            "tools": ["*"],
            "skills": ["*"],
            "credential_scopes": ["*"],
            "data_labels": ["*"],
            "allowed_purposes": ["*"],
            "network_destinations": ["*"],
            "workspace_read_roots": ["*"],
            "workspace_write_roots": ["*"],
        }


@dataclass
class TrustedRuntimeFactory:
    """Build named runtime collaborators without embedding execution policy."""

    _settings: AstraRuntimeSettings
    _model_client: ModelClient
    _tool_registry: AstraToolRegistry
    _tool_router: ToolRouter
    _plugin_runtime: PluginRuntimeState
    _evaluator: AgentObservationEvaluator
    _reflection_gate: AgentReflectionGate
    _completion_gate: AgentCompletionGate
    _sandbox_provider: Any
    _supervisor_close_tasks: set[asyncio.Task[Any]]
    _normalize_tool_output: Callable[[str, dict[str, Any]], dict[str, Any]]

    async def build(
        self,
        *,
        repository: RunUnitOfWork,
        run_id: str,
        goal: str,
        on_answer_delta: Callable[[str], Awaitable[None]] | None,
    ) -> TrustedRuntime:
        (run, active_plan), recovery = await self._load_run(repository, run_id)
        profile = RunExecutionProfile.model_validate(run.execution_profile)
        policy = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {}).effective
        permissions, permission_runtime = self._permission_runtime(repository, run, run_id)
        await permission_runtime.freeze_catalog()
        await permission_runtime.ensure()
        infrastructure = self._infrastructure(repository, permissions)
        supervisor = await self._subagent_supervisor(
            repository,
            run,
            run_id,
            policy,
            permission_runtime,
        )
        limits = self._limits(profile, policy)
        progress = self._progress(active_plan, run, list(run.tool_calls))
        approved_call, approved_turn, approved_snapshot, terminal = await recovery.recover(run_id, run, progress.observations)
        return self._composer().compose(
            RuntimeBuildValues(
                repository=repository,
                run_id=run_id,
                goal=goal,
                on_answer_delta=on_answer_delta,
                run=run,
                initial_turn_count=len(run.turns),
                profile=profile,
                policy=policy,
                limits=limits,
                progress=progress,
                approved_call=approved_call,
                approved_turn=approved_turn,
                approved_snapshot=approved_snapshot,
                terminal=terminal,
                permission_runtime=permission_runtime,
                infrastructure=infrastructure,
                supervisor=supervisor,
            )
        )

    def _composer(self) -> TrustedCapabilityFactory:
        return TrustedCapabilityFactory(
            self._settings,
            self._model_client,
            self._tool_registry,
            self._tool_router,
            self._plugin_runtime,
            self._evaluator,
            self._reflection_gate,
            self._completion_gate,
            self._normalize_tool_output,
        )

    async def _load_run(
        self,
        repository: RunUnitOfWork,
        run_id: str,
    ):
        recovery = RunRecoveryStage(
            repository,
            self._plugin_runtime,
            self._tool_registry,
            self._normalize_tool_output,
        )
        loaded = await recovery.load(run_id)
        return loaded, recovery

    def _permission_runtime(
        self,
        repository: RunUnitOfWork,
        run: RunRecord,
        run_id: str,
    ) -> tuple[PermissionRepository, RootPermissionRuntime]:
        catalog = [spec.model_dump(mode="json") for _, spec in sorted(self._tool_registry.specs().items())]
        self._validate_catalog(catalog)
        digest = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        permissions = PermissionRepository(repository.session)
        behavioral_catalog = self._plugin_runtime.snapshot_catalog(self._tool_registry)
        return permissions, RootPermissionRuntime(
            repository,
            permissions,
            run,
            run_id,
            catalog,
            digest,
            behavioral_catalog,
            self._plugin_runtime.behavioral_digest(self._tool_registry),
            self._plugin_runtime.display_digest(self._tool_registry),
        )

    def _validate_catalog(self, catalog: list[dict[str, Any]]) -> None:
        try:
            ExtensionTrustPolicy().inventory(
                [self._extension_descriptor(entry) for entry in catalog],
                allowed_providers=self._settings.trusted_tool_provider_map,
            )
        except ValueError as exc:
            raise ToolExecutionError("extension_trust_denied", str(exc)) from exc

    @staticmethod
    def _extension_descriptor(entry: dict[str, Any]) -> ExtensionDescriptor:
        schema_digest = hashlib.sha256(
            json.dumps(
                entry["input_schema"],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return ExtensionDescriptor(
            extension_type="tool",
            id=entry["name"],
            version=entry["version"],
            provider_id=entry["provider_id"],
            digest=entry["provider_digest"],
            trust_level=entry["trust_level"],
            schema_digest=schema_digest,
            annotations={"description": entry.get("description", "")},
        )

    def _infrastructure(
        self,
        repository: RunUnitOfWork,
        permissions: PermissionRepository,
    ) -> RuntimeInfrastructure:
        artifact_service = ArtifactService(
            repository,
            LocalArtifactStore(self._settings.artifact_store_path),
            max_files=self._settings.artifact_max_files,
            max_bytes=self._settings.artifact_max_bytes,
        )
        workspace_repository = WorkspaceRepository(repository.session)
        workspace_service = WorkspaceRuntimeService(
            workspace_repository,
            self._settings.task_workspace_store_path,
            max_files=self._settings.task_workspace_max_files,
            max_bytes=self._settings.task_workspace_max_bytes,
            max_file_bytes=self._settings.task_workspace_max_file_bytes,
            artifact_store_path=self._settings.artifact_store_path,
        )
        provider = self._sandbox_provider or build_sandbox_provider(self._settings)
        sandbox_service = SandboxJobService(
            repository,
            SandboxSupervisor(provider),
            artifact_service,
            workspace_service,
        )
        return RuntimeInfrastructure(
            permissions=permissions,
            artifact_service=artifact_service,
            workspace_repository=workspace_repository,
            workspace_service=workspace_service,
            sandbox_service=sandbox_service,
            memory_service=MemoryToolService(
                repository,
                writes_enabled=self._settings.agent_memory_write_enabled,
            ),
        )

    async def _subagent_supervisor(
        self,
        repository: RunUnitOfWork,
        run: RunRecord,
        run_id: str,
        policy: EffectiveReasoningPolicy,
        permission_runtime: RootPermissionRuntime,
    ) -> SubagentSupervisor | None:
        live_states = await ToolSettingsRepository(repository.session).get_or_create(default_tool_states(self._settings))
        eligibility = subagent_execution_eligibility(
            policy.subagents,
            live_swarm_enabled=bool(live_states.get("swarm", False)),
        )
        if not eligibility.executable:
            return None
        identity = await permission_runtime.ensure()
        root_execution = await AgentExecutionRepository(repository.session).get_or_create_root(run_id)
        await self._bind_root_identity(repository, root_execution, identity)
        supervisor = self._create_supervisor(
            repository,
            run_id,
            root_execution.id,
            identity.id,
            policy,
        )
        await supervisor.wake()
        self._close_supervisor_with_owner(supervisor, run_id)
        return supervisor

    async def _bind_root_identity(
        self,
        repository: RunUnitOfWork,
        root_execution: Any,
        identity: AgentIdentityRecord,
    ) -> None:
        if root_execution.identity_id is None:
            root_execution.identity_id = identity.id
            root_execution.state_version += 1
            await repository.session.commit()
            return
        if root_execution.identity_id != identity.id:
            raise ToolExecutionError(
                "subagent_identity_mismatch",
                "Root AgentExecution is bound to a different identity",
            )

    def _create_supervisor(
        self,
        repository: RunUnitOfWork,
        run_id: str,
        root_execution_id: str,
        identity_id: str,
        policy: EffectiveReasoningPolicy,
    ) -> SubagentSupervisor:
        child_sessions = async_sessionmaker(
            repository.session.bind,
            expire_on_commit=False,
            class_=type(repository.session),
        )
        return SubagentSupervisor(
            settings=self._settings,
            session=repository.session,
            session_factory=child_sessions,
            run_id=run_id,
            parent_execution_id=root_execution_id,
            parent_identity_id=identity_id,
            policy=policy.subagents,
            tool_registry=self._tool_registry,
            model_client_factory=lambda: build_model_client(
                self._settings,
                http_client=getattr(self._model_client, "_http_client", None),
            ),
        )

    def _close_supervisor_with_owner(
        self,
        supervisor: SubagentSupervisor,
        run_id: str,
    ) -> None:
        owner_task = asyncio.current_task()
        if owner_task is None:
            return

        def close(completed_task: asyncio.Task[Any]) -> None:
            failed = completed_task.cancelled()
            if not failed:
                try:
                    failed = completed_task.exception() is not None
                except asyncio.CancelledError:
                    failed = True
            close_task = asyncio.create_task(
                supervisor.close(cancel=failed),
                name=f"subagent-supervisor-close:{run_id}",
            )
            self._supervisor_close_tasks.add(close_task)
            close_task.add_done_callback(self._supervisor_close_tasks.discard)

        owner_task.add_done_callback(close)

    def _limits(
        self,
        profile: RunExecutionProfile,
        policy: EffectiveReasoningPolicy,
    ) -> tuple[int, int | None, int, int]:
        max_turns = self._bounded(policy.budgets.max_turns, self._settings.agent_max_turns)
        unlimited_tools = (
            profile.answer_mode == AnswerMode.trusted
            and policy.reasoning_effort == ReasoningEffort.deep
            and policy.budgets.max_tool_calls is None
        )
        max_tool_calls = (
            None
            if unlimited_tools
            else self._bounded(
                policy.budgets.max_tool_calls,
                self._settings.agent_max_tool_calls,
            )
        )
        return (
            max_turns,
            max_tool_calls,
            min(
                policy.budgets.max_reflections,
                self._settings.agent_max_reflections,
            ),
            min(policy.budgets.max_replans, self._settings.agent_max_replans),
        )

    @staticmethod
    def _bounded(requested: int | None, server_limit: int) -> int:
        return server_limit if requested is None else min(requested, server_limit)

    @staticmethod
    def _progress(active_plan: Any, run: RunRecord, tool_calls: list[Any]) -> ExecutionProgress:
        return ExecutionProgress(
            active_plan=active_plan,
            observations=list((run.agent_state or {}).get("observations", [])),
            tool_calls_used=sum(1 for call in tool_calls if call.status in {"running", "succeeded", "failed"}),
        )
