"""Shared terminal persistence for canonical Runtime outcomes."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.application.agent_runtime.contracts import (
    LoopOutcome,
)
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

logger = logging.getLogger("astra.run_finalization")


async def finalize_standard_run(
    repository: RunUnitOfWork,
    run_id: str,
    outcome: LoopOutcome,
    metrics: Any,
) -> None:
    status, answer = _terminal_status(outcome)
    answer, artifact_ids = await _clean_artifact_references(repository, run_id, answer)
    result = _standard_result(answer, artifact_ids)
    if status == "waiting_user":
        run = await repository.require_run_core(run_id)
        if not run.waiting_state:
            await repository.set_waiting_state(
                run_id,
                {"kind": "fast_user_question", "request": answer},
            )
        await repository.update_run_status(run_id, status, summary=answer)
    else:
        await repository.update_run_status(run_id, status, summary=answer, result=result)
    await repository.add_event(
        run_id,
        f"fast.{_terminal_event(status)}",
        {
            "status": status,
            "model_call_count": metrics.model_calls,
            "tool_action_count": metrics.tool_actions,
            "first_token_latency_ms": metrics.first_token_latency_ms,
            "elapsed_ms": metrics.elapsed_ms,
            "runtime": "fast-v1",
            "runtime_version": 1,
        },
    )
    await repository.session.commit()


async def finalize_trusted_run(
    repository: RunUnitOfWork,
    run_id: str,
    final_answer: AgentFinalAnswer,
    result: dict[str, Any],
    status: str,
) -> None:
    if status == "waiting_user":
        await repository.update_run_status(
            run_id,
            status,
            summary=final_answer.summary,
        )
        await _sync_root_execution(repository, run_id)
        await repository.session.commit()
        return
    await repository.add_event(
        run_id,
        "reasoning.phase.started",
        {"phase": "synthesizing", "label": "正在组织回答"},
    )
    await repository.update_run_status(run_id, "synthesizing")
    synth_step = await _mark_named_step_running(repository, run_id, "综合")
    await repository.create_artifact(
        run_id,
        "final_answer",
        content_ref=final_answer.model_dump_json(),
        metadata={"format": "json"},
    )
    if synth_step is not None:
        await repository.update_step(
            synth_step.id,
            "completed",
            evidence={
                "finding_count": len(final_answer.findings),
                "handled_by": "agent_loop",
            },
        )
    await _persist_verification(repository, run_id, result, status)
    current = await repository.require_run_core(run_id)
    if not current.active_plan_id:
        await _complete_pending_steps(repository, run_id)
    await repository.update_run_status(
        run_id,
        status,
        summary=final_answer.summary,
        result=result,
    )
    await _sync_root_execution(repository, run_id)
    await repository.session.commit()


async def _sync_root_execution(repository: RunUnitOfWork, run_id: str) -> None:
    """Keep the root AgentExecution projection aligned with the persisted Run."""
    run = await repository.require_run_core(run_id)
    await AgentExecutionRepository(repository.session).sync_root_from_run(run)


async def _persist_verification(
    repository: RunUnitOfWork,
    run_id: str,
    result: dict[str, Any],
    status: str,
) -> None:
    await repository.add_event(
        run_id,
        "reasoning.phase.started",
        {"phase": "verifying", "label": "正在验证结果"},
    )
    await repository.update_run_status(run_id, "verifying")
    step = await _mark_named_step_running(repository, run_id, "验证")
    if step is None:
        return
    report = result.get("verification_report", {})
    await repository.update_step(
        step.id,
        "completed",
        evidence={
            "status": report.get("status", status),
            "source_count": report.get("source_count", len(result.get("sources", []))),
            "caveat_count": report.get("caveat_count", len(result.get("caveats", []))),
        },
    )


async def _mark_named_step_running(
    repository: RunUnitOfWork,
    run_id: str,
    name_part: str,
) -> Any:
    run = await repository.require_run(run_id)
    for step in sorted(run.steps, key=lambda item: item.index):
        if name_part in step.title or name_part in step.intent:
            await repository.update_step(step.id, "running")
            return step
    return None


async def _complete_pending_steps(repository: RunUnitOfWork, run_id: str) -> None:
    run = await repository.require_run(run_id)
    for step in sorted(run.steps, key=lambda item: item.index):
        if step.status in {"pending", "running"}:
            await repository.update_step(
                step.id,
                "completed",
                evidence={"handled_by": "agent_loop"},
            )


async def _clean_artifact_references(
    repository: RunUnitOfWork,
    run_id: str,
    answer: str,
) -> tuple[str, list[str]]:
    artifacts = await repository.list_artifacts(run_id)
    allowed = {str(item.id) for item in artifacts if item.security_status == "verified" and item.storage_key}
    referenced: list[str] = []

    def replace(match: re.Match[str]) -> str:
        artifact_id = match.group(1)
        if artifact_id not in allowed:
            return ""
        if artifact_id not in referenced:
            referenced.append(artifact_id)
        return match.group(0)

    return re.sub(r"artifact:(?://)?([0-9a-fA-F-]{8,64})", replace, answer), referenced


def _terminal_status(outcome: LoopOutcome) -> tuple[str, str]:
    if outcome.kind == "completed":
        return "completed", outcome.answer
    if outcome.kind == "waiting":
        return "waiting_user", outcome.reason
    if outcome.kind == "cancelled":
        return "cancelled", outcome.reason
    if outcome.kind == "failed":
        return "failed", outcome.reason
    if outcome.kind == "blocked":
        return "blocked", outcome.reason
    raise ValueError("continue is not a terminal outcome")


def _terminal_event(status: str) -> str:
    return {
        "completed": "completed",
        "waiting_user": "waiting",
        "blocked": "blocked",
        "failed": "failed",
        "cancelled": "cancelled",
    }[status]


def _standard_result(answer: str, artifact_ids: list[str]) -> dict[str, object]:
    return {
        "summary": answer,
        "answer_mode": "standard",
        "assurance_level": "basic",
        "findings": [],
        "claims": [],
        "citations": [],
        "sources": [],
        "failed_sources": [],
        "source_quality": [],
        "conflicts": [],
        "caveats": [],
        "verification_notes": [],
        "memory_references": [],
        "audit_refs": {"referenced_artifact_ids": artifact_ids},
        "verification_report": None,
        "completion_decision": None,
    }
