from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import StateError, ValidationError
from app.db.models import RunRecord, TaskRecord, utc_now

CONTEXT_STATE_VERSION = 1
CONTEXT_TERMINAL_STATUSES = frozenset(
    {"completed", "completed_with_warnings", "failed", "blocked", "cancelled"}
)
_CJK_RE = re.compile(
    r"[\u2e80-\u2eff\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf"
    r"\u4e00-\u9fff\uac00-\ud7af\uff00-\uffef]"
)


@dataclass(frozen=True)
class ContextWindow:
    provider: str
    model: str
    tokens: int
    max_output_tokens: int | None
    source: str
    verified: bool
    documentation_url: str | None


@dataclass(frozen=True)
class ModelContextCatalogEntry:
    providers: tuple[str, ...]
    model_prefixes: tuple[str, ...]
    window_tokens: int
    max_output_tokens: int | None
    documentation_url: str


_CONTEXT_WINDOW_CATALOG: tuple[ModelContextCatalogEntry, ...] = (
    ModelContextCatalogEntry(
        ("openai",), ("gpt-5.6",), 1_050_000, 128_000,
        "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
    ),
    ModelContextCatalogEntry(
        ("openai",), ("gpt-4.1",), 1_047_576, 32_768,
        "https://developers.openai.com/api/docs/models/gpt-4.1",
    ),
    ModelContextCatalogEntry(
        ("openai",), ("gpt-5",), 400_000, 128_000,
        "https://developers.openai.com/api/docs/models/gpt-5",
    ),
    ModelContextCatalogEntry(
        ("openai",), ("o1", "o3", "o4"), 200_000, 100_000,
        "https://developers.openai.com/api/docs/models",
    ),
    ModelContextCatalogEntry(
        ("openai",), ("gpt-4o", "gpt-4-turbo"), 128_000, 16_384,
        "https://developers.openai.com/api/docs/models",
    ),
    ModelContextCatalogEntry(
        ("anthropic",),
        ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5"),
        1_000_000,
        128_000,
        "https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ModelContextCatalogEntry(
        ("anthropic",),
        ("claude-opus-4-6", "claude-sonnet-4-6"),
        1_000_000,
        128_000,
        "https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ModelContextCatalogEntry(
        ("anthropic",), ("claude",), 200_000, 64_000,
        "https://platform.claude.com/docs/en/about-claude/models/overview",
    ),
    ModelContextCatalogEntry(
        ("google", "gemini"), ("gemini",), 1_048_576, 65_536,
        "https://ai.google.dev/gemini-api/docs/models",
    ),
    ModelContextCatalogEntry(
        ("deepseek",),
        ("deepseek-v4", "deepseek-chat", "deepseek-reasoner"),
        1_000_000,
        384_000,
        "https://api-docs.deepseek.com/quick_start/pricing/",
    ),
    ModelContextCatalogEntry(
        ("xai",), ("grok-4.5",), 500_000, None,
        "https://docs.x.ai/developers/pricing",
    ),
    ModelContextCatalogEntry(
        ("xai",), ("grok-4.3", "grok-4.20"), 1_000_000, None,
        "https://docs.x.ai/developers/pricing",
    ),
)


@dataclass(frozen=True)
class ContextProjection:
    summary: str
    runs: tuple[RunRecord, ...]
    folded_run_ids: frozenset[str]


