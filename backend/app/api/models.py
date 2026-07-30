from fastapi import APIRouter, Depends

from app.conversation_context import resolve_context_window
from app.core.config import Settings, get_settings
from app.runner.model_reasoning import model_thinking_capability
from app.schemas.models import (
    ModelContextCapabilitiesRequest,
    ModelContextCapabilitiesResponse,
    ModelContextCapability,
    ModelThinkingCapabilitiesRequest,
    ModelThinkingCapabilitiesResponse,
)

router = APIRouter(prefix="/api/models", tags=["models"])


@router.post(
    "/thinking-capabilities/resolve",
    response_model=ModelThinkingCapabilitiesResponse,
)
async def resolve_thinking_capabilities(
    payload: ModelThinkingCapabilitiesRequest,
) -> ModelThinkingCapabilitiesResponse:
    return ModelThinkingCapabilitiesResponse(
        capabilities=[
            model_thinking_capability(provider=item.provider, model=item.model)
            for item in payload.models
        ]
    )


@router.post(
    "/context-capabilities/resolve",
    response_model=ModelContextCapabilitiesResponse,
)
async def resolve_context_capabilities(
    payload: ModelContextCapabilitiesRequest,
    settings: Settings = Depends(get_settings),
) -> ModelContextCapabilitiesResponse:
    capabilities: list[ModelContextCapability] = []
    for item in payload.models:
        window = resolve_context_window(
            item.provider,
            item.model,
            fallback_tokens=settings.context_window_fallback_tokens,
        )
        capabilities.append(
            ModelContextCapability(
                provider=item.provider,
                model=item.model,
                window_tokens=window.tokens,
                max_output_tokens=window.max_output_tokens,
                source=window.source,
                verified=window.verified,
                documentation_url=window.documentation_url,
            )
        )
    return ModelContextCapabilitiesResponse(capabilities=capabilities)
