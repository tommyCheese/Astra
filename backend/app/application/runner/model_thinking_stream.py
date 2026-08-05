"""Persist Provider-visible model thinking without mixing it with Astra reasoning summaries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

MODEL_THINKING_FLUSH_INTERVAL_SECONDS = 0.05
MODEL_THINKING_FLUSH_MAX_CHARS = 512
MODEL_THINKING_MAX_CHARS_PER_INVOCATION = 256 * 1024
MODEL_THINKING_MAX_CHARS_PER_RUN = 1024 * 1024


@dataclass
class _ThinkingStreamState:
    metadata: dict[str, Any]
    buffer: str = ""
    char_count: int = 0
    last_flush: float = 0.0
    truncated: bool = False


class ModelThinkingEventWriter:
    """Buffer model-thinking deltas into durable, replayable Run events."""

    def __init__(self, repository: RunUnitOfWork, run_id: str) -> None:
        self._repository = repository
        self._run_id = run_id
        self._streams: dict[str, _ThinkingStreamState] = {}
        self._run_char_count = 0

    async def accept(self, event: dict[str, Any]) -> None:
        phase = str(event.get("phase") or "")
        stream_id = str(event.get("stream_id") or "")
        if not stream_id:
            return
        if phase == "started":
            await self._start(stream_id, event)
        elif phase == "delta":
            await self._delta(stream_id, str(event.get("delta") or ""), event)
        elif phase == "completed":
            await self._complete(stream_id, event)
        elif phase == "unavailable":
            await self._unavailable(stream_id, event)

    @staticmethod
    def _metadata(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "stream_id": str(event.get("stream_id") or "")[:80],
            "provider": str(event.get("provider") or "")[:80],
            "model": str(event.get("model") or "")[:160],
            "operation": str(event.get("operation") or "")[:80],
            "attempt": max(1, int(event.get("attempt") or 1)),
            "content_level": (
                event.get("content_level")
                if event.get("content_level") in {"reasoning", "summary"}
                else "unavailable"
            ),
        }

    async def _start(self, stream_id: str, event: dict[str, Any]) -> None:
        if stream_id in self._streams:
            return
        metadata = self._metadata(event)
        self._streams[stream_id] = _ThinkingStreamState(metadata=metadata)
        await self._repository.add_event(
            self._run_id, "model_thinking.started", metadata
        )
        await self._repository.session.commit()

    async def _delta(self, stream_id: str, delta: str, event: dict[str, Any]) -> None:
        if not delta:
            return
        state = self._streams.get(stream_id)
        if state is None:
            await self._start(stream_id, event)
            state = self._streams[stream_id]
        invocation_remaining = MODEL_THINKING_MAX_CHARS_PER_INVOCATION - state.char_count
        run_remaining = MODEL_THINKING_MAX_CHARS_PER_RUN - self._run_char_count
        accepted = delta[: max(0, min(invocation_remaining, run_remaining))]
        if accepted:
            state.buffer += accepted
            state.char_count += len(accepted)
            self._run_char_count += len(accepted)
        if len(accepted) < len(delta):
            state.truncated = True
        current_time = time.monotonic()
        if state.buffer and (
            state.last_flush == 0.0
            or current_time - state.last_flush >= MODEL_THINKING_FLUSH_INTERVAL_SECONDS
            or len(state.buffer) >= MODEL_THINKING_FLUSH_MAX_CHARS
        ):
            await self._flush(state)
            state.last_flush = current_time

    async def _complete(self, stream_id: str, event: dict[str, Any]) -> None:
        state = self._streams.get(stream_id)
        if state is None:
            await self._unavailable(stream_id, {**event, "reason": "provider_returned_no_visible_thinking"})
            return
        await self._flush(state)
        await self._repository.add_event(
            self._run_id,
            "model_thinking.completed",
            {
                **state.metadata,
                "status": str(event.get("status") or "completed"),
                "char_count": state.char_count,
                "truncated": state.truncated,
                "invocation_limit": MODEL_THINKING_MAX_CHARS_PER_INVOCATION,
                "run_limit": MODEL_THINKING_MAX_CHARS_PER_RUN,
            },
        )
        await self._repository.session.commit()
        self._streams.pop(stream_id, None)

    async def _unavailable(self, stream_id: str, event: dict[str, Any]) -> None:
        metadata = self._metadata(event)
        await self._repository.add_event(
            self._run_id,
            "model_thinking.unavailable",
            {
                **metadata,
                "reason": str(event.get("reason") or "provider_did_not_return_visible_thinking")[:160],
            },
        )
        await self._repository.session.commit()

    async def _flush(self, state: _ThinkingStreamState) -> None:
        if not state.buffer:
            return
        delta = state.buffer
        state.buffer = ""
        await self._repository.add_event(
            self._run_id,
            "model_thinking.delta",
            {**state.metadata, "delta": delta},
        )
        await self._repository.session.commit()
