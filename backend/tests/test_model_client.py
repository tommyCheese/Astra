import json

import pytest

from app.application.agent_runtime.policies.reasoning import build_default_contract
from app.common.core.config import AstraRuntimeSettings
from app.domain.agent_profile import ModelOperation
from app.infrastructure.model_clients.anthropic import AnthropicModelClient
from app.infrastructure.model_clients.contracts import (
    ModelConfigurationError,
    ModelOutputError,
)
from app.infrastructure.model_clients.factory import build_model_client
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.model_clients.openai_compatible import OpenAICompatibleModelClient


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
        "catalog_search" not in node.required_capabilities and "catalog_read" not in node.required_capabilities
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
    manifests = {
        "catalog_search": {"task_capabilities": ["information.search"]},
        "catalog_read": {"task_capabilities": ["information.read"]},
    }
    first = await client.decide("查询 Astra", {"observations": [], "tool_manifests": manifests})
    second = await client.decide(
        "查询 Astra",
        {
            "observations": [
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {
                        "tool_name": "catalog_search",
                        "candidates": [{"url": "https://example.com/a", "snippet": "A"}],
                    },
                }
            ],
            "tool_manifests": manifests,
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
                        "tool_name": "catalog_search",
                        "candidates": [{"url": "https://example.com/a", "snippet": "A"}],
                    },
                },
                {
                    "kind": "tool_result",
                    "status": "succeeded",
                    "data": {
                        "tool_name": "catalog_read",
                        "url": "https://example.com/a",
                        "content": "A",
                    },
                },
            ],
            "tool_manifests": manifests,
        },
    )

    assert first.tool_name == "catalog_search"
    assert second.tool_name == "catalog_read"
    assert final.decision_type == "finalize"


async def test_mock_model_client_finalizes_delegated_plan_with_schema_shaped_outputs():
    client = MockModelClient()
    decision = await client.decide(
        "独立分析边界条件",
        {
            "active_node": None,
            "plan": {"nodes": [{"id": "child-step-1", "status": "completed"}]},
            "delegation_contract": {
                "request": {
                    "output_schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["summary", "checks", "confidence", "accepted"],
                        "properties": {
                            "summary": {"type": "string"},
                            "checks": {
                                "type": "array",
                                "minItems": 2,
                                "items": {
                                    "type": "object",
                                    "required": ["name", "severity"],
                                    "properties": {
                                        "name": {"type": "string"},
                                        "severity": {"enum": ["high", "low"]},
                                    },
                                },
                            },
                            "confidence": {"type": "number", "minimum": 0.5},
                            "accepted": {"type": "boolean"},
                        },
                    }
                }
            },
            "observations": [],
            "tool_manifests": {},
        },
    )

    assert decision.decision_type == "finalize"
    assert decision.node_result["outputs"] == {
        "summary": "Mock delegated result.",
        "checks": [
            {"name": "Mock delegated result.", "severity": "high"},
            {"name": "Mock delegated result.", "severity": "high"},
        ],
        "confidence": 0.5,
        "accepted": True,
    }


async def test_mock_model_client_executes_requested_workspace_write_then_read():
    client = MockModelClient()
    manifests = {
        "workspace.write": {"task_capabilities": ["workspace.write"]},
        "workspace.read": {"task_capabilities": ["workspace.read"]},
    }
    goal = "请创建 hello.txt 文件，内容为 hello Astra，然后读取并确认文件内容。"

    write = await client.decide(goal, {"observations": [], "tool_manifests": manifests})
    read = await client.decide(
        goal,
        {"observations": [{"data": {"tool_name": "workspace.write"}}], "tool_manifests": manifests},
    )

    assert write.tool_name == "workspace.write"
    assert write.tool_input == {"path": "hello.txt", "content": "hello Astra"}
    assert read.tool_name == "workspace.read"
    assert read.tool_input == {"path": "hello.txt"}


