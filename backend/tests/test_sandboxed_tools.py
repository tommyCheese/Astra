import json
from types import SimpleNamespace

import pytest

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.sandbox.runtime import SandboxResult
from app.infrastructure.plugins.builtin import _web_runtime_config
from app.infrastructure.tools.base import ToolExecutionContext, ToolExecutionError
from app.infrastructure.tools.registry import build_tool_registry
from app.infrastructure.tools.sandboxed import SandboxedWebTool
from app.infrastructure.tools.web.fetching import WebFetchTool


class RecordingSandboxService:
    def __init__(self, envelope):
        self.envelope = envelope
        self.request = None
        self.payload = None

    async def execute(self, request, **kwargs):
        self.request = request
        self.payload = json.loads((request.input_dir / "request.json").read_text())
        self.runtime_config = json.loads((request.input_dir / "runtime-config.json").read_text())
        return (
            SimpleNamespace(id="job-1"),
            [],
            SandboxResult(0, stdout=json.dumps(self.envelope)),
        )


def context(service):
    return ToolExecutionContext(
        run_id="run-1",
        tool_call_id="call-1",
        step_id="step-1",
        trace_id="trace-1",
        artifact_service=None,
        sandbox_service=service,
    )


def test_application_registry_exposes_only_container_tools():
    registry = build_tool_registry(
        AstraRuntimeSettings(sandbox_enabled=True, sandbox_skip_availability_check=True)
    )

    assert {"web_search", "web_fetch", "chart.render", "swarm"} == set(registry.specs())
    assert registry.specs()["swarm"].execution_backend == "astra.runtime"
    assert all(
        spec.execution_backend == "sandbox.remote"
        for name, spec in registry.specs().items()
        if name != "swarm"
    )


def test_registry_exposes_no_tools_when_sandbox_is_disabled():
    registry = build_tool_registry(AstraRuntimeSettings(sandbox_enabled=False))

    assert set(registry.specs()) == {"swarm"}
    assert registry.specs()["swarm"].execution_backend == "astra.runtime"


def test_web_runtime_config_is_an_explicit_host_secret_allowlist():
    settings = AstraRuntimeSettings(
        model_api_key="model-secret",
        database_url="postgresql://private-host/astra",
        artifact_store_path="/Users/private/artifacts",
        web_search_api_key="search-secret",
    )

    runtime_config = _web_runtime_config(settings, "web_search")

    assert runtime_config["WEB_SEARCH_PROVIDER"] == "auto"
    assert runtime_config["WEB_SEARCH_API_KEY"] == "search-secret"
    assert "MODEL_API_KEY" not in runtime_config
    assert "DATABASE_URL" not in runtime_config
    assert "ARTIFACT_STORE_PATH" not in runtime_config
    assert "/Users/private" not in json.dumps(runtime_config)


def test_web_runtime_config_passes_only_explicit_search_credentials():
    settings = AstraRuntimeSettings(
        web_search_provider="auto",
        web_search_api_key="brave-secret",
        google_search_api_key="google-secret",
        google_search_engine_id="cx-secret",
    )

    runtime_config = _web_runtime_config(settings, "web_search")

    assert runtime_config == {
        "ALLOW_NETWORK_READ": "true",
        "WEB_SEARCH_PROVIDER": "auto",
        "WEB_SEARCH_API_KEY": "brave-secret",
        "GOOGLE_SEARCH_API_KEY": "google-secret",
        "GOOGLE_SEARCH_ENGINE_ID": "cx-secret",
        "GOOGLE_SEARCH_RESULT_COUNT": "5",
        "GOOGLE_SEARCH_LANGUAGE": "lang_zh-CN",
        "GOOGLE_SEARCH_REGION": "",
        "GOOGLE_SEARCH_SAFE": "active",
    }


async def test_web_tool_executes_through_container_protocol_only():
    service = RecordingSandboxService(
        {"ok": True, "output": {"url": "https://example.com", "content": "example"}}
    )
    settings = AstraRuntimeSettings()
    tool = SandboxedWebTool(
        WebFetchTool(settings), settings, _web_runtime_config(settings, "web_fetch")
    )

    output = await tool.run({"url": "https://example.com"}, context=context(service))

    assert output["content"] == "example"
    assert service.payload == {
        "version": "1",
        "tool": "web_fetch",
        "input": {"url": "https://example.com"},
    }
    assert service.request.allow_internet_access is True
    assert service.request.record_stdout is False
    assert service.request.command == ["/opt/astra/bin/tool-runtime"]
    assert service.request.template == "astra-web-tools:0.1.0"
    assert service.request.environment == {"TZ": "UTC", "PYTHONHASHSEED": "0"}


async def test_web_tool_rejects_invalid_container_response():
    service = RecordingSandboxService({"ok": True, "output": "not-an-object"})
    settings = AstraRuntimeSettings()
    tool = SandboxedWebTool(
        WebFetchTool(settings), settings, _web_runtime_config(settings, "web_fetch")
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"url": "https://example.com"}, context=context(service))

    assert exc_info.value.category == "sandbox_policy_violation"
