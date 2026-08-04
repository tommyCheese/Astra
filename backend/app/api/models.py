import time

import httpx
from fastapi import APIRouter, Depends

from app.context_windows import resolve_context_window
from app.core.config import Settings, get_settings
from app.model_providers import API_KEY_OPTIONAL_MODEL_PROVIDERS, SUPPORTED_MODEL_PROVIDERS
from app.runner.model_reasoning import model_thinking_capability
from app.schemas.models import (
    ModelConnectionTestRequest,
    ModelConnectionTestResponse,
    ModelContextCapabilitiesRequest,
    ModelContextCapabilitiesResponse,
    ModelContextCapability,
    ModelThinkingCapabilitiesRequest,
    ModelThinkingCapabilitiesResponse,
    RuntimeDefaultModelResponse,
)

router = APIRouter(prefix="/api/models", tags=["models"])


def _connection_test_request(
    payload: ModelConnectionTestRequest,
) -> tuple[str, dict[str, str], dict[str, object]]:
    if payload.provider == "anthropic":
        return (
            f"{payload.base_url}/messages",
            {
                "x-api-key": payload.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": payload.model,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            },
        )
    headers = {"content-type": "application/json"}
    if payload.api_key:
        headers["authorization"] = f"Bearer {payload.api_key}"
    return (
        f"{payload.base_url}/chat/completions",
        headers,
        {
            "model": payload.model,
            "messages": [{"role": "user", "content": "Reply with exactly OK."}],
            "stream": False,
        },
    )


def _connection_error_message(status_code: int) -> tuple[str, str]:
    if status_code in {401, 403}:
        return "authentication_failed", "连接失败：API Key 无效或无权访问该模型。"
    if status_code == 404:
        return "not_found", "连接失败：API 地址或模型 ID 不存在。"
    if status_code == 429:
        return "rate_limited", "连接失败：供应商限流或账户配额不足。"
    if status_code >= 500:
        return "provider_unavailable", "连接失败：供应商服务暂时不可用。"
    return "request_rejected", "连接失败：供应商拒绝了测试请求，请检查模型 ID 与 API 地址。"


async def probe_model_connection(
    payload: ModelConnectionTestRequest,
    *,
    client: httpx.AsyncClient | None = None,
) -> ModelConnectionTestResponse:
    if payload.provider not in SUPPORTED_MODEL_PROVIDERS:
        return ModelConnectionTestResponse(
            connected=False,
            provider=payload.provider,
            model=payload.model,
            message="连接失败：当前模型供应商尚未接入通用运行时。",
            error_code="provider_unsupported",
        )
    if payload.provider not in API_KEY_OPTIONAL_MODEL_PROVIDERS and not payload.api_key:
        return ModelConnectionTestResponse(
            connected=False,
            provider=payload.provider,
            model=payload.model,
            message="连接失败：请先填写 API Key。",
            error_code="api_key_required",
        )

    url, headers, request_payload = _connection_test_request(payload)
    started = time.perf_counter()
    owns_client = client is None
    if client is None:
        timeout = httpx.Timeout(15.0, connect=10.0)
        client = httpx.AsyncClient(timeout=timeout)
    try:
        response = await client.post(url, headers=headers, json=request_payload)
        latency_ms = max(1, round((time.perf_counter() - started) * 1000))
        if not response.is_success:
            error_code, message = _connection_error_message(response.status_code)
            return ModelConnectionTestResponse(
                connected=False,
                provider=payload.provider,
                model=payload.model,
                message=message,
                latency_ms=latency_ms,
                error_code=error_code,
            )
        return ModelConnectionTestResponse(
            connected=True,
            provider=payload.provider,
            model=payload.model,
            message="连接成功，模型已响应测试请求。",
            latency_ms=latency_ms,
        )
    except httpx.TimeoutException:
        return ModelConnectionTestResponse(
            connected=False,
            provider=payload.provider,
            model=payload.model,
            message="连接超时，请检查 API 地址或网络后重试。",
            error_code="timeout",
        )
    except httpx.RequestError:
        return ModelConnectionTestResponse(
            connected=False,
            provider=payload.provider,
            model=payload.model,
            message="无法连接模型服务，请检查 API 地址和网络。",
            error_code="network_error",
        )
    finally:
        if owns_client:
            await client.aclose()


@router.get("/default", response_model=RuntimeDefaultModelResponse)
async def get_runtime_default_model(
    settings: Settings = Depends(get_settings),
) -> RuntimeDefaultModelResponse:
    return RuntimeDefaultModelResponse(
        provider=settings.model_provider,
        model=settings.model_name,
        configured=(
            settings.model_provider == "mock"
            or (
                settings.model_provider in SUPPORTED_MODEL_PROVIDERS
                and bool(settings.model_name.strip())
                and bool(settings.model_base_url.strip())
                and (
                    settings.model_provider in API_KEY_OPTIONAL_MODEL_PROVIDERS
                    or bool(settings.model_api_key.strip())
                )
            )
        ),
    )


@router.post("/test-connection", response_model=ModelConnectionTestResponse)
async def test_model_connection(
    payload: ModelConnectionTestRequest,
) -> ModelConnectionTestResponse:
    return await probe_model_connection(payload)


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
