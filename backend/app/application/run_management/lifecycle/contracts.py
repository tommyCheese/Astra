"""Narrow contracts shared by Run application and dispatch adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.api_views import CreateRunResponse
from app.infrastructure.db.models.runs import RunRecord

RunStarter = Callable[[str, AstraRuntimeSettings], Awaitable[None]]
RunCompletionCallback = Callable[[asyncio.Task[None]], None]


def run_response(run: RunRecord) -> CreateRunResponse:
    return CreateRunResponse(
        task_id=run.task_id,
        run_id=run.id,
        status=run.status,
        answer_mode=run.answer_mode,
    )


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
