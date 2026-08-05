import asyncio

import pytest

from app.application.run_management.dispatcher import InProcessRunDispatcher
from app.common.core.config import AstraRuntimeSettings


@pytest.mark.asyncio
async def test_dispatcher_retains_run_until_engine_finishes():
    started = asyncio.Event()
    release = asyncio.Event()

    async def run_engine(_run_id, _settings):
        started.set()
        await release.wait()

    dispatcher = InProcessRunDispatcher(run_engine)
    task = dispatcher.start("run-1", AstraRuntimeSettings())
    await started.wait()

    assert dispatcher.active_run_ids() == ("run-1",)
    assert dispatcher.start("run-1", AstraRuntimeSettings()) is task

    release.set()
    await task
    await asyncio.sleep(0)
    assert dispatcher.active_run_ids() == ()


@pytest.mark.asyncio
async def test_dispatcher_cancels_run_idempotently():
    started = asyncio.Event()

    async def run_engine(_run_id, _settings):
        started.set()
        await asyncio.Event().wait()

    dispatcher = InProcessRunDispatcher(run_engine)
    dispatcher.start("run-1", AstraRuntimeSettings())
    await started.wait()

    assert await dispatcher.cancel("run-1") is True
    assert await dispatcher.cancel("run-1") is False
    assert dispatcher.active_run_ids() == ()


@pytest.mark.asyncio
async def test_dispatcher_survives_engine_cleanup_failure():
    started = asyncio.Event()

    async def run_engine(_run_id, _settings):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            raise RuntimeError("cleanup failed") from error

    dispatcher = InProcessRunDispatcher(run_engine)
    dispatcher.start("run-1", AstraRuntimeSettings())
    await started.wait()

    assert await dispatcher.cancel("run-1") is True
    assert dispatcher.active_run_ids() == ()


@pytest.mark.asyncio
async def test_dispatcher_shutdown_cancels_every_active_run():
    started = {"run-1": asyncio.Event(), "run-2": asyncio.Event()}

    async def run_engine(run_id, _settings):
        started[run_id].set()
        await asyncio.Event().wait()

    dispatcher = InProcessRunDispatcher(run_engine)
    dispatcher.start("run-1", AstraRuntimeSettings())
    dispatcher.start("run-2", AstraRuntimeSettings())
    await asyncio.gather(*(event.wait() for event in started.values()))

    await dispatcher.shutdown()

    assert dispatcher.active_run_ids() == ()
