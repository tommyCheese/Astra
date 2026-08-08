from __future__ import annotations

import re
from typing import Any

from app.common.schemas.agent.fast_runtime import FastExecutionResult
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


class FastFinalizer:
    async def persist(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        execution: FastExecutionResult,
    ) -> dict[str, Any]:
        answer, referenced_artifact_ids = await self._clean_artifact_references(
            repo, run_id, execution.answer
        )
        result = {
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
            "audit_refs": {"referenced_artifact_ids": referenced_artifact_ids},
            "verification_report": None,
            "completion_decision": None,
        }
        if execution.status == "waiting_user":
            current = await repo.require_run_core(run_id)
            if not current.waiting_state:
                await repo.set_waiting_state(
                    run_id,
                    {"kind": "fast_user_question", "request": answer},
                )
            await repo.update_run_status(run_id, execution.status, summary=answer)
        else:
            await repo.update_run_status(
                run_id,
                execution.status,
                summary=answer,
                result=result,
            )
        event_type = {
            "completed": "fast.completed",
            "waiting_user": "fast.waiting",
            "blocked": "fast.blocked",
        }[execution.status]
        await repo.add_event(
            run_id,
            event_type,
            {
                "status": execution.status,
                "model_call_count": execution.model_call_count,
                "tool_action_count": execution.tool_action_count,
                "first_token_latency_ms": execution.first_token_latency_ms,
                "elapsed_ms": execution.elapsed_ms,
                "runtime": "fast-v1",
                "runtime_version": 1,
            },
        )
        await repo.session.commit()
        return result

    @staticmethod
    async def _clean_artifact_references(repo, run_id: str, answer: str) -> tuple[str, list[str]]:
        artifacts = await repo.list_artifacts(run_id)
        allowed = {
            str(item.id)
            for item in artifacts
            if item.security_status == "verified" and item.storage_key
        }
        referenced: list[str] = []
        pattern = re.compile(r"artifact:(?://)?([0-9a-fA-F-]{8,64})")

        def replace(match: re.Match[str]) -> str:
            artifact_id = match.group(1)
            if artifact_id not in allowed:
                return ""
            if artifact_id not in referenced:
                referenced.append(artifact_id)
            return match.group(0)

        return pattern.sub(replace, answer), referenced
