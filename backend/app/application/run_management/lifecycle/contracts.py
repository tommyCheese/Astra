"""Narrow contracts shared by Run application and dispatch adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.api_views import CreateRunResponse

RunStarter = Callable[[str, AstraRuntimeSettings], Awaitable[None]]
RunCompletionCallback = Callable[[asyncio.Task[None]], None]


@dataclass(frozen=True)
class PreparedRunExecution:
    response: CreateRunResponse
    settings: AstraRuntimeSettings


class RunExecutionDispatcher(Protocol):
    def start(
        self,
        run_id: str,
        settings: AstraRuntimeSettings,
        *,
        on_complete: RunCompletionCallback | None = None,
    ) -> asyncio.Task[None]: ...

    async def cancel(self, run_id: str) -> bool: ...

    async def shutdown(self) -> None: ...
