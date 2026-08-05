"""One-version adapter for registries assembled before provider plugins were mandatory."""

from __future__ import annotations

from collections import defaultdict

from app.application.permissions.effects import (
    BashEffectAnalyzer,
    ChartEffectAnalyzer,
    WebEffectAnalyzer,
)
from app.infrastructure.plugins.builtin_components import (
    BashApprovalPresenter,
    BashResultProcessor,
    ChartArtifactValidator,
    ChartResultProcessor,
    LegacyAutoResultAdapter,
    LegacyRawResultAdapter,
    WebEvidenceValidator,
    WebResultProcessor,
)
from app.infrastructure.plugins.catalog import PluginCatalog, PluginCatalogBuilder
from app.infrastructure.plugins.contracts import (
    PluginApplicabilityBinding,
    PluginComponentContribution,
    PluginComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    PluginToolContribution,
)
from app.infrastructure.plugins.discovery import BuiltinDiscoverySource


_COMPONENTS = {
    "web_search": (WebEffectAnalyzer, WebResultProcessor, WebEvidenceValidator, None),
    "web_fetch": (WebEffectAnalyzer, WebResultProcessor, WebEvidenceValidator, None),
    "chart.render": (ChartEffectAnalyzer, ChartResultProcessor, ChartArtifactValidator, None),
    "bash_execute": (BashEffectAnalyzer, BashResultProcessor, None, BashApprovalPresenter),
}


def build_legacy_compatibility_catalog(registry) -> PluginCatalog:
    grouped = defaultdict(list)
    for tool in registry.tools():
        grouped[tool.spec.provider_id].append(tool)
    contributions = tuple(
        _contribution(provider_id, tools)
        for provider_id, tools in sorted(grouped.items())
    )
    allowed = {
        item.descriptor.provider_id: {item.descriptor.digest}
        for item in contributions
    }
    return PluginCatalogBuilder(
        [BuiltinDiscoverySource(contributions)],
        allowed_providers=allowed,
    ).build_static()


def _contribution(provider_id, tools) -> PluginContribution:
    digest = tools[0].spec.provider_digest
    descriptor = PluginDescriptor(
        plugin_id=f"{provider_id}.legacy-compat",
        provider_id=provider_id,
        version="1",
        digest=digest,
        source="builtin",
        trust_level="platform",
    )
    analyzers = []
    processors = []
    validators = []
    presenters = []
    for tool in tools:
        mapped = _COMPONENTS.get(tool.spec.name)
        if mapped is None:
            continue
        for kind, factory, target in zip(
            ("analyzer", "processor", "validator", "presenter"),
            mapped,
            (analyzers, processors, validators, presenters),
            strict=True,
        ):
            if factory is not None:
                target.append(_component(provider_id, tool.spec.name, kind, factory))
    return PluginContribution(
        descriptor=descriptor,
        tools=tuple(
            PluginToolContribution(
                tool=tool,
                executor_id="in_process",
                result_adapter_id=(
                    "legacy.raw.v0"
                    if tool.spec.name in {"web_search", "web_fetch"}
                    else "legacy.auto.v0"
                ),
                result_adapter_factory=(
                    LegacyRawResultAdapter
                    if tool.spec.name in {"web_search", "web_fetch"}
                    else LegacyAutoResultAdapter
                ),
            )
            for tool in tools
        ),
        effect_analyzers=tuple(analyzers),
        result_processors=tuple(processors),
        validators=tuple(validators),
        approval_presenters=tuple(presenters),
    )


def _component(provider_id, tool_name, kind, factory):
    return PluginComponentContribution(
        identity=PluginComponentIdentity(
            component_id=f"{provider_id}.legacy.{tool_name}.{kind}",
            provider_id=provider_id,
            version="1",
            digest="legacy-compat",
        ),
        applicability=PluginApplicabilityBinding(tool_names=(tool_name,)),
        factory=factory,
    )


def legacy_approval_presenter(tool_name):
    mapped = _COMPONENTS.get(tool_name)
    factory = mapped[3] if mapped is not None else None
    return factory() if factory is not None else None
