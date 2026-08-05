import logging

import httpx

from app.common.core.config import Settings
from app.infrastructure.model_clients.anthropic import AnthropicModelClient
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.model_clients.openai_compatible import OpenAICompatibleModelClient

logger = logging.getLogger("astra.model")


def build_model_client(
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ModelClient:
    if settings.model_provider == "mock":
        return MockModelClient()
    if settings.model_provider == "anthropic":
        return AnthropicModelClient(settings, http_client=http_client)
    return OpenAICompatibleModelClient(settings, http_client=http_client)
