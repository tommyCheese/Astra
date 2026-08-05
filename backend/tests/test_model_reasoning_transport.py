import json
from typing import ClassVar

import pytest

from app.application.runner.engine import (
    close_shared_model_http_clients,
    shared_model_http_client,
    shared_tool_registry,
)
from app.common.core.config import AstraRuntimeSettings
from app.domain.agent_profile import ModelOperation
from app.infrastructure.model_clients.anthropic import AnthropicModelClient
from app.infrastructure.model_clients.openai_compatible import OpenAICompatibleModelClient
from app.infrastructure.tools.base import AstraToolRegistry


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
    options: ClassVar[list] = []
    instances: ClassVar[int] = 0
    closes: ClassVar[int] = 0

    def __init__(self, **kwargs):
        self.__class__.instances += 1
        self.__class__.options.append(kwargs)

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
        ("openai", "gpt-5", "fast", {"reasoning_effort": "medium"}, True),
        (
            "qwen",
            "qwen3.7-plus",
            "deep",
            {"enable_thinking": True, "thinking_budget": 2048},
            False,
        ),
        ("deepseek", "deepseek-reasoner", "deep", {}, True),
    ],
)
async def test_openai_compatible_transport_applies_supported_reasoning_fields(
    monkeypatch, provider, model, effort, expected, has_json_mode
):
    FakeOpenAIAsyncClient.requests = []
    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeOpenAIAsyncClient)
    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider=provider, model_name=model, model_api_key="secret")
    )
    client.bind_reasoning_effort(effort)
    usage = RecordingUsage()
    client.usage_recorder = usage

    payload = await client._chat_json(
        [
            {"role": "system", "content": "Return JSON"},
            {"role": "user", "content": "返回 JSON"},
        ],
        operation=ModelOperation.SYNTHESIS,
    )

    request = FakeOpenAIAsyncClient.requests[0][2]["json"]
    assert payload == {"summary": "完成"}
    assert {key: request[key] for key in expected} == expected
    assert ("response_format" in request) is has_json_mode
    assert ("prompt_cache_key" in request) is (provider == "openai")
    metadata = usage.finished[0][1]["usage"]["astra_reasoning"]
    assert metadata["applied"] is bool(expected)
    assert metadata["request_params"] == expected


async def test_explicit_model_thinking_depth_overrides_agent_effort_in_transport(
    monkeypatch,
):
    FakeOpenAIAsyncClient.requests = []
    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeOpenAIAsyncClient)
    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(
            model_provider="openai",
            model_name="gpt-5.2",
            model_api_key="secret",
        )
    )
    client.bind_reasoning_effort("deep")
    client.bind_model_thinking(
        {
            "requested": {
                "enabled": True,
                "depth": "low",
                "capability_version": 1,
            },
            "effective": {"enabled": True, "depth": "low"},
            "source": "explicit_model_control",
            "adapter": "openai-gpt5-modern",
            "adjustments": [],
            "capability_version": 1,
        }
    )
    usage = RecordingUsage()
    client.usage_recorder = usage

    await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.SYNTHESIS,
    )

    request = FakeOpenAIAsyncClient.requests[0][2]["json"]
    assert request["reasoning_effort"] == "low"
    metadata = usage.finished[0][1]["usage"]["astra_reasoning"]
    assert metadata["effort"] == "deep"
    assert metadata["depth"] == "low"
    assert metadata["source"] == "explicit_model_control"


async def test_openai_compatible_transport_reuses_connection_pool(monkeypatch):
    FakeOpenAIAsyncClient.requests = []
    FakeOpenAIAsyncClient.instances = 0
    FakeOpenAIAsyncClient.closes = 0
    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeOpenAIAsyncClient)
    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider="openai", model_name="gpt-5", model_api_key="secret")
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


