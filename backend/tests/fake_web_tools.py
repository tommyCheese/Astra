from app.application.permissions.effects import WebEffectAnalyzer
from app.infrastructure.plugins.builtin_components import (
    WebEvidenceValidator,
    WebResultProcessor,
)
from app.infrastructure.plugins.catalog import PluginCatalogBuilder
from app.infrastructure.plugins.contracts import (
    PluginApplicabilityBinding,
    PluginComponentContribution,
    PluginComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    PluginToolContribution,
)
from app.infrastructure.plugins.discovery import BuiltinDiscoverySource
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolResultEnvelope,
)


class FakeSearch(AstraTool):
    spec = AstraToolSpec(
        name="web_search",
        version="test",
        input_schema={"required": ["query"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.search", "source.discover"],
        provider_id="astra.builtin",
        provider_digest="builtin",
    )

    async def run(self, tool_input, *, context=None):
        self.last_context = context
        return ToolResultEnvelope(data={
            "query": tool_input["query"],
            "provider": "test",
            "candidate_count": 1,
            "warnings": [],
            "candidates": [
                {
                    "url": "https://test.invalid/source",
                    "title": "Test",
                    "snippet": "Evidence",
                    "provider": "test",
                    "retrieved_at": "now",
                }
            ],
        }).model_dump(mode="json")


class FakeFetch(AstraTool):
    spec = AstraToolSpec(
        name="web_fetch",
        version="test",
        input_schema={"required": ["url"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.read", "source.retrieve", "evidence.extract"],
        provider_id="astra.builtin",
        provider_digest="builtin",
    )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(data={
            "url": tool_input["url"],
            "status_code": 200,
            "title": "Test",
            "content": "Deterministic test evidence",
            "quality_score": 0.9,
            "extraction_strategy": "test",
            "warnings": [],
            "retrieved_at": "now",
        }).model_dump(mode="json")


def fake_web_registry():
    tools = (FakeSearch(), FakeFetch())
    descriptor = PluginDescriptor(
        plugin_id="test.web.plugin",
        provider_id="astra.builtin",
        version="1",
        digest="builtin",
        source="builtin",
        trust_level="platform",
    )

    def component(component_id, factory):
        return PluginComponentContribution(
            identity=PluginComponentIdentity(
                component_id=f"test.web.{component_id}",
                provider_id="astra.builtin",
                version="1",
                digest="builtin",
            ),
            applicability=PluginApplicabilityBinding(
                tool_names=("web_search", "web_fetch")
            ),
            factory=factory,
        )

    contribution = PluginContribution(
        descriptor=descriptor,
        tools=tuple(PluginToolContribution(tool, "in_process") for tool in tools),
        effect_analyzers=(component("effect", WebEffectAnalyzer),),
        result_processors=(component("result", WebResultProcessor),),
        validators=(component("validator", WebEvidenceValidator),),
    )
    catalog = PluginCatalogBuilder(
        [BuiltinDiscoverySource([contribution])],
        allowed_providers={"astra.builtin": {"builtin"}},
    ).build_static()
    return AstraToolRegistry(plugin_catalog=catalog).extend(tools)
