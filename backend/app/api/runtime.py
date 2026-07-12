from fastapi import APIRouter
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.core.errors import StateError, ValidationError
from app.runtime_profiles import RuntimeProfileService

router = APIRouter(prefix="/api/runtime", tags=["runtime"])
service = RuntimeProfileService(get_settings())

class Dependency(BaseModel):
    name: str = Field(max_length=64)
    version: str = Field(default="", max_length=64)

class BuildRequest(BaseModel):
    dependencies: list[Dependency] = Field(max_length=32)

@router.get("")
async def get_runtime(): return service.read()

@router.post("/build")
async def build_runtime(request: BuildRequest):
    try:
        return await service.start([item.model_dump() for item in request.dependencies])
    except ValueError as exc:
        raise ValidationError("RUNTIME_DEPENDENCY_INVALID", str(exc)) from exc
    except RuntimeError as exc:
        raise StateError("RUNTIME_BUILD_IN_PROGRESS", str(exc)) from exc
