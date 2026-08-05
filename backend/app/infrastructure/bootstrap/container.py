"""Typed ownership of process-wide Astra application dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.memory.consolidation.service import AutoDreamService
from app.application.run_management.conversation_retention import ConversationRetentionService
from app.application.run_management.dispatcher import InProcessRunDispatcher
from app.application.runner.engine import (
    close_shared_model_http_clients,
    shared_model_http_client,
    shared_tool_registry,
)
from app.application.scheduling.service import SchedulerService
from app.common.core.config import AstraRuntimeSettings
from app.domain.agent_profile import configure_agent_profile_resolver
from app.infrastructure.sandbox.profiles import RuntimeProfileService
from app.infrastructure.tools.base import AstraToolRegistry

ModelHttpClientProvider = Callable[[AstraRuntimeSettings], httpx.AsyncClient | None]
ToolRegistryProvider = Callable[[AstraRuntimeSettings], AstraToolRegistry]
AsyncResourceCloser = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ApplicationContainer:
    settings: AstraRuntimeSettings
    session_factory: async_sessionmaker[AsyncSession]
    runtime_profile_service: RuntimeProfileService
    conversation_retention_service: ConversationRetentionService
    autodream_service: AutoDreamService
    run_dispatcher: InProcessRunDispatcher
    scheduler_service: SchedulerService
    model_http_client_for_settings: ModelHttpClientProvider
    tool_registry_for_settings: ToolRegistryProvider
    close_model_http_clients: AsyncResourceCloser


def build_application_container(
    settings: AstraRuntimeSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> ApplicationContainer:
    runtime_profile_service = RuntimeProfileService(
        settings,
        recover_interrupted=True,
        session_factory=session_factory,
    )
    run_dispatcher = InProcessRunDispatcher()
    configure_agent_profile_resolver(runtime_profile_service.active_agent_profile)
    return ApplicationContainer(
        settings=settings,
        session_factory=session_factory,
        runtime_profile_service=runtime_profile_service,
        conversation_retention_service=ConversationRetentionService(settings, session_factory),
        autodream_service=AutoDreamService(settings, session_factory),
        run_dispatcher=run_dispatcher,
        scheduler_service=SchedulerService(settings, session_factory, run_dispatcher),
        model_http_client_for_settings=shared_model_http_client,
        tool_registry_for_settings=shared_tool_registry,
        close_model_http_clients=close_shared_model_http_clients,
    )
