"""Typed ownership of process-wide Astra application dependencies."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_profile import configure_agent_profile_resolver
from app.conversation_retention import ConversationRetentionService
from app.core.config import Settings
from app.memory.consolidation.service import AutoDreamService
from app.run_management.dispatcher import InProcessRunDispatcher
from app.runner.engine import (
    close_shared_model_http_clients,
    shared_model_http_client,
    shared_tool_registry,
)
from app.runtime_profiles import RuntimeProfileService
from app.scheduling.service import SchedulerService
from app.tools.base import ToolRegistry

ModelHttpClientProvider = Callable[[Settings], httpx.AsyncClient | None]
ToolRegistryProvider = Callable[[Settings], ToolRegistry]
AsyncResourceCloser = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class ApplicationContainer:
    settings: Settings
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
    settings: Settings,
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
