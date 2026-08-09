"""Small, typed-at-the-boundary projections for Run-owned records."""

from __future__ import annotations

from typing import Any

from app.infrastructure.db.models.runs import RunEventRecord, RunRecord


def step_views(run: RunRecord, canonical_steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if canonical_steps is not None:
        return canonical_steps
    return [
        {
            "id": step.id,
            "index": step.index,
            "title": step.title,
            "intent": step.intent,
            "status": step.status,
            "depends_on": step.depends_on or [],
            "evidence": step.evidence,
            "started_at": step.started_at,
            "completed_at": step.completed_at,
        }
        for step in sorted(run.steps, key=lambda item: item.index)
    ]


def tool_call_views(run: RunRecord) -> list[dict[str, Any]]:
    return [
        {
            "id": call.id,
            "step_id": call.step_id,
            "plan_node_id": call.plan_node_id,
            "node_execution_id": call.node_execution_id,
            "tool_name": call.tool_name,
            "tool_version": call.tool_version,
            "input": call.input,
            "output": call.output,
            "status": call.status,
            "permission": call.permission,
            "side_effect_level": call.side_effect_level,
            "started_at": call.started_at,
            "completed_at": call.completed_at,
            "error": call.error,
        }
        for call in run.tool_calls
    ]


def artifact_views(run: RunRecord) -> list[dict[str, Any]]:
    return [
        {
            "id": artifact.id,
            "type": artifact.type,
            "path": artifact.path,
            "content_ref": artifact.content_ref,
            "metadata": artifact.metadata_,
            "mime_type": artifact.mime_type,
            "size_bytes": artifact.size_bytes,
            "checksum": artifact.checksum,
            "security_status": artifact.security_status,
            "tool_call_id": artifact.tool_call_id,
            "plan_node_id": artifact.plan_node_id,
            "sandbox_job_id": artifact.sandbox_job_id,
            "provenance": artifact.provenance,
            "content_url": f"/api/artifacts/{artifact.id}/content"
            if artifact.storage_key and artifact.security_status == "verified"
            else None,
            "created_at": artifact.created_at,
        }
        for artifact in run.artifacts
    ]


def sandbox_job_views(run: RunRecord) -> list[dict[str, Any]]:
    return [
        {
            "id": job.id,
            "tool_call_id": job.tool_call_id,
            "status": job.status,
            "executor": job.executor,
            "runtime_profile": job.runtime_profile,
            "resource_limits": job.resource_limits,
            "runtime_name": job.runtime_name,
            "image_digest": job.image_digest,
            "exit_reason": job.exit_reason,
            "error": job.error,
            "stdout_summary": job.stdout_summary,
            "stderr_summary": job.stderr_summary,
            "input_artifact_ids": job.input_artifact_ids,
            "output_artifact_ids": job.output_artifact_ids,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }
        for job in run.sandbox_jobs
    ]


def event_views(run: RunRecord) -> list[dict[str, Any]]:
    sequences: dict[str, int] = {}
    return [_event_view(event, sequences) for event in sorted(run.events, key=lambda item: item.id)]


def _event_view(event: RunEventRecord, sequences: dict[str, int]) -> dict[str, Any]:
    agent_sequence = None
    if event.agent_execution_id:
        agent_sequence = sequences.get(event.agent_execution_id, 0) + 1
        sequences[event.agent_execution_id] = agent_sequence
    return {
        "id": event.id,
        "run_sequence": event.id,
        "agent_execution_id": event.agent_execution_id,
        "agent_sequence": agent_sequence,
        "type": event.type,
        "payload": event.payload,
        "created_at": event.created_at,
    }


def turn_views(run: RunRecord) -> list[dict[str, Any]]:
    return [_turn_view(turn) for turn in sorted(run.turns, key=lambda item: item.turn_index)]


def _turn_view(turn) -> dict[str, Any]:
    return {
        "id": turn.id,
        "run_id": turn.run_id,
        "plan_node_id": turn.plan_node_id,
        "node_execution_id": turn.node_execution_id,
        "turn_index": turn.turn_index,
        "decision_type": turn.decision_type,
        "reasoning_summary": turn.reasoning_summary,
        "selected_tool": turn.selected_tool,
        "decision": turn.decision,
        "observation": turn.observation,
        "reflection": turn.reflection,
        "tool_call_id": turn.tool_call_id,
        "artifact_id": turn.artifact_id,
        "memory_reads": turn.memory_reads,
        "memory_writes": turn.memory_writes,
        "status": turn.status,
        "evaluation": turn.evaluation,
        "reflection_patch": turn.reflection_patch,
        "state_version_before": turn.state_version_before,
        "state_version_after": turn.state_version_after,
        "plan_version": turn.plan_version,
        "phase": turn.phase,
        "idempotency_key": turn.idempotency_key,
        "paused_node": turn.paused_node,
        "created_at": turn.created_at,
        "updated_at": turn.updated_at,
    }


def memory_views(run: RunRecord) -> list[dict[str, Any]]:
    return [_memory_view(memory) for memory in run.memories]


def _memory_view(memory) -> dict[str, Any]:
    fields = (
        "id",
        "run_id",
        "memory_key",
        "namespace_type",
        "namespace_id",
        "scope",
        "kind",
        "status",
        "version",
        "state_version",
        "content",
        "structured_data",
        "provenance",
        "confidence",
        "importance",
        "utility_score",
        "access_count",
        "observed_at",
        "valid_from",
        "valid_to",
        "supersedes_id",
        "consolidation_generation",
        "created_at",
        "updated_at",
        "expires_at",
        "last_accessed_at",
        "revoked_at",
        "revoke_reason",
    )
    return {field: getattr(memory, field) for field in fields}


def join_views(run: RunRecord) -> list[dict[str, Any]]:
    return [
        {
            "id": join.id,
            "parent_execution_id": join.parent_execution_id,
            "consumer_plan_node_id": join.consumer_plan_node_id,
            "join_key": join.join_key,
            "group_id": join.group_id,
            "policy": join.policy,
            "child_execution_ids": join.child_execution_ids or [],
            "required_execution_ids": join.required_execution_ids or [],
            "optional_execution_ids": join.optional_execution_ids or [],
            "status": join.status,
            "result": join.result or {},
            "state_version": join.state_version,
            "created_at": join.created_at,
            "completed_at": join.completed_at,
            "updated_at": join.updated_at,
        }
        for join in run.agent_joins
    ]
