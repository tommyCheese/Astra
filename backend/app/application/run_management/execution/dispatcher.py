"""Ownership and lifecycle of in-process Run engine tasks."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from app.application.run_management.execution.run_execution import execute_run_in_process
from app.application.run_management.lifecycle.contracts import RunCompletionCallback, RunStarter
from app.common.core.config import AstraRuntimeSettings

logger = logging.getLogger("astra.run_dispatcher")


@dataclass
class InProcessRunDispatcher:
    _run_starter: RunStarter = execute_run_in_process
    _tasks: set[asyncio.Task[None]] = field(default_factory=set, init=False)
    _tasks_by_run_id: dict[str, asyncio.Task[None]] = field(default_factory=dict, init=False)

    def start(
        self,
        run_id: str,
        settings: AstraRuntimeSettings,
        *,
        on_complete: RunCompletionCallback | None = None,
    ) -> asyncio.Task[None]:
        """Start one Run and retain it strongly until completion."""
        existing_task = self._tasks_by_run_id.get(run_id)
        if existing_task is not None and not existing_task.done():
            return existing_task
        task = asyncio.create_task(
            self._run_starter(run_id, settings),
            name=f"astra-run-{run_id}",
        )
        self._tasks.add(task)
        self._tasks_by_run_id[run_id] = task
        task.add_done_callback(lambda completed_task: self._finish(run_id, completed_task, on_complete))
        return task

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks_by_run_id.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            logger.warning(
                "run.background.cancel_cleanup_failed run_id=%s cause=%s",
                run_id,
                type(error).__name__,
            )
        return True

    async def startup(self) -> None:
        """Satisfy the managed-lifecycle contract; no eager work is required."""

    async def shutdown(self) -> None:
        active_run_ids = tuple(self._tasks_by_run_id)
        if active_run_ids:
            await asyncio.gather(
                *(self.cancel(run_id) for run_id in active_run_ids),
                return_exceptions=True,
            )

    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks_by_run_id))

    def _finish(
        self,
        run_id: str,
        task: asyncio.Task[None],
        on_complete: RunCompletionCallback | None,
    ) -> None:
        self._tasks.discard(task)
        if self._tasks_by_run_id.get(run_id) is task:
            self._tasks_by_run_id.pop(run_id, None)
        self._report_failure(task)
        if on_complete is not None:
            on_complete(task)

    @staticmethod
    def _report_failure(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            logger.info("run.background.cancelled task=%s", task.get_name())
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "run.background.failed task=%s cause=%s",
                task.get_name(),
                type(error).__name__,
                exc_info=(type(error), error, error.__traceback__),
            )
