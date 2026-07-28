import json
from typing import ClassVar

import pytest

from app.agent_profile import ModelOperation
from app.core.config import Settings
from app.runner.engine import close_shared_model_http_clients, shared_model_http_client
from app.runner.model_client import AnthropicModelClient, OpenAICompatibleModelClient


class RecordingUsage:
    def __init__(self):
        self.finished = []

    async def start(self, **kwargs):
        return "invocation-1"

    async def finish(self, invocation_id, **kwargs):
        self.finished.append((invocation_id, kwargs))


class FakeOpenAIResponse:
    headers: ClassVar[dict[str, str]] = {
        "content-type": "application/json",
        "x-request-id": "request-1",
    }
    status_code = 200

    def raise_for_status(self):
        return None

    async def aread(self):
        return json.dumps(
            {
                "choices": [{"message": {"content": '{"summary":"完成"}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11},
            }
        ).encode()


class FakeStreamContext:
    async def __aenter__(self):
        return FakeOpenAIResponse()

    async def __aexit__(self, *args):
        return None


class FakeOpenAIAsyncClient:
    requests: ClassVar[list] = []
    instances: ClassVar[int] = 0
    closes: ClassVar[int] = 0

    def __init__(self, **kwargs):
        self.__class__.instances += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aclose(self):
        self.__class__.closes += 1

    def stream(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return FakeStreamContext()


@pytest.mark.parametrize(
    ("provider", "model", "effort", "expected", "has_json_mode"),
    [
        ("openai", "gpt-5", "fast", {"reasoning_effort": "minimal"}, True),
        (
            "qwen",
            "qwen3.7-plus",
            "deep",
            {"enable_thinking": True, "thinking_budget": 8192},
            False,
        ),
        ("deepseek", "deepseek-reasoner", "deep", {}, True),
    ],
)
async def test_openai_compatible_transport_applies_supported_reasoning_fields(
    monkeypatch, provider, model, effort, expected, has_json_mode
):
    FakeOpenAIAsyncClient.requests = []
    monkeypatch.setattr("app.runner.model_client.httpx.AsyncClient", FakeOpenAIAsyncClient)
    client = OpenAICompatibleModelClient(
        Settings(model_provider=provider, model_name=model, model_api_key="secret")
    )
    client.bind_reasoning_effort(effort)
    usage = RecordingUsage()
    client.usage_recorder = usage

    payload = await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.SYNTHESIS,
    )

    request = FakeOpenAIAsyncClient.requests[0][2]["json"]
    assert payload == {"summary": "完成"}
    assert {key: request[key] for key in expected} == expected
    assert ("response_format" in request) is has_json_mode
    metadata = usage.finished[0][1]["usage"]["astra_reasoning"]
    assert metadata["applied"] is bool(expected)
    assert metadata["request_params"] == expected


async def test_openai_compatible_transport_reuses_connection_pool(monkeypatch):
    FakeOpenAIAsyncClient.requests = []
    FakeOpenAIAsyncClient.instances = 0
    FakeOpenAIAsyncClient.closes = 0
    monkeypatch.setattr("app.runner.model_client.httpx.AsyncClient", FakeOpenAIAsyncClient)
    client = OpenAICompatibleModelClient(
        Settings(model_provider="openai", model_name="gpt-5", model_api_key="secret")
    )

    for _ in range(2):
        await client._chat_json(
            [{"role": "user", "content": "返回 JSON"}],
            operation=ModelOperation.SYNTHESIS,
        )
    await client.aclose()

    assert FakeOpenAIAsyncClient.instances == 1
    assert len(FakeOpenAIAsyncClient.requests) == 2
    assert FakeOpenAIAsyncClient.closes == 1


async def test_server_reuses_model_connections_across_runs(monkeypatch):
    await close_shared_model_http_clients()
    FakeOpenAIAsyncClient.instances = 0
    FakeOpenAIAsyncClient.closes = 0
    monkeypatch.setattr("app.runner.engine.httpx.AsyncClient", FakeOpenAIAsyncClient)
    settings = Settings(
        model_provider="openai",
        model_name="gpt-5",
        model_api_key="secret",
        model_base_url="https://api.openai.test/v1",
    )

    first = shared_model_http_client(settings)
    second = shared_model_http_client(settings)

    assert first is second
    assert FakeOpenAIAsyncClient.instances == 1
    await close_shared_model_http_clients()
    assert FakeOpenAIAsyncClient.closes == 1


async def test_anthropic_transport_applies_output_config_effort(monkeypatch):
    requests = []

    class FakeResponse:
        headers: ClassVar[dict[str, str]] = {"request-id": "request-1"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "content": [{"type": "text", "text": '{"summary":"完成"}'}],
                "usage": {"input_tokens": 8, "output_tokens": 3},
            }

    class FakeAnthropicAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr("app.runner.model_client.httpx.AsyncClient", FakeAnthropicAsyncClient)
    client = AnthropicModelClient(
        Settings(
            model_provider="anthropic",
            model_name="claude-sonnet-4-6",
            model_api_key="secret",
        )
    )
    client.bind_reasoning_effort("balanced")
    usage = RecordingUsage()
    client.usage_recorder = usage

    await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.CONTRACT,
    )

    assert requests[0]["output_config"] == {"effort": "medium"}
    assert usage.finished[0][1]["usage"]["astra_reasoning"]["adapter"] == "anthropic-effort"