def resolve_context_window(
    provider: str,
    model: str,
    *,
    fallback_tokens: int = 131_072,
) -> ContextWindow:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    catalog_provider = normalized_provider
    catalog_model = normalized_model
    if normalized_provider == "openrouter" and "/" in normalized_model:
        catalog_provider, catalog_model = normalized_model.split("/", 1)

    catalog_tokens: int | None = None
    catalog_max_output: int | None = None
    documentation_url: str | None = None
    for entry in _CONTEXT_WINDOW_CATALOG:
        if (
            any(item in catalog_provider for item in entry.providers)
            and any(catalog_model.startswith(item) for item in entry.model_prefixes)
        ):
            catalog_tokens = entry.window_tokens
            catalog_max_output = entry.max_output_tokens
            documentation_url = entry.documentation_url
            break

    if catalog_tokens is not None:
        tokens = catalog_tokens
        source = "catalog"
        verified = True
    else:
        tokens = fallback_tokens
        source = "fallback"
        verified = False

    return ContextWindow(
        provider=provider,
        model=model,
        tokens=tokens,
        max_output_tokens=catalog_max_output,
        source=source,
        verified=verified,
        documentation_url=documentation_url,
    )


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
    def __init__(self, session: AsyncSession, settings: Settings):
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
            "version": CONTEXT_STATE_VERSION,
            "summary": str(raw.get("summary") or ""),
            "folded_run_ids": list(
                dict.fromkeys(str(item) for item in raw.get("folded_run_ids", []) if item)
            ),
            "last_action": raw.get("last_action"),
            "last_action_at": raw.get("last_action_at"),
        }

    async def projection(
        self,
        task: TaskRecord,
        *,
        runs: list[RunRecord] | None = None,
    ) -> ContextProjection:
        state = self._state(task)
        folded = frozenset(state["folded_run_ids"])
        all_runs = runs if runs is not None else await self.list_runs(task.id)
        visible = tuple(
            run
            for run in all_runs
            if run.id not in folded
            and (
                run.status in CONTEXT_TERMINAL_STATUSES
                # Older records and test fixtures may carry a finalized summary
                # before their status transition is persisted.
                or bool(run.summary)
            )
        )
        return ContextProjection(
            summary=state["summary"],
            runs=visible,
            folded_run_ids=folded,
        )

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
            context_lines.append("Earlier conversation summary:\n" + projection.summary)
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
        summary_tokens = (
            estimate_messages_tokens([projection.summary]) if projection.summary else 0
        )
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
        if ratio >= 1:
            status = "overflow"
        elif ratio >= self.settings.context_auto_compact_ratio:
            status = "compact_required"
        elif ratio >= self.settings.context_auto_compact_ratio * 0.75:
            status = "warning"
        else:
            status = "normal"
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
            "visible_run_count": len(projection.runs),
            "folded_run_count": len(projection.folded_run_ids),
            "breakdown": [
                {"kind": "system", "tokens": system_tokens, "item_count": 1},
                *(
                    [{"kind": "summary", "tokens": summary_tokens, "item_count": 1}]
                    if summary_tokens
                    else []
                ),
                *(
                    [{
                        "kind": "conversation",
                        "tokens": conversation_tokens,
                        "item_count": len(projection.runs),
                    }]
                    if conversation_tokens
                    else []
                ),
                *(
                    [{"kind": "draft", "tokens": draft_tokens, "item_count": 1}]
                    if draft_tokens
                    else []
                ),
                {
                    "kind": "output_reserve",
                    "tokens": output_reserve,
                    "item_count": 1,
                },
            ],
            "last_action": state.get("last_action"),
            "last_action_at": self._parse_datetime(state.get("last_action_at")),
        }

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
            raise StateError(
                "CONVERSATION_CONTEXT_ACTIVE",
                "对话仍在执行或等待继续，暂时不能修改上下文。",
            )

    def _build_summary(
        self,
        previous_summary: str,
        runs: list[RunRecord],
    ) -> str:
        sections: list[str] = []
        if previous_summary:
            sections.append(previous_summary.strip())
        for run in runs:
            goal, answer = self._run_context(run)
            sections.append(
                "- 用户目标：" + goal.strip()[:600] + "\n"
                + "  回答要点：" + answer.strip()[:1200]
            )
        combined = "\n".join(item for item in sections if item)
        limit = self.settings.context_summary_max_chars
        return combined[-limit:]

    async def compact(
        self,
        task: TaskRecord,
        *,
        retain_runs: int | None = None,
        action: str = "compact",
        require_idle: bool = True,
        commit: bool = True,
    ) -> dict[str, int]:
        runs = await self.list_runs(task.id)
        if require_idle:
            await self.ensure_idle(task.id, runs=runs)
        projection = await self.projection(task, runs=runs)
        retain = (
            self.settings.context_compact_retain_runs
            if retain_runs is None
            else max(0, retain_runs)
        )
        eligible = list(projection.runs[:-retain] if retain else projection.runs)
        if not eligible:
            return {"folded": 0, "retained": len(projection.runs)}
        state = self._state(task)
        folded = list(
            dict.fromkeys([*state["folded_run_ids"], *(run.id for run in eligible)])
        )
        now = utc_now()
        task.context_state = {
            "version": CONTEXT_STATE_VERSION,
            "summary": self._build_summary(state["summary"], eligible),
            "folded_run_ids": folded,
            "last_action": action,
            "last_action_at": now.isoformat(),
        }
        task.updated_at = now
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return {"folded": len(eligible), "retained": len(projection.runs) - len(eligible)}

    async def clear(self, task: TaskRecord, *, commit: bool = True) -> dict[str, int]:
        runs = await self.list_runs(task.id)
        await self.ensure_idle(task.id, runs=runs)
        state = self._state(task)
        folded = list(
            dict.fromkeys([*state["folded_run_ids"], *(run.id for run in runs)])
        )
        now = utc_now()
        task.context_state = {
            "version": CONTEXT_STATE_VERSION,
            "summary": "",
            "folded_run_ids": folded,
            "last_action": "clear",
            "last_action_at": now.isoformat(),
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
            raise ValidationError(
                "CONTEXT_WINDOW_EXCEEDED",
                "当前请求在压缩后仍超出模型上下文窗口，请缩短输入或执行 /clear。",
                {
                    "used_tokens": current["used_tokens"],
                    "available_input_tokens": current["available_input_tokens"],
                },
            )
        return current
