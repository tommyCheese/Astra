import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, RunEventRecord
from app.db.session import EventAwareAsyncSession
from app.repositories.runs import RunRepository
from app.runtime_events import (
    MAX_PUBLISHED_EVENTS_PER_RUN,
    PublishedRunEvent,
    RunEventBroker,
    run_event_broker,
)


async def test_run_event_broker_wakes_all_subscribers_without_lost_wakeup():
    broker = RunEventBroker()
    first_version = broker.subscribe("run-1")
    second_version = broker.subscribe("run-1")
    first = asyncio.create_task(
        broker.wait_for_change("run-1", first_version, timeout=1)
    )
    second = asyncio.create_task(
        broker.wait_for_change("run-1", second_version, timeout=1)
    )
    await asyncio.sleep(0)

    broker.publish("run-1")

    assert await asyncio.wait_for(first, timeout=0.05) == first_version + 1
    assert await asyncio.wait_for(second, timeout=0.05) == second_version + 1
    broker.unsubscribe("run-1")
    broker.unsubscribe("run-1")
    assert broker.subscribe("run-1") == 0
    broker.unsubscribe("run-1")


async def test_run_event_broker_delivers_committed_events_without_database_refresh():
    broker = RunEventBroker()
    version = broker.subscribe("run-1")
    event = PublishedRunEvent(
        id=4,
        run_id="run-1",
        type="answer.delta",
        payload={"delta": "首片段"},
        created_at="2026-07-29T00:00:00+00:00",
    )

    broker.publish_events([event])

    assert await broker.wait_for_change("run-1", version, timeout=0.05) == version + 1
    assert broker.events_after("run-1", 0) == [event]
    assert broker.events_after("run-1", 4) == []
    broker.unsubscribe("run-1")


async def test_run_event_broker_falls_back_after_payload_free_notification():
    broker = RunEventBroker()
    broker.subscribe("run-1")

    broker.publish("run-1")

    assert broker.events_after("run-1", 0) is None
    broker.mark_database_synced("run-1", 0)
    assert broker.events_after("run-1", 0) == []
    broker.unsubscribe("run-1")


async def test_run_event_broker_bounds_cache_and_refreshes_lagging_subscriber():
    broker = RunEventBroker()
    broker.subscribe("run-1")
    events = [
        PublishedRunEvent(
            id=index,
            run_id="run-1",
            type="answer.delta",
            payload={"delta": str(index)},
            created_at="2026-07-29T00:00:00+00:00",
        )
        for index in range(1, MAX_PUBLISHED_EVENTS_PER_RUN + 2)
    ]

    broker.publish_events(events)

    assert broker.events_after("run-1", 0) is None
    assert broker.events_after("run-1", 1) == events[1:]
    broker.unsubscribe("run-1")


async def test_event_aware_session_notifies_only_after_commit():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=EventAwareAsyncSession,
    )
    async with sessions() as session:
        repo = RunRepository(session)
        run = await repo.create_task_run("通知测试", {"provider": "mock"})
        version = run_event_broker.subscribe(run.id)

        await repo.add_event(run.id, "test.pending", {})
        unchanged = await run_event_broker.wait_for_change(
            run.id,
            version,
            timeout=0.001,
        )
        assert unchanged == version

        await session.commit()
        changed = await run_event_broker.wait_for_change(
            run.id,
            version,
            timeout=0.05,
        )
        assert changed == version + 1
        published = run_event_broker.events_after(run.id, 0)
        assert published is not None
        assert published[-1].type == "test.pending"

        session.add(RunEventRecord(run_id=run.id, type="test.direct", payload={}))
        await session.commit()
        direct_change = await run_event_broker.wait_for_change(
            run.id,
            changed,
            timeout=0.05,
        )
        assert direct_change == changed + 1
        run_event_broker.unsubscribe(run.id)
    await engine.dispose()


async def test_event_aware_session_discards_rolled_back_notifications():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=EventAwareAsyncSession,
    )
    async with sessions() as session:
        repo = RunRepository(session)
        run = await repo.create_task_run("回滚测试", {"provider": "mock"})
        run_id = run.id
        version = run_event_broker.subscribe(run_id)

        await repo.add_event(run_id, "test.rolled_back", {})
        await session.rollback()

        unchanged = await run_event_broker.wait_for_change(
            run_id,
            version,
            timeout=0.005,
        )
        assert unchanged == version
        run_event_broker.unsubscribe(run_id)
    await engine.dispose()
