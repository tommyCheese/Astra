import json

import pytest

from app.agent_profile import ModelOperation
from app.agent_runtime.reasoning import build_default_contract
from app.core.config import Settings
from app.model_clients.anthropic import AnthropicModelClient
from app.model_clients.contracts import (
    ModelConfigurationError,
    ModelOutputError,
)
from app.model_clients.factory import build_model_client
from app.model_clients.mock import MockModelClient
from app.model_clients.openai_compatible import OpenAICompatibleModelClient


async def test_mock_model_client_returns_structured_outputs():
    client = MockModelClient()
    contract = build_default_contract("查询 Astra")
    plan = await client.plan(
        "查询 Astra",
        contract=contract,
    )
    answer = await client.synthesize(
        "查询 Astra",
        [{"url": "https://example.com/a", "content": "示例内容", "retrieved_at": "now"}],
    )

    assert plan.nodes
    assert all(
        "web_search" not in node.required_capabilities
        and "web_fetch" not in node.required_capabilities
        for node in plan.nodes
    )
    assert plan.nodes[1].required_capabilities == [
        "information.search",
        "information.read",
    ]
    assert plan.nodes[-1].depends_on == ["step-2"]
    assert answer.sources[0].url == "https://example.com/a"


async def test_mock_model_client_agent_decisions():
    client = MockModelClient()
    first = await client.decide("查询 Astra", {"observations": []})
    second = await client.decide(
        "查询 Astra",
        {
            "observations": [
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {
                        "tool_name": "web_search",
                        "candidates": [{"url": "https://example.com/a", "snippet": "A"}],
                    },
                }
            ]
        },
    )
    final = await client.decide(
        "查询 Astra",
        {
            "observations": [
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {
                        "tool_name": "web_search",
                        "candidates": [{"url": "https://example.com/a", "snippet": "A"}],
                    },
                },
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {"tool_name": "web_fetch", "url": "https://example.com/a"},
                },
            ]
        },
    )

    assert first.tool_name == "web_search"
    assert second.tool_name == "web_fetch"
    assert final.decision_type == "finalize"


async def test_mock_model_client_does_not_retry_failed_fetch_url():
    client = MockModelClient()
    context = {
        "observations": [
            {
                "kind": "tool_result",
                "status": "succeeded",
                "data": {
                    "tool_name": "web_search",
                    "candidates": [
                        {"url": "https://example.com/fails", "snippet": "bad"},
                        {"url": "https://example.com/next", "snippet": "next"},
                    ],
                },
            },
            {
                "kind": "tool_error",
                "status": "failed",
                "data": {
                    "tool_name": "web_fetch",
                    "url": "https://example.com/fails",
                },
            },
        ]
    }

    decision = await client.decide("查询 Astra", context)

    assert decision.decision_type == "call_tool"
    assert decision.tool_name == "web_fetch"
    assert decision.tool_input["url"] == "https://example.com/next"


async def test_mock_model_reflection_and_memory_candidates():
    client = MockModelClient()
    reflection = await client.reflect("查询 Astra", {"last_observation": {"status": "failed"}})
    memories = await client.extract_memory_candidates(
        "查询 Astra",
        {
            "run_id": "run-1",
            "evidence_pack": {
                "artifact_id": "artifact-1",
                "fetched_sources": [{"url": "https://example.com"}],
            },
        },
    )

    assert reflection.next_action
    assert memories[0].provenance["artifact_id"] == "artifact-1"


def test_real_model_requires_credentials():
    settings = Settings(model_provider="openai", model_api_key="")

    with pytest.raises(ModelConfigurationError):
        build_model_client(settings)


def test_mock_model_requires_no_credentials():
    client = build_model_client(Settings(model_provider="mock", model_api_key=""))

    assert isinstance(client, MockModelClient)


def test_anthropic_provider_uses_native_client():
    client = build_model_client(
        Settings(model_provider="anthropic", model_api_key="secret", model_name="claude-test")
    )

    assert isinstance(client, AnthropicModelClient)


async def test_anthropic_client_translates_messages_and_stream_callbacks(monkeypatch):
    requests = []
    timeline = []

    class FakeResponse:
        def __init__(self):
            self.headers = {"request-id": "request-1"}

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield (
                'data: {"type":"content_block_delta",'
                '"delta":{"type":"text_delta","text":"{\\"summary\\":\\"完成\\"}"}}'
            )

    class FakeStreamContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            requests.append((url, kwargs))
            return FakeStreamContext()

    monkeypatch.setattr("app.model_clients.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    client = AnthropicModelClient(
        Settings(
            model_provider="anthropic",
            model_api_key="secret",
            model_name="claude-test",
            model_base_url="https://api.anthropic.test/v1",
        )
    )
    deltas = []

    class UsageRecorder:
        async def start(self, **_kwargs):
            timeline.append("usage.start")
            return "invocation-1"

        async def finish(self, _invocation_id, **_kwargs):
            timeline.append("usage.finish")

    client.usage_recorder = UsageRecorder()

    async def on_delta(value):
        deltas.append(value)
        timeline.append(f"delta:{value}")

    payload = await client._chat_json(
        [
            {"role": "system", "content": "Return JSON"},
            {"role": "user", "content": "完成任务"},
        ],
        operation=ModelOperation.SYNTHESIS,
        stream_field="summary",
        on_field_delta=on_delta,
    )

    assert payload == {"summary": "完成"}
    assert requests[0][0] == "https://api.anthropic.test/v1/messages"
    assert requests[0][1]["headers"]["x-api-key"] == "secret"
    assert requests[0][1]["json"]["system"] == [
        {
            "type": "text",
            "text": "Return JSON",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert requests[0][1]["json"]["stream"] is True
    assert deltas == ["完成", "\1"]
    assert timeline.index("delta:完成") < timeline.index("usage.start")


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_invalid_json_does_not_retry_after_streaming_visible_summary(monkeypatch, provider):
    requests = []
    streamed_payload = '{"summary":"保留已经展示的回答","broken":'

    class FakeResponse:
        status_code = 200

        def __init__(self):
            self.headers = {
                "content-type": "text/event-stream",
                "request-id": "request-1",
            }

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            if provider == "anthropic":
                yield "data: " + json.dumps(
                    {
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": streamed_payload},
                    }
                )
            else:
                yield "data: " + json.dumps({"choices": [{"delta": {"content": streamed_payload}}]})

    class FakeStreamContext:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            return None

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        def stream(self, method, url, **kwargs):
            requests.append((method, url, kwargs))
            return FakeStreamContext()

    monkeypatch.setattr("app.model_clients.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    settings = Settings(
        model_provider=provider,
        model_api_key="secret",
        model_name="test-model",
        model_base_url=f"https://{provider}.test/v1",
    )
    client = (
        AnthropicModelClient(settings)
        if provider == "anthropic"
        else OpenAICompatibleModelClient(settings)
    )
    deltas = []

    async def on_delta(delta):
        deltas.append(delta)

    with pytest.raises(ModelOutputError):
        await client._chat_json(
            [{"role": "user", "content": "回答"}],
            operation=ModelOperation.SYNTHESIS,
            stream_field="summary",
            on_field_delta=on_delta,
        )

    assert requests and len(requests) == 1
    assert "".join(delta for delta in deltas if delta != "\1") == "保留已经展示的回答"
