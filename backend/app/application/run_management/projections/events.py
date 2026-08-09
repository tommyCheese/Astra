from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

MAX_PUBLISHED_EVENTS_PER_RUN = 2048
COALESCIBLE_EVENT_TYPES = frozenset(
    {
        "answer.delta",
        "reasoning.summary.delta",
        "subagent.progress",
        "subagent.heartbeat",
        "budget.usage_updated",
    }
)


@dataclass(frozen=True)
class PublishedRunEvent:
    id: int
    run_id: str
    type: str
    payload: dict[str, Any]
    created_at: str
    agent_execution_id: str | None = None


@dataclass
class _RunEventState:
    version: int = 0
    subscribers: int = 0
    event: asyncio.Event = field(default_factory=asyncio.Event)
    published_events: list[PublishedRunEvent] = field(default_factory=list)
    cache_complete: bool = True
    dropped_through_id: int = 0


@dataclass
class RunEventBroker:
    """Wake in-process SSE readers after committed RunEvent writes."""

    _states: dict[str, _RunEventState] = field(default_factory=dict)

    def subscribe(self, run_id: str) -> int:
        state = self._states.setdefault(run_id, _RunEventState())
        state.subscribers += 1
        return state.version

    def unsubscribe(self, run_id: str) -> None:
        state = self._states.get(run_id)
        if state is None:
            return
        state.subscribers = max(0, state.subscribers - 1)
        if state.subscribers == 0:
            self._states.pop(run_id, None)

    def publish(self, run_id: str) -> None:
        state = self._states.get(run_id)
        if state is None:
            return
        state.cache_complete = False
        self._wake(state)

    def publish_events(self, events: list[PublishedRunEvent]) -> None:
        events_by_run: dict[str, list[PublishedRunEvent]] = {}
        for event in events:
            events_by_run.setdefault(event.run_id, []).append(event)
        for run_id, run_events in events_by_run.items():
            state = self._states.get(run_id)
            if state is None:
                continue
            for published in run_events:
                if published.type not in COALESCIBLE_EVENT_TYPES:
                    state.published_events.append(published)
                    continue
                replacement_index = next(
                    (
                        index
                        for index in range(len(state.published_events) - 1, -1, -1)
                        if state.published_events[index].type == published.type
                        and state.published_events[index].agent_execution_id == published.agent_execution_id
                    ),
                    None,
                )
                if replacement_index is None:
                    state.published_events.append(published)
                else:
                    state.published_events[replacement_index] = published
            overflow = len(state.published_events) - MAX_PUBLISHED_EVENTS_PER_RUN
            if overflow > 0:
                state.dropped_through_id = max(
                    state.dropped_through_id,
                    state.published_events[overflow - 1].id,
                )
                del state.published_events[:overflow]
            self._wake(state)

    def events_after(self, run_id: str, after_id: int) -> list[PublishedRunEvent] | None:
        """Return committed in-process events, or None when a DB refresh is required."""
        state = self._states.get(run_id)
        if state is None or not state.cache_complete or after_id < state.dropped_through_id:
            return None
        return [event for event in state.published_events if event.id > after_id]

    def mark_database_synced(self, run_id: str, through_id: int) -> None:
        state = self._states.get(run_id)
        if state is None:
            return
        state.published_events = [event for event in state.published_events if event.id > through_id]
        state.dropped_through_id = max(state.dropped_through_id, through_id)
        state.cache_complete = True

    @staticmethod
    def _wake(state: _RunEventState) -> None:
        waiting = state.event
        state.version += 1
        state.event = asyncio.Event()
        waiting.set()

    def publish_many(self, run_ids: set[str]) -> None:
        for run_id in run_ids:
            self.publish(run_id)

    async def wait_for_change(
        self,
        run_id: str,
        after_version: int,
        *,
        timeout: float,
    ) -> int:
        state = self._states.get(run_id)
        if state is None or state.version != after_version:
            return state.version if state is not None else after_version
        event = state.event
        with suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=timeout)
        state = self._states.get(run_id)
        return state.version if state is not None else after_version


run_event_broker = RunEventBroker()
