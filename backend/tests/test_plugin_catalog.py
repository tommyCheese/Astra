from dataclasses import replace

import pytest

from app.core.config import Settings
from app.plugins.builtin import builtin_contributions
from app.plugins.catalog import PluginCatalogBuilder, PluginCatalogError
from app.plugins.contracts import (
    ApplicabilityBinding,
    ComponentContribution,
    ComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    PluginLifecycleState,
    ToolContribution,
)
from app.plugins.discovery import (
    BuiltinDiscoverySource,
    IsolatedDescriptorDiscoverySource,
    IsolatedProviderReference,
    ManagedPackageDiscoverySource,
)
from app.plugins.interfaces import HealthProbe, HealthReport, ToolProviderPlugin
from app.tools.base import Tool, ToolExecutionError, ToolSpec


class CatalogTool(Tool):
    def __init__(self, name="catalog.read", provider_id="catalog.provider"):
        self.spec = ToolSpec(
            name=name,
            version="1",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission="network_read",
            side_effect_level="read_only",
            provider_id=provider_id,
            provider_digest="sha256:catalog",
            trust_level="managed",
        )

    async def run(self, tool_input, *, context=None):
        return {"protocol_version": "1", "status": "succeeded", "data": {}}


def plugin_descriptor(provider_id="catalog.provider", **updates):
    values = {
        "plugin_id": f"{provider_id}.plugin",
        "provider_id": provider_id,
        "version": "1",
        "digest": "sha256:catalog",
        "source": "builtin",
        "trust_level": "platform",
    }
    values.update(updates)
    return PluginDescriptor(**values)


class CatalogPlugin(ToolProviderPlugin):
    def __init__(self, descriptor=None, tool_name="catalog.read", *, healthy=True):
        self.descriptor = descriptor or plugin_descriptor()
        self.tool_name = tool_name
        self.healthy = healthy

    def contribute(self):
        tool = CatalogTool(self.tool_name, self.descriptor.provider_id)
        tool.spec = tool.spec.model_copy(
            update={
                "provider_digest": self.descriptor.digest,
                "trust_level": self.descriptor.trust_level,
            }
        )
        return PluginContribution(
            descriptor=self.descriptor,
            tools=(ToolContribution(tool=tool, executor_id="in_process"),),
        )


class ProbedCatalogPlugin(CatalogPlugin, HealthProbe):
    async def check(self):
        return HealthReport(healthy=self.healthy, reason=None if self.healthy else "offline")


def builder(*plugins, allowed=None):
    return PluginCatalogBuilder(
        [BuiltinDiscoverySource(plugins)],
        allowed_providers=allowed or {"catalog.provider": {"sha256:catalog"}},
    )


async def test_catalog_is_deterministic_immutable_and_composable():
    first = await builder(CatalogPlugin()).build()
    second = await builder(CatalogPlugin()).build()

    assert first.digest == second.digest
    assert list(first.tools) == ["catalog.read"]
    assert first.tool_registry().get("catalog.read").spec.name == "catalog.read"
    with pytest.raises(TypeError):
        first.tools["other"] = CatalogTool("other")


async def test_catalog_detects_provider_manifest_mutation_after_assembly():
    descriptor = plugin_descriptor()
    tool = CatalogTool()
    contribution = PluginContribution(
        descriptor=descriptor,
        tools=(ToolContribution(tool=tool, executor_id="in_process"),),
    )
    catalog = await builder(contribution).build()
    tool.spec = tool.spec.model_copy(update={"version": "changed"})

    with pytest.raises(ToolExecutionError) as drift:
        await catalog.tools["catalog.read"].run({})
    assert drift.value.category == "provider_identity_changed"


async def test_provider_digest_and_duplicate_tool_conflicts_fail_closed():
    changed = CatalogPlugin(plugin_descriptor(digest="sha256:changed"))
    with pytest.raises(PluginCatalogError) as drift:
        await builder(changed).build()
    assert drift.value.category == "provider_digest_changed"

    other_descriptor = plugin_descriptor("other.provider")
    with pytest.raises(PluginCatalogError, match="Duplicate model-visible tool"):
        await builder(
            CatalogPlugin(),
            CatalogPlugin(other_descriptor, tool_name="catalog.read"),
            allowed={
                "catalog.provider": {"sha256:catalog"},
                "other.provider": {"sha256:catalog"},
            },
        ).build()


async def test_duplicate_component_and_ambiguous_analyzer_bindings_are_rejected():
    descriptor = plugin_descriptor()
    identity = ComponentIdentity(
        component_id="catalog.analyzer",
        provider_id=descriptor.provider_id,
        version="1",
        digest="sha256:component",
    )
    binding = ApplicabilityBinding(tool_names=("catalog.read",))
    base = CatalogPlugin(descriptor).contribute()
    contribution = replace(
        base,
        effect_analyzers=(
            ComponentContribution(identity, binding, object),
            ComponentContribution(
                identity.model_copy(update={"component_id": "catalog.analyzer.two"}),
                binding,
                object,
            ),
        ),
    )

    with pytest.raises(PluginCatalogError, match="Ambiguous effect analyzer"):
        await builder(contribution).build()


async def test_unhealthy_and_disabled_providers_do_not_expose_tools():
    unhealthy = await builder(ProbedCatalogPlugin(healthy=False)).build()
    disabled = await builder(CatalogPlugin(plugin_descriptor(enabled=False))).build()

    assert unhealthy.tools == {}
    assert unhealthy.providers["catalog.provider"].state == PluginLifecycleState.unhealthy
    assert disabled.tools == {}
    assert disabled.providers["catalog.provider"].state == PluginLifecycleState.disabled


def test_managed_discovery_is_disabled_by_default_and_has_no_workspace_input(tmp_path):
    (tmp_path / "plugin.json").write_text('{"provider_id":"workspace.attack"}')
    source = ManagedPackageDiscoverySource([], enabled=False)

    assert source.discover() == ()
    assert "workspace.attack" not in repr(source.discover())


async def test_isolated_descriptor_cannot_smuggle_in_process_code():
    descriptor = plugin_descriptor(source="isolated_descriptor", trust_level="untrusted")
    tool = CatalogTool()
    contribution = PluginContribution(
        descriptor=descriptor,
        tools=(ToolContribution(tool=tool, executor_id="in_process"),),
    )
    source = IsolatedDescriptorDiscoverySource(
        [IsolatedProviderReference(descriptor, contribution)]
    )

    with pytest.raises(PluginCatalogError) as rejected:
        await PluginCatalogBuilder(
            [source], allowed_providers={"catalog.provider": {"sha256:catalog"}}
        ).build()
    assert rejected.value.category == "invalid_plugin"


def test_builtin_providers_contribute_domain_components_without_agent_loop_wiring():
    contributions = builtin_contributions(
        Settings(sandbox_skip_availability_check=True, tool_bash_execute_enabled=True)
    )
    by_provider = {item.descriptor.provider_id: item for item in contributions}

    assert {"astra.web", "astra.chart", "astra.shell"} == set(by_provider)
    assert {item.tool.spec.name for item in by_provider["astra.web"].tools} == {
        "web_search",
        "web_fetch",
    }
    assert by_provider["astra.web"].effect_analyzers
    assert by_provider["astra.web"].result_processors
    assert by_provider["astra.web"].validators
    assert by_provider["astra.chart"].effect_analyzers
    assert by_provider["astra.chart"].result_processors
    assert by_provider["astra.chart"].validators
    assert by_provider["astra.shell"].effect_analyzers
    assert by_provider["astra.shell"].result_processors
    assert by_provider["astra.shell"].approval_presenters
