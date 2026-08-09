import pytest

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolArtifactReference,
    ToolExecutionError,
    ToolResultEnvelope,
)
from app.infrastructure.tools.registry import (
    build_application_tool_registry,
    build_plugin_inventory,
)


class ExampleTool(AstraTool):
    spec = AstraToolSpec(
        name="example.read",
        version="1",
        input_schema={},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
    )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(data={"ok": True}).model_dump(mode="json")


def test_builtin_catalog_contains_no_retired_web_tools_or_provider():
    settings = AstraRuntimeSettings(sandbox_skip_availability_check=True)
    inventory = build_plugin_inventory(settings)
    registry = build_application_tool_registry(settings)

    assert set(inventory.tools) == {"chart.render", "bash_execute"}
    assert set(inventory.providers) == {"astra.chart", "astra.shell"}
    assert set(registry.specs()) == {
        "chart.render",
        "forget",
        "remember",
        "swarm",
        "workspace.edit",
        "workspace.list",
        "workspace.read",
        "workspace.search",
        "workspace.write",
    }
    for retired in ("web_search", "web_fetch"):
        with pytest.raises(ToolExecutionError) as error:
            registry.get(retired)
        assert error.value.category == "tool_not_allowed"


def test_disabled_sandbox_keeps_control_plane_and_structured_workspace_tools():
    registry = build_application_tool_registry(AstraRuntimeSettings(sandbox_enabled=False))

    assert set(registry.specs()) == {
        "forget",
        "remember",
        "swarm",
        "workspace.edit",
        "workspace.list",
        "workspace.read",
        "workspace.search",
        "workspace.write",
    }


def test_disabled_memory_writes_hide_remember_but_keep_forget_available():
    registry = build_application_tool_registry(AstraRuntimeSettings(sandbox_enabled=False, agent_memory_write_enabled=False))

    assert "remember" not in registry.specs()
    assert "forget" in registry.specs()


def test_tool_contract_serializes_artifact_envelope_and_legacy_permission_shape():
    spec = ExampleTool.spec
    result = ToolResultEnvelope(artifacts=[ToolArtifactReference(id="a1", type="chart", mime_type="image/png")])

    assert spec.capabilities == ["network_read"]
    assert result.model_dump()["artifacts"][0]["id"] == "a1"


def test_tool_registries_remain_composable():
    first = AstraToolRegistry().extend([ExampleTool()])
    second = AstraToolRegistry().extend([])

    assert set(AstraToolRegistry.compose(first, second).specs()) == {"example.read"}
