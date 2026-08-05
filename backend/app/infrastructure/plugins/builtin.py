from __future__ import annotations

from collections.abc import Iterable

from app.application.permissions.effects import (
    BashEffectAnalyzer,
    ChartEffectAnalyzer,
    WebEffectAnalyzer,
)
from app.common.core.config import Settings
from app.infrastructure.plugins.builtin_components import (
    BashApprovalPresenter,
    BashResultProcessor,
    ChartArtifactValidator,
    ChartResultProcessor,
    WebEvidenceValidator,
    WebResultProcessor,
)
from app.infrastructure.plugins.contracts import (
    ApplicabilityBinding,
    ComponentContribution,
    ComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    ToolContribution,
)
from app.infrastructure.tools.base import Tool
from app.infrastructure.tools.bash import BashExecuteTool
from app.infrastructure.tools.chart import ChartRenderTool
from app.infrastructure.tools.sandboxed import SandboxedWebTool
from app.infrastructure.tools.web import build_web_registry


def builtin_contributions(settings: Settings) -> tuple[PluginContribution, ...]:
    contributions = []
    if settings.sandbox_enabled:
        native_web = build_web_registry(settings)
        web_tools = [
            SandboxedWebTool(native_web.get(name), settings) for name in native_web.specs()
        ]
        if web_tools:
            contributions.append(
                _provider(
                    "astra.web",
                    web_tools,
                    analyzers=[("web.effect", ("web_search", "web_fetch"), WebEffectAnalyzer)],
                    processors=[("web.result", ("web_search", "web_fetch"), WebResultProcessor)],
                    validators=[
                        ("web.validator", ("web_search", "web_fetch"), WebEvidenceValidator)
                    ],
                )
            )
        if settings.tool_chart_render_enabled:
            contributions.append(
                _provider(
                    "astra.chart",
                    [ChartRenderTool(settings)],
                    analyzers=[("chart.effect", ("chart.render",), ChartEffectAnalyzer)],
                    processors=[("chart.result", ("chart.render",), ChartResultProcessor)],
                    validators=[("chart.validator", ("chart.render",), ChartArtifactValidator)],
                )
            )
        if settings.tool_bash_execute_enabled:
            contributions.append(
                _provider(
                    "astra.shell",
                    [BashExecuteTool(settings)],
                    analyzers=[("shell.effect", ("bash_execute",), BashEffectAnalyzer)],
                    processors=[("shell.result", ("bash_execute",), BashResultProcessor)],
                    presenters=[("shell.approval", ("bash_execute",), BashApprovalPresenter)],
                )
            )
    return tuple(contributions)


def _provider(
    provider_id: str,
    tools: Iterable[Tool],
    *,
    analyzers=(),
    processors=(),
    validators=(),
    presenters=(),
) -> PluginContribution:
    descriptor = PluginDescriptor(
        plugin_id=f"{provider_id}.builtin",
        provider_id=provider_id,
        version="1",
        digest="builtin",
        source="builtin",
        trust_level="platform",
    )
    bound_tools = []
    for tool in tools:
        tool.spec = tool.spec.model_copy(
            update={
                "provider_id": provider_id,
                "provider_digest": descriptor.digest,
                "trust_level": descriptor.trust_level,
            }
        )
        bound_tools.append(
            ToolContribution(
                tool=tool,
                executor_id="in_process",
            )
        )

    def components(entries):
        return tuple(
            ComponentContribution(
                identity=ComponentIdentity(
                    component_id=f"{provider_id}.{component_id}",
                    provider_id=provider_id,
                    version="1",
                    digest="builtin",
                ),
                applicability=ApplicabilityBinding(tool_names=tool_names),
                factory=factory,
            )
            for component_id, tool_names, factory in entries
        )

    return PluginContribution(
        descriptor=descriptor,
        tools=tuple(bound_tools),
        effect_analyzers=components(analyzers),
        result_processors=components(processors),
        validators=components(validators),
        approval_presenters=components(presenters),
    )
