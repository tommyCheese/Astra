import pytest

from app.infrastructure.bootstrap.lifecycle import LifecycleCoordinator


class RecordingService:
    def __init__(self, name: str, events: list[str], *, fail_startup: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_startup = fail_startup

    async def startup(self) -> None:
        self.events.append(f"{self.name}.startup")
        if self.fail_startup:
            raise RuntimeError(f"{self.name} failed")

    async def shutdown(self) -> None:
        self.events.append(f"{self.name}.shutdown")


def build_coordinator(
    events: list[str], services: tuple[RecordingService, ...]
) -> LifecycleCoordinator:
    def prepare_process_resources() -> None:
        events.append("process.prepare")

    async def initialize_persistence() -> None:
        events.append("persistence.initialize")

    async def close_process_resources() -> None:
        events.append("process.close")

    return LifecycleCoordinator(
        prepare_process_resources=prepare_process_resources,
        initialize_persistence=initialize_persistence,
        services=services,
        close_process_resources=close_process_resources,
    )


async def test_lifecycle_starts_in_dependency_order_and_stops_in_reverse_order():
    events: list[str] = []
    runtime = RecordingService("runtime", events)
    retention = RecordingService("retention", events)
    autodream = RecordingService("autodream", events)
    scheduler = RecordingService("scheduler", events)
    coordinator = build_coordinator(events, (runtime, retention, autodream, scheduler))

    await coordinator.startup()
    await coordinator.shutdown()

    assert events == [
        "process.prepare",
        "runtime.startup",
        "persistence.initialize",
        "retention.startup",
        "autodream.startup",
        "scheduler.startup",
        "scheduler.shutdown",
        "autodream.shutdown",
        "retention.shutdown",
        "runtime.shutdown",
        "process.close",
    ]


async def test_lifecycle_cleans_only_successfully_started_services_after_failure():
    events: list[str] = []
    runtime = RecordingService("runtime", events)
    retention = RecordingService("retention", events)
    autodream = RecordingService("autodream", events, fail_startup=True)
    scheduler = RecordingService("scheduler", events)
    coordinator = build_coordinator(events, (runtime, retention, autodream, scheduler))

    with pytest.raises(RuntimeError, match="autodream failed"):
        await coordinator.startup()

    assert events == [
        "process.prepare",
        "runtime.startup",
        "persistence.initialize",
        "retention.startup",
        "autodream.startup",
        "retention.shutdown",
        "runtime.shutdown",
        "process.close",
    ]
