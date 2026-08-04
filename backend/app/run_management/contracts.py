"""Narrow contracts shared by Run application and dispatch adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings
from app.schemas.agent.api_views import CreateRunResponse

RunStarter = Callable[[str, Settings], Awaitable[None]]
RunCompletionCallback = Callable[[asyncio.Task[None]], None]


@dataclass(frozen=True)
class PreparedRun:
    response: CreateRunResponse
    settings: Settings


class RunDispatcher(Protocol):
    def start(
        self,
        run_id: str,
        settings: Settings,
        *,
        on_complete: RunCompletionCallback | None = None,
    ) -> asyncio.Task[None]: ...

    async def cancel(self, run_id: str) -> bool: ...

    async def shutdown(self) -> None: ...