async def test_openai_prompt_cache_key_tracks_only_the_static_system_prefix(
    monkeypatch,
):
    FakeOpenAIAsyncClient.requests = []
    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeOpenAIAsyncClient)
    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider="openai", model_name="gpt-5", model_api_key="secret")
    )

    for system, user in (
        ("stable", "first"),
        ("stable", "second"),
        ("changed", "third"),
    ):
        await client._chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            operation=ModelOperation.SYNTHESIS,
        )

    keys = [
        request[2]["json"]["prompt_cache_key"]
        for request in FakeOpenAIAsyncClient.requests
    ]
    assert keys[0] == keys[1]
    assert keys[2] != keys[0]


async def test_openai_stream_decodes_multiple_fields_across_chunk_boundaries():
    content = (
        '{"decision_type":"finalize","reasoning_summary":"先检查",'
        '"final_answer":{"summary":"流式\\n回答"}}'
    )

    class StreamingResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}
        status_code = 200

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for index in range(0, len(content), 3):
                event = {"choices": [{"delta": {"content": content[index : index + 3]}}]}
                yield f"data: {json.dumps(event, ensure_ascii=False)}"
            yield "data: [DONE]"

    class StreamingContext:
        async def __aenter__(self):
            return StreamingResponse()

        async def __aexit__(self, *args):
            return None

    class StreamingClient:
        def stream(self, method, url, **kwargs):
            return StreamingContext()

    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider="openai", model_name="gpt-5", model_api_key="secret"),
        http_client=StreamingClient(),
    )
    reasoning = []
    answer = []

    async def capture_reasoning(value):
        reasoning.append(value)

    async def capture_answer(value):
        answer.append(value)

    payload = await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.DECISION_WITH_ANSWER,
        stream_callbacks={
            "reasoning_summary": capture_reasoning,
            "summary": capture_answer,
        },
    )

    assert payload["final_answer"]["summary"] == "流式\n回答"
    assert "".join(reasoning[:-1]) == "先检查"
    assert "".join(answer[:-1]) == "流式\n回答"
    assert reasoning[-1] == answer[-1] == "\1"


async def test_anthropic_stream_delivers_answer_before_response_completes():
    content = '{"summary":"即时回答","decision_type":"finalize","reasoning_summary":"完成"}'
    emitted = 0
    requests = []

    class StreamingResponse:
        headers: ClassVar[dict[str, str]] = {"request-id": "request-1"}

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            nonlocal emitted
            yield 'data: {"type":"message_start","message":{"usage":{"input_tokens":8}}}'
            for index in range(0, len(content), 3):
                emitted += 1
                event = {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "text_delta",
                        "text": content[index : index + 3],
                    },
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}"
            yield 'data: {"type":"message_delta","usage":{"output_tokens":16}}'
            yield 'data: {"type":"message_stop"}'

    class StreamingContext:
        async def __aenter__(self):
            return StreamingResponse()

        async def __aexit__(self, *args):
            return None

    class StreamingClient:
        def stream(self, method, url, **kwargs):
            requests.append(kwargs["json"])
            return StreamingContext()

    client = AnthropicModelClient(
        AstraRuntimeSettings(
            model_provider="anthropic",
            model_name="claude-sonnet-4-6",
            model_api_key="secret",
        ),
        http_client=StreamingClient(),
    )
    answer = []
    first_answer_chunk = None

    async def capture_answer(value):
        nonlocal first_answer_chunk
        answer.append(value)
        if value != "\1" and first_answer_chunk is None:
            first_answer_chunk = emitted

    payload = await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.DECISION_WITH_ANSWER,
        stream_callbacks={"summary": capture_answer},
    )

    assert payload["summary"] == "即时回答"
    assert "".join(answer[:-1]) == "即时回答"
    assert answer[-1] == "\1"
    assert first_answer_chunk is not None and first_answer_chunk < emitted
    assert requests[0]["stream"] is True