async def test_mock_model_client_advances_through_workspace_regression_sequence():
    client = MockModelClient()
    manifests = {
        name: {"task_capabilities": [name]}
        for name in (
            "workspace.write",
            "workspace.read",
            "workspace.search",
            "workspace.edit",
            "workspace.list",
        )
    }
    goal = (
        "创建 regression/files/sample.txt，内容为 alpha beta\\nTARGET_TEXT\\ngamma；"
        "读取文件；搜索 TARGET_TEXT；将 gamma 精确替换为 delta；再次读取；列出目录。"
    )
    observations = []
    decisions = []
    for _ in range(6):
        decision = await client.decide(goal, {"observations": observations, "tool_manifests": manifests})
        decisions.append(decision)
        observations.append({"kind": "tool_result", "status": "succeeded", "tool_name": decision.tool_name, "data": {}})

    assert [decision.tool_name for decision in decisions] == [
        "workspace.write",
        "workspace.read",
        "workspace.search",
        "workspace.edit",
        "workspace.read",
        "workspace.list",
    ]
    assert decisions[0].tool_input == {
        "path": "regression/files/sample.txt",
        "content": "alpha beta\nTARGET_TEXT\ngamma",
    }
    assert decisions[2].tool_input == {"query": "TARGET_TEXT"}
    assert decisions[3].tool_input == {
        "path": "regression/files/sample.txt",
        "old_text": "gamma",
        "new_text": "delta",
    }
    assert decisions[5].tool_input == {"path": "regression/files"}


async def test_mock_workspace_decision_uses_the_current_request_in_conversation_context():
    client = MockModelClient()
    manifests = {
        "workspace.write": {"task_capabilities": ["workspace.write"]},
        "workspace.read": {"task_capabilities": ["workspace.read"]},
    }
    goal = (
        "Conversation context:\n"
        "User: 创建 history/old.txt，内容为 old\n"
        "Assistant: 已创建旧文件。\n"
        "Current user request: 创建 current/new.txt，内容为 new，然后读取文件。"
    )

    write = await client.decide(goal, {"observations": [], "tool_manifests": manifests})
    read = await client.decide(
        goal,
        {"observations": [{"data": {"tool_name": "workspace.write"}}], "tool_manifests": manifests},
    )

    assert write.tool_input == {"path": "current/new.txt", "content": "new"}
    assert read.tool_input == {"path": "current/new.txt"}


async def test_mock_synthesis_does_not_echo_recursive_conversation_context():
    client = MockModelClient()
    rendered_goal = (
        "Conversation context:\n"
        "User: 历史问题\n"
        "Assistant: 历史回答\n"
        "Current user request: 当前文件问题"
    )

    answer = await client.synthesize(rendered_goal, [])

    assert answer.summary == "已完成任务：当前文件问题"
    assert "Conversation context" not in answer.summary
    assert "历史回答" not in answer.summary


async def test_mock_plan_uses_only_the_current_request_from_conversation_context():
    client = MockModelClient()
    contract = await client.contract("当前请求")
    plan = await client.plan(
        "Conversation context:\nUser: 历史请求\nAssistant: 历史回答\n"
        "Current user request: 保存记忆 current-only",
        contract=contract,
    )

    assert "当前请求" not in plan.nodes[0].intent
    assert "历史请求" not in plan.nodes[0].intent
    assert "历史回答" not in plan.nodes[0].intent
    assert "保存记忆 current-only" in plan.nodes[0].intent
    assert plan.nodes[1].required_capabilities == ["memory.remember"]


async def test_mock_workspace_decision_supports_strict_path_content_range_and_replace_all_inputs():
    client = MockModelClient()
    manifests = {
        name: {"task_capabilities": [name]}
        for name in ("workspace.write", "workspace.read", "workspace.edit")
    }
    goal = (
        "创建文件，文件路径为 `regression/深层 目录/样本.txt`，内容为<<<"
        "第一行：中文, commas; semicolons\n第二行：🧪\\path\n重复 TOKEN TOKEN"
        ">>>；读取第 2 行到第 3 行；将 TOKEN 全部替换为 DONE。"
    )
    observations = []

    write = await client.decide(goal, {"observations": observations, "tool_manifests": manifests})
    observations.append({"tool_name": "workspace.write"})
    read = await client.decide(goal, {"observations": observations, "tool_manifests": manifests})
    observations.append({"tool_name": "workspace.read"})
    edit = await client.decide(goal, {"observations": observations, "tool_manifests": manifests})

    assert write.tool_input == {
        "path": "regression/深层 目录/样本.txt",
        "content": "第一行：中文, commas; semicolons\n第二行：🧪\\path\n重复 TOKEN TOKEN",
    }
    assert read.tool_input == {
        "path": "regression/深层 目录/样本.txt",
        "line_start": 2,
        "line_end": 3,
    }
    assert edit.tool_input == {
        "path": "regression/深层 目录/样本.txt",
        "old_text": "TOKEN",
        "new_text": "DONE",
        "replace_all": True,
    }


