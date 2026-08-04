"""Process health endpoints backed by the typed application container."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.platform.http.dependencies import ApplicationServices, get_application_container

router = APIRouter()
ApplicationDependencies = Annotated[ApplicationServices, Depends(get_application_container)]


@router.get("/api/health")
async def health(container: ApplicationDependencies) -> dict[str, Any]:
    return {
        "status": "ok",
        "scheduler": container.scheduler_service.health(),
    }


@router.get("/api/ready")
async def readiness(container: ApplicationDependencies) -> JSONResponse:
    scheduler_health = container.scheduler_service.health()
    response_payload = {
        "status": "ready" if scheduler_health["ready"] else "not_ready",
        "scheduler": scheduler_health,
    }
    return JSONResponse(
        status_code=200 if scheduler_health["ready"] else 503,
        content=response_payload,
    )
