from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.errors import StateError, ValidationError
from app.runtime_profiles import RuntimeProfileService

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


def get_runtime_profile_service(request: Request) -> RuntimeProfileService:
    return request.app.state.runtime_profile_service


class Dependency(BaseModel):
    name: str = Field(max_length=64)
    version: str = Field(default="", max_length=64)


class BuildRequest(BaseModel):
    dependencies: list[Dependency] = Field(max_length=32)


class CancelBuildRequest(BaseModel):
    build_id: str


@router.get("")
async def get_runtime(service: RuntimeProfileService = Depends(get_runtime_profile_service)):
    return service.read()


@router.post("/build")
async def build_runtime(
    request: BuildRequest,
    service: RuntimeProfileService = Depends(get_runtime_profile_service),
):
    try:
        return await service.start([item.model_dump() for item in request.dependencies])
    except ValueError as exc:
        raise ValidationError("RUNTIME_DEPENDENCY_INVALID", str(exc)) from exc
    except RuntimeError as exc:
        raise StateError("RUNTIME_BUILD_IN_PROGRESS", str(exc)) from exc


@router.post("/build/cancel")
async def cancel_runtime_build(
    request: CancelBuildRequest,
    service: RuntimeProfileService = Depends(get_runtime_profile_service),
):
    try:
        return await service.cancel(request.build_id)
    except RuntimeError as exc:
        raise StateError("RUNTIME_BUILD_NOT_CANCELLABLE", str(exc)) from exc