async def test_mock_explicit_tool_decision_routes_any_manifest_tool_with_strict_json():
    client = MockModelClient()
    goal = (
        '调用工具 chart.render，参数为<<<'
        '{"chart_type":"bar","x":"name","y":["value"],'
        '"data":{"columns":["name","value"],"rows":[["A",1]]}}'
        '>>>；必须真实执行。'
    )
    context = {
        "observations": [],
        "tool_manifests": {"chart.render": {"task_capabilities": ["data.visualize"]}},
    }

    decision = await client.decide(goal, context)

    assert decision.decision_type == "call_tool"
    assert decision.tool_name == "chart.render"
    assert decision.tool_input == {
        "chart_type": "bar",
        "x": "name",
        "y": ["value"],
        "data": {"columns": ["name", "value"], "rows": [["A", 1]]},
    }


async def test_mock_explicit_tool_decision_is_manifest_bounded_and_not_repeated():
    client = MockModelClient()
    goal = '调用工具 bash_execute，参数为<<<{"command":"printf ok"}>>>'
    manifests = {"bash_execute": {"task_capabilities": ["workspace.execute"]}}

    repeated = await client.decide(
        goal,
        {
            "observations": [{"data": {"tool_name": "bash_execute"}}],
            "tool_manifests": manifests,
        },
    )
    unavailable = await client.decide(goal, {"observations": [], "tool_manifests": {}})

    assert repeated.decision_type == "finalize"
    assert unavailable.decision_type == "finalize"


async def test_non_workspace_explicit_tool_json_does_not_trigger_workspace_keywords():
    client = MockModelClient()
    goal = (
        '调用工具 swarm，参数为<<<{"resource_scope":'
        '{"workspace_read_roots":[],"workspace_write_roots":[]}}>>>'
    )
    context = {
        "observations": [],
        "tool_manifests": {
            "workspace.read": {"task_capabilities": ["workspace.read"]},
        },
    }

    decision = await client.decide(goal, context)

    assert decision.decision_type == "finalize"


async def test_mock_model_client_does_not_retry_failed_fetch_url():
    client = MockModelClient()
    context = {
        "observations": [
            {
                "kind": "tool_result",
                "status": "succeeded",
                "data": {
                    "tool_name": "catalog_search",
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
                    "tool_name": "catalog_read",
                    "url": "https://example.com/fails",
                },
            },
        ],
        "tool_manifests": {
            "catalog_search": {"task_capabilities": ["information.search"]},
            "catalog_read": {"task_capabilities": ["information.read"]},
        },
    }

    decision = await client.decide("查询 Astra", context)

    assert decision.decision_type == "call_tool"
    assert decision.tool_name == "catalog_read"
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
    settings = AstraRuntimeSettings(model_provider="openai", model_api_key="")

    with pytest.raises(ModelConfigurationError):
        build_model_client(settings)


def test_mock_model_requires_no_credentials():
    client = build_model_client(AstraRuntimeSettings(model_provider="mock", model_api_key=""))

    assert isinstance(client, MockModelClient)


def test_anthropic_provider_uses_native_client():
    client = build_model_client(
        AstraRuntimeSettings(model_provider="anthropic", model_api_key="secret", model_name="claude-test")
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
            yield ('data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"{\\"summary\\":\\"完成\\"}"}}')

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

    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    client = AnthropicModelClient(
        AstraRuntimeSettings(
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

    monkeypatch.setattr("app.infrastructure.model_clients.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    settings = AstraRuntimeSettings(
        model_provider=provider,
        model_api_key="secret",
        model_name="test-model",
        model_base_url=f"https://{provider}.test/v1",
    )
    client = AnthropicModelClient(settings) if provider == "anthropic" else OpenAICompatibleModelClient(settings)
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
