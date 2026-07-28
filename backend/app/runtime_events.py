from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field


@dataclass
class _RunEventState:
    version: int = 0
    subscribers: int = 0
    event: asyncio.Event = field(default_factory=asyncio.Event)


class RunEventBroker:
    """Wake in-process SSE readers after committed RunEvent writes."""

    def __init__(self) -> None:
        self._states: dict[str, _RunEventState] = {}

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