async def test_openai_compatible_stream_forwards_reasoning_content_separately():
    content = '{"summary":"完成"}'

    class StreamingResponse:
        headers: ClassVar[dict[str, str]] = {"content-type": "text/event-stream"}
        status_code = 200

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for value in ("先分析\n", "再验证"):
                yield "data: " + json.dumps(
                    {"choices": [{"delta": {"reasoning_content": value, "content": ""}}]},
                    ensure_ascii=False,
                )
            yield "data: " + json.dumps(
                {"choices": [{"delta": {"reasoning_content": "", "content": content}}]},
                ensure_ascii=False,
            )
            yield "data: [DONE]"

    class StreamingContext:
        async def __aenter__(self):
            return StreamingResponse()

        async def __aexit__(self, *args):
            return None

    class StreamingClient:
        def stream(self, method, url, **kwargs):
            return StreamingContext()

    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider="qwen", model_name="qwen3.7-plus", model_api_key="secret"),
        http_client=StreamingClient(),
    )
    observed = []

    async def observe(event):
        observed.append(event)

    client.bind_model_thinking_observer(observe)
    payload = await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.SYNTHESIS,
    )

    assert payload == {"summary": "完成"}
    assert [event["phase"] for event in observed] == ["started", "delta", "delta", "completed"]
    assert "".join(event.get("delta", "") for event in observed) == "先分析\n再验证"
    assert {event["content_level"] for event in observed} == {"reasoning"}
    assert all("content" not in event for event in observed)


async def test_anthropic_stream_forwards_only_visible_thinking_deltas():
    content = '{"summary":"完成"}'
    requests = []

    class StreamingResponse:
        headers: ClassVar[dict[str, str]] = {"request-id": "request-1"}

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"检查假设\\n"}}'
            yield 'data: {"type":"content_block_delta","delta":{"type":"signature_delta","signature":"secret-signature"}}'
            yield 'data: {"type":"content_block_start","content_block":{"type":"redacted_thinking","data":"secret-redacted"}}'
            yield "data: " + json.dumps(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": content}},
                ensure_ascii=False,
            )

    class StreamingContext:
        async def __aenter__(self):
            return StreamingResponse()

        async def __aexit__(self, *args):
            return None

    class StreamingClient:
        def stream(self, method, url, **kwargs):
            requests.append(kwargs["json"])
            return StreamingContext()

    client = AnthropicModelClient(
        AstraRuntimeSettings(model_provider="anthropic", model_name="claude-sonnet-4-6", model_api_key="secret"),
        http_client=StreamingClient(),
    )
    client.bind_model_thinking(
        {
            "requested": {"enabled": True, "depth": "high", "capability_version": 2},
            "effective": {"enabled": True, "depth": "high"},
            "source": "explicit_model_control",
            "adapter": "anthropic-adaptive-thinking",
            "adjustments": [],
            "capability_version": 2,
        }
    )
    observed = []

    async def observe(event):
        observed.append(event)

    client.bind_model_thinking_observer(observe)
    payload = await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}],
        operation=ModelOperation.SYNTHESIS,
    )

    assert payload == {"summary": "完成"}
    assert requests[0]["thinking"]["display"] == "summarized"
    assert "".join(event.get("delta", "") for event in observed) == "检查假设\n"
    assert {event["content_level"] for event in observed} == {"summary"}
    assert "secret-signature" not in json.dumps(observed)
    assert "secret-redacted" not in json.dumps(observed)


async def test_disabled_model_thinking_does_not_forward_provider_reasoning_content():
    class JsonResponse(FakeOpenAIResponse):
        async def aread(self):
            return json.dumps(
                {
                    "choices": [{"message": {"content": '{"summary":"完成"}', "reasoning_content": "不应展示"}}],
                    "usage": {},
                },
                ensure_ascii=False,
            ).encode()

    class JsonContext:
        async def __aenter__(self):
            return JsonResponse()

        async def __aexit__(self, *args):
            return None

    class JsonClient:
        def stream(self, method, url, **kwargs):
            return JsonContext()

    client = OpenAICompatibleModelClient(
        AstraRuntimeSettings(model_provider="qwen", model_name="qwen3.7-plus", model_api_key="secret"),
        http_client=JsonClient(),
    )
    client.bind_model_thinking(
        {
            "requested": {"enabled": False, "depth": None, "capability_version": 2},
            "effective": {"enabled": False, "depth": None},
            "source": "explicit_model_control",
            "adapter": "qwen-hybrid-thinking",
            "adjustments": [],
            "capability_version": 2,
        }
    )
    observed = []

    async def observe(event):
        observed.append(event)

    client.bind_model_thinking_observer(observe)
    await client._chat_json(
        [{"role": "user", "content": "返回 JSON"}], operation=ModelOperation.SYNTHESIS
    )
    assert observed == []


