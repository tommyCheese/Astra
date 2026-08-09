"""Test harness for invoking the production trusted composition directly."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.infrastructure.bootstrap.trusted_runtime import run_trusted_runtime


class TrustedRuntimeHarness:
    def __init__(
        self,
        settings,
        *,
        model_client,
        tool_registry,
        sandbox_provider=None,
    ) -> None:
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.sandbox_provider = sandbox_provider
        self._supervisor_close_tasks: set[Any] = set()

    async def run(
        self,
        repository,
        run_id: str,
        goal: str,
        on_answer_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        return await run_trusted_runtime(
            settings=self.settings,
            model_client=self.model_client,
            tool_registry=self.tool_registry,
            repository=repository,
            run_id=run_id,
            goal=goal,
            on_answer_delta=on_answer_delta,
            sandbox_provider=self.sandbox_provider,
            supervisor_close_tasks=self._supervisor_close_tasks,
        )
