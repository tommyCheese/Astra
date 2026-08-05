from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.application.context_compaction.accounting import TokenAccountingService
from app.application.context_compaction.policy import build_compaction_policy
from app.application.context_compaction.service import AgentContextCompactionService
from app.common.schemas.context_compaction import (
    ContextEnvelope,
    ContextItem,
    ContextOwnerRole,
    ContinuationManifest,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.repositories.context_compaction import ContextCompactionAttemptRepository


class SemanticConversationCompactor:
    """Builds and installs a semantic checkpoint for old conversation runs."""

    def __init__(self, manager: Any, window_resolver: Callable[..., Any]):
        self.manager = manager
        self.settings = manager.settings
        self.session = manager.session
        self.window_resolver = window_resolver

    async def compact(
        self,
        task: TaskRecord,
        *,
        retain_runs: int | None,
        action: str,
        require_idle: bool,
        commit: bool,
        direction: str,
    ) -> dict[str, int | str]:
        projection, eligible = await self._eligible_runs(
            task, retain_runs=retain_runs, require_idle=require_idle
        )
        if not eligible:
            return {"folded": 0, "retained": len(projection.runs)}
        state = self.manager._state(task)
        accounting = TokenAccountingService()
        envelope = self._build_envelope(task, eligible, state, direction, accounting)
        result = await self._generate_checkpoint(envelope, accounting)
        if result.checkpoint is None:
            return await self._record_failure(task, projection, result, commit=commit)
        return await self._record_success(
            task,
            projection,
            eligible,
            result,
            action=action,
            direction=direction,
            commit=commit,
        )

    async def _eligible_runs(self, task, *, retain_runs, require_idle):
        runs = await self.manager.list_runs(task.id)
        if require_idle:
            await self.manager.ensure_idle(task.id, runs=runs)
        projection = await self.manager.projection(task, runs=runs)
        retain = (
            self.settings.context_compact_retain_runs
            if retain_runs is None
            else max(0, retain_runs)
        )
        eligible = list(projection.runs[:-retain] if retain else projection.runs)
        return projection, eligible

    def _build_envelope(self, task, eligible, state, direction, accounting):
        body = self._body_items(eligible, accounting)
        if state["summary"] and not state["checkpoint"]:
            count, _, _ = accounting.count_text(state["summary"])
            body.insert(
                0,
                ContextItem(
                    id=f"legacy:{task.id}",
                    kind="legacy_v1_summary",
                    content=state["summary"],
                    summary=state["summary"],
                    token_count=count,
                ),
            )
        prefix_text = direction.strip() or task.description or task.title
        prefix = self._prefix_item(task.id, prefix_text, accounting)
        window = self.window_resolver(
            self.settings.model_provider,
            self.settings.model_name,
            fallback_tokens=self.settings.context_window_fallback_tokens,
        )
        output_reserve = min(
            self.settings.context_output_reserve_tokens,
            window.max_output_tokens or self.settings.context_output_reserve_tokens,
        )
        accounting_result = accounting.account(
            context_window=window.tokens,
            output_reserve=output_reserve,
            compaction_output_reserve=self.settings.context_compaction_output_reserve_tokens,
            protected_prefix=prefix,
            checkpoint=self._checkpoint_item(task.id, state),
            body=body,
        )
        return ContextEnvelope(
            owner_type=ContextOwnerRole.conversation,
            owner_id=task.id,
            purpose=prefix_text,
            protected_prefix=prefix,
            prior_checkpoint=state["checkpoint"],
            compactable_body=tuple(body),
            accounting=accounting_result,
            continuation=ContinuationManifest(
                owner_type=ContextOwnerRole.conversation,
                owner_id=task.id,
                state_version=state["state_version"],
                window_number=state["window_number"],
                source_item_ids=tuple(run.id for run in eligible),
            ),
        )

    def _body_items(self, eligible, accounting):
        body = []
        for run in eligible:
            goal, answer = self.manager._run_context(run)
            content = {
                "run_id": run.id,
                "goal": goal,
                "answer": answer,
                "status": run.status,
                "created_at": run.created_at.isoformat(),
            }
            count, _, _ = accounting.count_value(content)
            body.append(
                ContextItem(
                    id=run.id,
                    kind="conversation_run",
                    content=content,
                    summary=f"User: {goal}\nAssistant: {answer}",
                    token_count=count,
                )
            )
        return body

    @staticmethod
    def _prefix_item(task_id, prefix_text, accounting):
        count, _, _ = accounting.count_text(prefix_text)
        return (
            ContextItem(
                id=f"conversation:{task_id}:intent",
                kind="current_request",
                content=prefix_text,
                summary=prefix_text,
                token_count=count,
                canonical=True,
            ),
        )

    @staticmethod
    def _checkpoint_item(task_id, state):
        if not state["checkpoint"]:
            return ()
        return (
            ContextItem(
                id=f"checkpoint:{task_id}",
                kind="prior_checkpoint",
                content=state["checkpoint"],
            ),
        )

    async def _generate_checkpoint(self, envelope, accounting):
        from app.infrastructure.model_clients.factory import build_model_client

        attempts = ContextCompactionAttemptRepository(self.session)
        service = AgentContextCompactionService(attempts, accounting=accounting)
        client = build_model_client(self.settings)
        try:
            return await service.compact(
                envelope,
                build_compaction_policy(self.settings, ContextOwnerRole.conversation),
                generate=client.generate_context_checkpoint,
                install=attempts.install_conversation_checkpoint,
            )
        finally:
            await client.aclose()

    async def _record_failure(self, task, projection, result, *, commit):
        raw = task.context_state if isinstance(task.context_state, dict) else {}
        failure_code = result.failure_code or "compaction_failed"
        task.context_state = {**raw, "compaction_failure_code": failure_code}
        task.updated_at = utc_now()
        await self._finish(commit)
        return {
            "folded": 0,
            "retained": len(projection.runs),
            "status": "failed",
            "failure_code": failure_code,
        }

    async def _record_success(
        self, task, projection, eligible, result, *, action, direction, commit
    ):
        await self.session.refresh(task)
        raw = task.context_state if isinstance(task.context_state, dict) else {}
        task.context_state = {
            **raw,
            "token_before": result.token_before,
            "token_after": result.token_after,
            "compaction_implementation": (
                result.implementation.value if result.implementation else "astra_semantic"
            ),
            "compaction_failure_code": None,
            "last_action": action,
            "last_action_at": utc_now().isoformat(),
            "compaction_direction": direction.strip(),
        }
        task.updated_at = utc_now()
        await self._finish(commit)
        folded = len(eligible) - len(result.retained_tail_ids)
        return {"folded": folded, "retained": len(projection.runs) - folded}

    async def _finish(self, commit: bool) -> None:
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
