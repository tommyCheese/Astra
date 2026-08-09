import json

import httpx
import pytest
from pydantic import ValidationError

from app.common.schemas.model_providers import ModelConnectionTestRequest
from app.interfaces.api.model_providers import probe_model_connection


async def test_openai_compatible_connection_probe_sends_current_configuration():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    payload = ModelConnectionTestRequest(
        provider="openai",
        model="gpt-test",
        api_key="secret",
        base_url="https://models.example/v1/",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_model_connection(payload, client=client)

    assert result.connected is True
    assert result.latency_ms is not None
    assert captured == {
        "url": "https://models.example/v1/chat/completions",
        "authorization": "Bearer secret",
        "body": {
            "model": "gpt-test",
            "messages": [{"role": "user", "content": "Reply with exactly OK."}],
            "stream": False,
        },
    }


async def test_anthropic_connection_probe_uses_native_messages_api():
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("x-api-key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "OK"}]})

    payload = ModelConnectionTestRequest(
        provider="anthropic",
        model="claude-test",
        api_key="anthropic-secret",
        base_url="https://api.anthropic.test/v1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_model_connection(payload, client=client)

    assert result.connected is True
    assert captured["url"] == "https://api.anthropic.test/v1/messages"
    assert captured["api_key"] == "anthropic-secret"
    assert captured["body"] == {
        "model": "claude-test",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }


async def test_connection_probe_returns_safe_authentication_failure():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="upstream secret diagnostic")

    payload = ModelConnectionTestRequest(
        provider="openai",
        model="gpt-test",
        api_key="bad-key",
        base_url="https://models.example/v1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await probe_model_connection(payload, client=client)

    assert result.connected is False
    assert result.error_code == "authentication_failed"
    assert "upstream secret diagnostic" not in result.message


async def test_connection_probe_rejects_missing_required_api_key_without_network():
    payload = ModelConnectionTestRequest(
        provider="openai",
        model="gpt-test",
        base_url="https://models.example/v1",
    )

    result = await probe_model_connection(payload)

    assert result.connected is False
    assert result.error_code == "api_key_required"


def test_connection_request_rejects_invalid_base_url():
    with pytest.raises(ValidationError):
        ModelConnectionTestRequest(
            provider="openai",
            model="gpt-test",
            api_key="secret",
            base_url="file:///tmp/model",
        )
