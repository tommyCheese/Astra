from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.context_compaction.conversation import SemanticConversationCompactor
from app.application.context_compaction.policy import build_compaction_policy
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import AstraInputValidationError, AstraStateConflictError
from app.common.schemas.context_compaction import ContextOwnerRole
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.context_windows import resolve_context_window

CONTEXT_STATE_VERSION = 2
CONTEXT_TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "failed", "blocked", "cancelled"}
)
_CJK_RE = re.compile(
    r"[\u2e80-\u2eff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf"
    r"\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]"
)


@dataclass(frozen=True)
class ConversationContextProjection:
    summary: str
    runs: tuple[RunRecord, ...]
    folded_run_ids: frozenset[str]


def estimate_tokens(text: str) -> int:
    """Conservative tokenizer-independent estimate suitable for preflight budgets."""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + math.ceil(other / 3.2)


def estimate_messages_tokens(messages: list[str]) -> int:
    return sum(estimate_tokens(message) + 6 for message in messages)


class ConversationContextManager:
    def __init__(self, session: AsyncSession, settings: AstraRuntimeSettings):
        self.session = session
        self.settings = settings

    async def require_task(self, task_id: str) -> TaskRecord:
        task = await self.session.get(TaskRecord, task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task

    async def list_runs(self, task_id: str) -> list[RunRecord]:
        result = await self.session.scalars(
            select(RunRecord)
            .where(RunRecord.task_id == task_id)
            .order_by(RunRecord.created_at, RunRecord.id)
        )
        return list(result.all())

    @staticmethod
    def _state(task: TaskRecord) -> dict[str, Any]:
        raw = task.context_state if isinstance(task.context_state, dict) else {}
        return {
            "version": int(raw.get("version", CONTEXT_STATE_VERSION)),
            "checkpoint": raw.get("checkpoint")
            if isinstance(raw.get("checkpoint"), dict)
            else None,
            "state_version": int(raw.get("state_version", 0)),
            "window_number": int(raw.get("window_number", 0)),
            "retained_tail_ids": list(raw.get("retained_tail_ids", [])),
            "token_before": raw.get("token_before"),
            "token_after": raw.get("token_after"),
            "compaction_implementation": raw.get("compaction_implementation"),
            "compaction_failure_code": raw.get("compaction_failure_code"),
            "folded_run_ids": list(
                dict.fromkeys(str(item) for item in raw.get("folded_run_ids", []) if item)
            ),
            "last_action": raw.get("last_action"),
            "last_action_at": raw.get("last_action_at"),
            "command_history": list(raw.get("command_history", [])),
            "compaction_direction": str(raw.get("compaction_direction") or ""),
        }

    async def projection(
        self,
        task: TaskRecord,
        *,
        runs: list[RunRecord] | None = None,
    ) -> ConversationContextProjection:
        state = self._state(task)
        folded = frozenset(state["folded_run_ids"])
        all_runs = runs if runs is not None else await self.list_runs(task.id)
        visible = tuple(
            run
            for run in all_runs
            if run.id not in folded and run.status in CONTEXT_TERMINAL_STATUSES
        )
        return ConversationContextProjection(
            summary=self._checkpoint_text(state["checkpoint"]),
            runs=visible,
            folded_run_ids=folded,
        )

    @staticmethod
    def _checkpoint_text(checkpoint: dict[str, Any] | None) -> str:
        if not checkpoint:
            return ""
        fields = (
            "user_intent",
            "current_constraints",
            "key_decisions",
            "completed_outcomes",
            "open_issues",
            "next_steps",
        )
        selected = {key: checkpoint[key] for key in fields if checkpoint.get(key)}
        return json.dumps(selected, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _run_context(run: RunRecord) -> tuple[str, str]:
        goal = str((run.model_policy or {}).get("conversation_goal") or "")
        answer = str(
            run.summary
            or (run.result or {}).get("summary")
            or ("任务未完成。" if run.status in {"failed", "blocked", "cancelled"} else "")
        )
        return goal, answer

    async def render_goal(self, task: TaskRecord, current_goal: str) -> str:
        projection = await self.projection(task)
        context_lines: list[str] = []
        if projection.summary:
            context_lines.append("Conversation checkpoint:\n" + projection.summary)
        for run in projection.runs:
            goal, answer = self._run_context(run)
            context_lines.extend((f"User: {goal}", f"Assistant: {answer}"))
        if not context_lines:
            return current_goal
        return (
            "Conversation context:\n"
            + "\n".join(context_lines)
            + f"\nCurrent user request: {current_goal}"
        )

    async def status(
        self,
        task: TaskRecord,
        *,
        provider: str,
        model: str,
        draft: str = "",
        runs: list[RunRecord] | None = None,
    ) -> dict[str, Any]:
        projection = await self.projection(task, runs=runs)
        state = self._state(task)
        window = resolve_context_window(
            provider,
            model,
            fallback_tokens=self.settings.context_window_fallback_tokens,
        )
        messages = [projection.summary] if projection.summary else []
        summary_tokens = estimate_messages_tokens([projection.summary]) if projection.summary else 0
        conversation_tokens = 0
        for run in projection.runs:
            run_messages = list(self._run_context(run))
            messages.extend(run_messages)
            conversation_tokens += estimate_messages_tokens(run_messages)
        draft_tokens = estimate_messages_tokens([draft]) if draft else 0
        if draft:
            messages.append(draft)
        system_tokens = self.settings.context_system_reserve_tokens
        used = system_tokens + estimate_messages_tokens(messages)
        output_reserve = self.settings.context_output_reserve_tokens
        if window.max_output_tokens is not None:
            output_reserve = min(output_reserve, window.max_output_tokens)
        available = max(1, window.tokens - output_reserve)
        ratio = used / available
        status = self._usage_status(ratio)
        return {
            "provider": provider,
            "model": model,
            "window_tokens": window.tokens,
            "max_output_tokens": window.max_output_tokens,
            "context_source": window.source,
            "context_verified": window.verified,
            "context_documentation_url": window.documentation_url,
            "available_input_tokens": available,
            "used_tokens": used,
            "remaining_tokens": max(0, available - used),
            "usage_ratio": round(ratio, 6),
            "auto_compact_ratio": self.settings.context_auto_compact_ratio,
            "status": status,
            "estimated": True,
            "summary_active": bool(projection.summary),
            "compaction_implementation": (
                state["compaction_implementation"] or "astra_semantic"
                if state["checkpoint"]
                else None
            ),
            "compaction_failure_code": state["compaction_failure_code"],
            "checkpoint_status": "active" if state["checkpoint"] else "none",
            "window_number": state["window_number"],
            "token_before": state.get("token_before"),
            "token_after": state.get("token_after"),
            "retained_run_count": len(state["retained_tail_ids"]),
            "visible_run_count": len(projection.runs),
            "folded_run_count": len(projection.folded_run_ids),
            "breakdown": self._usage_breakdown(
                system_tokens=system_tokens,
                summary_tokens=summary_tokens,
                conversation_tokens=conversation_tokens,
                conversation_count=len(projection.runs),
                draft_tokens=draft_tokens,
                output_reserve=output_reserve,
            ),
            "last_action": state.get("last_action"),
            "last_action_at": self._parse_datetime(state.get("last_action_at")),
        }

    def _usage_status(self, ratio: float) -> str:
        if ratio >= 1:
            return "overflow"
        if ratio >= self.settings.context_auto_compact_ratio:
            return "compact_required"
        if ratio >= self.settings.context_auto_compact_ratio * 0.75:
            return "warning"
        return "normal"

    @staticmethod
    def _usage_breakdown(
        *,
        system_tokens: int,
        summary_tokens: int,
        conversation_tokens: int,
        conversation_count: int,
        draft_tokens: int,
        output_reserve: int,
    ) -> list[dict[str, int | str]]:
        breakdown = [{"kind": "system", "tokens": system_tokens, "item_count": 1}]
        if summary_tokens:
            breakdown.append({"kind": "summary", "tokens": summary_tokens, "item_count": 1})
        if conversation_tokens:
            breakdown.append(
                {
                    "kind": "conversation",
                    "tokens": conversation_tokens,
                    "item_count": conversation_count,
                }
            )
        if draft_tokens:
            breakdown.append({"kind": "draft", "tokens": draft_tokens, "item_count": 1})
        breakdown.append({"kind": "output_reserve", "tokens": output_reserve, "item_count": 1})
        return breakdown

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

    async def ensure_idle(self, task_id: str, *, runs: list[RunRecord] | None = None) -> None:
        all_runs = runs if runs is not None else await self.list_runs(task_id)
        if any(run.status not in CONTEXT_TERMINAL_STATUSES for run in all_runs):
            raise AstraStateConflictError(
                "CONVERSATION_CONTEXT_ACTIVE",
                "对话仍在执行或等待继续，暂时不能修改上下文。",
            )

    async def compact(
        self,
        task: TaskRecord,
        *,
        retain_runs: int | None = None,
        action: str = "compact",
        require_idle: bool = True,
        commit: bool = True,
        direction: str = "",
    ) -> dict[str, int | str | bool]:
        policy = build_compaction_policy(self.settings, ContextOwnerRole.conversation)
        if not policy.enabled or policy.shadow_mode:
            raise AstraStateConflictError(
                "CONTEXT_COMPACTION_UNAVAILABLE",
                "Semantic context compaction is not enabled.",
            )
        return await self._semantic_compact(
            task,
            retain_runs=retain_runs,
            action=action,
            require_idle=require_idle,
            commit=commit,
            direction=direction,
        )

    async def _semantic_compact(
        self,
        task: TaskRecord,
        *,
        retain_runs: int | None,
        action: str,
        require_idle: bool,
        commit: bool,
        direction: str,
    ) -> dict[str, int | str | bool]:
        return await SemanticConversationCompactor(self, resolve_context_window).compact(
            task,
            retain_runs=retain_runs,
            action=action,
            require_idle=require_idle,
            commit=commit,
            direction=direction,
        )

    async def clear(self, task: TaskRecord, *, commit: bool = True) -> dict[str, int]:
        runs = await self.list_runs(task.id)
        await self.ensure_idle(task.id, runs=runs)
        state = self._state(task)
        folded = list(dict.fromkeys([*state["folded_run_ids"], *(run.id for run in runs)]))
        now = utc_now()
        task.context_state = {
            "version": CONTEXT_STATE_VERSION,
            "state_version": state["state_version"] + 1,
            "window_number": state["window_number"],
            "checkpoint": None,
            "retained_tail_ids": [],
            "folded_run_ids": folded,
            "last_action": "clear",
            "last_action_at": now.isoformat(),
            "command_history": state["command_history"],
            "compaction_direction": "",
        }
        task.updated_at = now
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return {"cleared": len(runs)}

    async def prepare_for_run(
        self,
        task: TaskRecord,
        *,
        provider: str,
        model: str,
        draft: str,
    ) -> dict[str, Any]:
        current = await self.status(
            task,
            provider=provider,
            model=model,
            draft=draft,
        )
        if current["usage_ratio"] >= self.settings.context_auto_compact_ratio:
            await self.compact(
                task,
                retain_runs=self.settings.context_compact_retain_runs,
                action="auto_compact",
                require_idle=False,
                commit=False,
            )
            current = await self.status(
                task,
                provider=provider,
                model=model,
                draft=draft,
            )
        if current["usage_ratio"] >= 1:
            raise AstraInputValidationError(
                "CONTEXT_WINDOW_EXCEEDED",
                "当前请求在压缩后仍超出模型上下文窗口，请缩短输入或执行 /clear。",
                {
                    "used_tokens": current["used_tokens"],
                    "available_input_tokens": current["available_input_tokens"],
                },
            )
        return current
