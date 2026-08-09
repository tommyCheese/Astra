"""Process-scoped provider clients and immutable tool registries."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict

import httpx

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.model_clients.contracts import model_http_client_options
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.registry import build_application_tool_registry

_MODEL_HTTP_CLIENTS: dict[str, httpx.AsyncClient] = {}
_TOOL_REGISTRIES: OrderedDict[str, AstraToolRegistry] = OrderedDict()
MAX_TOOL_REGISTRIES = 16


def shared_model_http_client(settings: AstraRuntimeSettings) -> httpx.AsyncClient | None:
    if settings.model_provider == "mock":
        return None
    endpoint = settings.model_base_url.rstrip("/")
    client = _MODEL_HTTP_CLIENTS.get(endpoint)
    if client is None:
        client = httpx.AsyncClient(**model_http_client_options(settings))
        _MODEL_HTTP_CLIENTS[endpoint] = client
    return client


def shared_tool_registry(settings: AstraRuntimeSettings) -> AstraToolRegistry:
    payload = {name: value for name, value in settings.model_dump(mode="json").items() if not name.startswith("model_")}
    key = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    registry = _TOOL_REGISTRIES.get(key)
    if registry is not None:
        _TOOL_REGISTRIES.move_to_end(key)
        return registry
    registry = build_application_tool_registry(settings)
    _TOOL_REGISTRIES[key] = registry
    if len(_TOOL_REGISTRIES) > MAX_TOOL_REGISTRIES:
        _TOOL_REGISTRIES.popitem(last=False)
    return registry


async def close_shared_model_http_clients() -> None:
    clients = list(_MODEL_HTTP_CLIENTS.values())
    _MODEL_HTTP_CLIENTS.clear()
    _TOOL_REGISTRIES.clear()
    for client in clients:
        await client.aclose()
