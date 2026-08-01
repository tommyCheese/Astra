from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agent_profile import AgentProfileConfigurationError
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


class AgentProfileDocuments(BaseModel):
    identity: str = Field(max_length=16_384)
    soul: str = Field(max_length=16_384)
    memory: str = Field(max_length=16_384)
    autodream: str = Field(max_length=16_384)


class AgentProfileUpdateRequest(BaseModel):
    documents: AgentProfileDocuments


class MemorySettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    write_enabled: bool
    recall_enabled: bool
    retrieval_max_items: int = Field(ge=0, le=50)
    retrieval_max_tokens: int = Field(ge=0, le=32_000)
    retrieval_min_confidence: float = Field(ge=0.0, le=1.0)
    retrieval_min_score: float = Field(ge=0.0, le=1.0)
    autodream_enabled: bool
    autodream_scan_seconds: int = Field(ge=60, le=604_800)
    autodream_min_candidates: int = Field(ge=2, le=100)


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


@router.put("/agent-profile")
async def update_runtime_agent_profile(
    request: AgentProfileUpdateRequest,
    service: RuntimeProfileService = Depends(get_runtime_profile_service),
):
    try:
        return service.update_agent_profile(request.documents.model_dump())
    except AgentProfileConfigurationError as exc:
        raise ValidationError("AGENT_PROFILE_INVALID", str(exc)) from exc


@router.post("/agent-profile/reset")
async def reset_runtime_agent_profile(
    service: RuntimeProfileService = Depends(get_runtime_profile_service),
):
    return service.reset_agent_profile()


@router.put("/memory-settings")
async def update_runtime_memory_settings(
    payload: MemorySettingsUpdateRequest,
    request: Request,
    service: RuntimeProfileService = Depends(get_runtime_profile_service),
):
    previous = service.memory_settings()
    try:
        updated = service.update_memory_settings(payload.model_dump())
    except ValueError as exc:
        raise ValidationError("MEMORY_SETTINGS_INVALID", str(exc)) from exc
    try:
        autodream = request.app.state.autodream_service
        if updated["autodream_enabled"] and not previous["autodream_enabled"]:
            await autodream.startup()
        elif previous["autodream_enabled"] and not updated["autodream_enabled"]:
            await autodream.shutdown()
    except Exception as exc:
        service.update_memory_settings(previous)
        raise StateError(
            "MEMORY_SETTINGS_APPLY_FAILED",
            "记忆设置已恢复，AutoDream 状态切换失败。",
        ) from exc
    return updated