@pytest.mark.parametrize(
    ("provider", "model"),
    [("openai", "gpt-5"), ("anthropic", "claude-sonnet-4-6")],
)
async def test_server_reuses_model_connections_across_runs(
    monkeypatch, provider, model
):
    await close_shared_model_http_clients()
    FakeOpenAIAsyncClient.instances = 0
    FakeOpenAIAsyncClient.closes = 0
    FakeOpenAIAsyncClient.options = []
    monkeypatch.setattr("app.application.runner.engine.httpx.AsyncClient", FakeOpenAIAsyncClient)
    settings = AstraRuntimeSettings(
        model_provider=provider,
        model_name=model,
        model_api_key="secret",
        model_base_url="https://api.openai.test/v1",
    )

    first = shared_model_http_client(settings)
    second = shared_model_http_client(settings)

    assert first is second
    assert FakeOpenAIAsyncClient.instances == 1
    options = FakeOpenAIAsyncClient.options[0]
    assert options["http2"] is True
    assert options["limits"].max_connections == 64
    assert options["limits"].max_keepalive_connections == 32
    assert options["limits"].keepalive_expiry == 300
    assert options["timeout"].connect == 10
    await close_shared_model_http_clients()
    assert FakeOpenAIAsyncClient.closes == 1


async def test_server_reuses_tool_registry_across_model_overrides(monkeypatch):
    await close_shared_model_http_clients()
    builds = 0

    def build_registry(_settings):
        nonlocal builds
        builds += 1
        return AstraToolRegistry()

    monkeypatch.setattr("app.application.runner.engine.build_application_tool_registry", build_registry)
    settings = AstraRuntimeSettings(
        model_provider="openai",
        model_name="first-model",
        model_api_key="secret",
        sandbox_skip_availability_check=True,
    )

    first = shared_tool_registry(settings)
    second = shared_tool_registry(
        settings.model_copy(
            update={"model_name": "second-model", "model_api_key": "other-secret"}
        )
    )
    changed_tools = shared_tool_registry(
        settings.model_copy(update={"tool_states": {"bash_execute": True}})
    )

    assert first is second
    assert changed_tools is not first
    assert builds == 2
    await close_shared_model_http_clients()


async def test_anthropic_transport_applies_adaptive_thinking_and_effort(monkeypatch):
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
        instances = 0
        closes = 0

        def __init__(self, **kwargs):
            self.__class__.instances += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aclose(self):
            self.__class__.closes += 1

        async def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeAnthropicAsyncClient)
    client = AnthropicModelClient(
        AstraRuntimeSettings(
            model_provider="anthropic",
            model_name="claude-sonnet-4-6",
            model_api_key="secret",
        )
    )
    client.bind_reasoning_effort("balanced")
    client.bind_model_thinking(
        {
            "requested": {
                "enabled": True,
                "depth": "high",
                "capability_version": 2,
            },
            "effective": {"enabled": True, "depth": "high"},
            "source": "explicit_model_control",
            "adapter": "anthropic-adaptive-thinking",
            "adjustments": [],
            "capability_version": 2,
        }
    )
    usage = RecordingUsage()
    client.usage_recorder = usage

    for _ in range(2):
        await client._chat_json(
            [{"role": "user", "content": "返回 JSON"}],
            operation=ModelOperation.CONTRACT,
        )
    await client.aclose()

    assert requests[0]["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert requests[0]["output_config"] == {"effort": "high"}
    assert (
        usage.finished[0][1]["usage"]["astra_reasoning"]["adapter"]
        == "anthropic-adaptive-thinking"
    )
    assert FakeAnthropicAsyncClient.instances == 1
    assert len(requests) == 2
    assert FakeAnthropicAsyncClient.closes == 1
