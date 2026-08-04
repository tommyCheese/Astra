"""Single route registration point for the composed HTTP application."""

from fastapi import FastAPI

from app.api.conversations import router as conversations_router
from app.api.evolution import router as evolution_router
from app.api.memories import recall_router as memory_recall_router
from app.api.memories import router as memories_router
from app.api.memory_consolidation import router as memory_consolidation_router
from app.api.models import router as models_router
from app.api.permissions import router as permissions_router
from app.api.preferences import router as preferences_router
from app.api.runs import router as runs_router
from app.api.runtime import router as runtime_router
from app.api.schedules import router as schedules_router
from app.api.skills import router as skills_router
from app.api.tools import router as tools_router
from app.api.usage import router as usage_router
from app.platform.http.system import router as system_router

APPLICATION_ROUTERS = (
    runs_router,
    conversations_router,
    evolution_router,
    memories_router,
    memory_recall_router,
    memory_consolidation_router,
    models_router,
    preferences_router,
    permissions_router,
    runtime_router,
    schedules_router,
    tools_router,
    usage_router,
    skills_router,
    system_router,
)


def register_routes(application: FastAPI) -> None:
    for router in APPLICATION_ROUTERS:
        application.include_router(router)
