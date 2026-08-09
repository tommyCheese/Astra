"""Ordered startup and failure-safe shutdown for process-wide services."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import FastAPI

from app.application.skills.builtin_catalog import ensure_builtin_skills
from app.infrastructure.bootstrap.container import ApplicationContainer
from app.infrastructure.repositories.tool_settings import (
    ToolProviderSettingsRepository,
    ToolSettingsRepository,
    apply_provider_states,
    apply_tool_states,
    default_tool_states,
)
from app.infrastructure.repositories.usage import UsageRepository

logger = logging.getLogger("astra.bootstrap")


class ManagedLifecycle(Protocol):
    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


@dataclass
class LifecycleCoordinator:
    prepare_process_resources: Callable[[], None]
    initialize_persistence: Callable[[], Awaitable[None]]
    services: Sequence[ManagedLifecycle]
    close_process_resources: Callable[[], Awaitable[None]]
    _started_services: list[ManagedLifecycle] = field(default_factory=list, init=False)
    _process_resources_prepared: bool = field(default=False, init=False)

    async def startup(self) -> None:
        self.prepare_process_resources()
        self._process_resources_prepared = True
        try:
            first_service, *remaining_services = self.services
            await self._start_service(first_service)
            await self.initialize_persistence()
            for service in remaining_services:
                await self._start_service(service)
        except Exception:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        while self._started_services:
            service = self._started_services.pop()
            try:
                await service.shutdown()
            except Exception:
                logger.exception("application.service_shutdown_failed service=%s", type(service).__name__)
        if self._process_resources_prepared:
            self._process_resources_prepared = False
            await self.close_process_resources()

    async def _start_service(self, service: ManagedLifecycle) -> None:
        await service.startup()
        self._started_services.append(service)


async def initialize_persistence(container: ApplicationContainer) -> None:
    async with container.session_factory() as session:
        await ensure_builtin_skills(session, container.settings)
        tool_states = await ToolSettingsRepository(session).get_or_create(default_tool_states(container.settings))
        provider_states = await ToolProviderSettingsRepository(session).get_or_create(
            dict.fromkeys(container.settings.trusted_tool_provider_map, True)
        )
        container.tool_registry_for_settings(
            apply_provider_states(
                apply_tool_states(container.settings, tool_states),
                provider_states,
            )
        )
        await session.commit()
        interrupted_count = await UsageRepository(session).reconcile_interrupted()
        if interrupted_count:
            logger.warning("usage.reconciled_interrupted count=%s", interrupted_count)


def build_lifecycle_coordinator(container: ApplicationContainer) -> LifecycleCoordinator:
    return LifecycleCoordinator(
        prepare_process_resources=lambda: container.model_http_client_for_settings(container.settings),
        initialize_persistence=lambda: initialize_persistence(container),
        services=(
            container.runtime_profile_service,
            container.conversation_retention_service,
            container.autodream_service,
            container.run_dispatcher,
            container.scheduler_service,
        ),
        close_process_resources=container.close_model_http_clients,
    )


def create_application_lifespan(
    container: ApplicationContainer,
) -> Callable[[FastAPI], AsyncIterator[None]]:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        coordinator = build_lifecycle_coordinator(container)
        await coordinator.startup()
        try:
            yield
        finally:
            await coordinator.shutdown()

    return lifespan
