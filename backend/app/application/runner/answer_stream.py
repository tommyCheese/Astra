from __future__ import annotations

import time

from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

STREAM_FLUSH_INTERVAL_SECONDS = 0.1
STREAM_FLUSH_MAX_CHARS = 512


class AnswerStreamMixin:
    async def _emit_answer_stream(self, repo: RunUnitOfWork, run_id: str, content: str) -> None:
        await self._start_answer_stream(repo, run_id)
        await self._answer_delta(repo, run_id, content)
        await self._complete_answer_stream(repo, run_id, content)

    async def _start_answer_stream(self, repo: RunUnitOfWork, run_id: str) -> None:
        self._answer_buffers[run_id] = ""
        self._answer_flush_at[run_id] = 0.0
        self._answer_start_pending.add(run_id)

    async def _ensure_answer_stream_started(self, repo: RunUnitOfWork, run_id: str) -> None:
        if run_id not in self._answer_start_pending:
            return
        self._answer_start_pending.discard(run_id)
        await repo.add_event(
            run_id,
            "answer.started",
            {"role": "assistant", "mode": "native"},
            flush=False,
        )

    async def _answer_delta(self, repo: RunUnitOfWork, run_id: str, delta: str) -> None:
        if not delta:
            return
        await self._ensure_answer_stream_started(repo, run_id)
        await repo.add_event(run_id, "answer.delta", {"delta": delta})
        await repo.session.commit()

    async def _handle_answer_delta(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        delta: str,
        *,
        background_verification: bool = False,
    ) -> None:
        if delta == "\0":
            await self._start_answer_stream(repo, run_id)
            return
        if delta == "\1":
            buffered = self._answer_buffers.get(run_id, "")
            self._answer_buffers[run_id] = ""
            await self._ensure_answer_stream_started(repo, run_id)
            if buffered:
                await repo.add_event(run_id, "answer.delta", {"delta": buffered})
            await repo.add_event(
                run_id,
                "answer.content.completed",
                {"background_verification": background_verification},
            )
            await repo.session.commit()
            return
        if not delta:
            return
        buffered = self._answer_buffers.get(run_id, "") + delta
        now = time.monotonic()
        last_flush = self._answer_flush_at.get(run_id, 0.0)
        first_delta = last_flush == 0.0
        should_flush = (
            first_delta
            or now - last_flush >= STREAM_FLUSH_INTERVAL_SECONDS
            or len(buffered) >= STREAM_FLUSH_MAX_CHARS
        )
        if should_flush:
            self._answer_buffers[run_id] = ""
            self._answer_flush_at[run_id] = now
            await self._answer_delta(repo, run_id, buffered)
        else:
            self._answer_buffers[run_id] = buffered

    async def _complete_answer_stream(self, repo: RunUnitOfWork, run_id: str, content: str) -> None:
        buffered = self._answer_buffers.pop(run_id, "")
        self._answer_flush_at.pop(run_id, None)
        await self._ensure_answer_stream_started(repo, run_id)
        if buffered:
            await repo.add_event(run_id, "answer.delta", {"delta": buffered})
        await repo.add_event(
            run_id,
            "answer.completed",
            {"content": content, "status": "answer_complete"},
        )
        await repo.session.commit()

    async def _mark_named_step_running(self, repo: RunUnitOfWork, run_id: str, name_part: str):
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if name_part in step.title or name_part in step.intent:
                await repo.update_step(step.id, "running")
                return step
        return None

    async def _complete_pending_steps(self, repo: RunUnitOfWork, run_id: str) -> None:
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if step.status in {"pending", "running"}:
                await repo.update_step(
                    step.id,
                    "completed",
                    evidence={"handled_by": "agent_loop"},
                )
