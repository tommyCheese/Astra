"""Typed FastAPI dependencies for process-wide application services."""

from __future__ import annotations

from typing import Protocol

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.memory.autodream import AutoDreamService
from app.run_management.contracts import RunDispatcher
from app.runtime_profiles import RuntimeProfileService
from app.scheduling.service import SchedulerService


class ApplicationServices(Protocol):
    session_factory: async_sessionmaker[AsyncSession]
    runtime_profile_service: RuntimeProfileService
    autodream_service: AutoDreamService
    run_dispatcher: RunDispatcher
    scheduler_service: SchedulerService


def get_application_container(request: Request) -> ApplicationServices:
    return request.app.state.container
