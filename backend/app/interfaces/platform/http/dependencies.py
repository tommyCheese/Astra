"""Typed FastAPI dependencies for process-wide application services."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.memory.consolidation.service import AutoDreamService
from app.application.run_management.lifecycle.contracts import RunExecutionDispatcher
from app.application.scheduling.service import SchedulerService
from app.infrastructure.sandbox.profiles import RuntimeProfileService


class AstraApplicationServices(Protocol):
    session_factory: async_sessionmaker[AsyncSession]
    runtime_profile_service: RuntimeProfileService
    autodream_service: AutoDreamService
    run_dispatcher: RunExecutionDispatcher
    scheduler_service: SchedulerService


def get_application_container(request: Request) -> AstraApplicationServices:
    return request.app.state.container
