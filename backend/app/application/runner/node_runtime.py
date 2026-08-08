"""Runtime resources and observation projections for one parallel Plan node."""

from dataclasses import dataclass
from typing import Any

from app.application.runner.coordinator import NodeContextSnapshot
from app.application.workspaces.artifacts import ArtifactService, LocalArtifactStore
from app.application.workspaces.runtime import WorkspaceRuntimeService
from app.common.schemas.agent.execution_state import AgentObservation
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.workspaces import WorkspaceRepository
from app.infrastructure.sandbox.docker_provider import build_sandbox_provider
from app.infrastructure.sandbox.runtime import SandboxJobService, SandboxSupervisor


@dataclass
class ParallelNodeRuntime:
    run: Any
    artifact_service: ArtifactService
    workspace_service: WorkspaceRuntimeService
    sandbox_service: SandboxJobService
    goal: str
    observations: list[dict[str, Any]]
    evidence_refs: list[str]
    excluded_tools: set[str]
    maximum_turns: int
    maximum_tool_calls: int
    tool_calls: int = 0


async def prepare_parallel_node_runtime(
    settings, repository: RunUnitOfWork, context: NodeContextSnapshot
) -> ParallelNodeRuntime:
    run = await repository.require_run(context.run_id)
    artifact_service = ArtifactService(
        repository,
        LocalArtifactStore(settings.artifact_store_path),
        max_files=settings.artifact_max_files,
        max_bytes=settings.artifact_max_bytes,
    )
    workspace_service = WorkspaceRuntimeService(
        WorkspaceRepository(repository.session),
        settings.task_workspace_store_path,
        max_files=settings.task_workspace_max_files,
        max_bytes=settings.task_workspace_max_bytes,
        max_file_bytes=settings.task_workspace_max_file_bytes,
        artifact_store_path=settings.artifact_store_path,
    )
    sandbox_service = SandboxJobService(
        repository,
        SandboxSupervisor(build_sandbox_provider(settings)),
        artifact_service,
        workspace_service,
    )
    dependency_refs = set(context.dependency_evidence)
    observations = [
        observation_from_call(call)
        for call in run.tool_calls
        if call.id in dependency_refs and call.status == "succeeded"
    ]
    goal = str(
        context.task_contract.get("original_goal")
        or run.model_policy.get("conversation_goal")
        or context.node["intent"]
    )
    return ParallelNodeRuntime(
        run=run,
        artifact_service=artifact_service,
        workspace_service=workspace_service,
        sandbox_service=sandbox_service,
        goal=goal,
        observations=observations,
        evidence_refs=list(context.dependency_evidence),
        excluded_tools=set(),
        maximum_turns=max(1, int(context.reserved_budgets.get("turns", 1))),
        maximum_tool_calls=max(0, int(context.reserved_budgets.get("tool_calls", 0))),
    )


def observation_from_call(call) -> dict[str, Any]:
    return observation_from_output(
        call.plan_node_id,
        call.node_execution_id,
        call.tool_name,
        call.id,
        dict(call.output or {}),
    )


def observation_from_output(
    plan_node_id: str | None,
    execution_id: str | None,
    tool_name: str,
    tool_call_id: str,
    output: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(output.get("data") or output)
    return AgentObservation(
        plan_node_id=plan_node_id,
        kind="tool_result",
        status="succeeded",
        summary=f"{tool_name} completed",
        data={
            "tool_name": tool_name,
            **payload,
            "tool_call_id": tool_call_id,
            "node_execution_id": execution_id,
        },
    ).model_dump(mode="json")


def evidence_pack(observations: list[dict[str, Any]]) -> dict[str, Any]:
    fetched_sources = [
        observation.get("data", {})
        for observation in observations
        if observation.get("kind") == "tool_result"
        and observation.get("data", {}).get("url")
        and (observation.get("data", {}).get("content") or observation.get("data", {}).get("snapshot"))
    ]
    return {"fetched_sources": fetched_sources}
