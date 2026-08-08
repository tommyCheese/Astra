from __future__ import annotations

from collections.abc import Iterable

from app.application.permissions.effects import (
    BashEffectAnalyzer,
    ChartEffectAnalyzer,
)
from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.plugins.builtin_components import (
    BashApprovalPresenter,
    BashResultProcessor,
    ChartArtifactValidator,
    ChartResultProcessor,
)
from app.infrastructure.plugins.contracts import (
    PluginApplicabilityBinding,
    PluginComponentContribution,
    PluginComponentIdentity,
    PluginContribution,
    PluginDescriptor,
    PluginToolContribution,
)
from app.infrastructure.tools.base import AstraTool
from app.infrastructure.tools.bash import BashExecuteTool
from app.infrastructure.tools.chart import ChartRenderTool


def builtin_contributions(
    settings: AstraRuntimeSettings,
    *,
    include_disabled: bool = False,
) -> tuple[PluginContribution, ...]:
    contributions = []
    if settings.sandbox_enabled:
        if include_disabled or (
            _tool_enabled(settings, "chart.render")
            and _provider_enabled(settings, "astra.chart")
        ):
            contributions.append(
                _provider(
                    "astra.chart",
                    [ChartRenderTool(settings)],
                    configuration_revision=_configuration_revision(settings, "astra.chart"),
                    analyzers=[("chart.effect", ("chart.render",), ChartEffectAnalyzer)],
                    processors=[("chart.result", ("chart.render",), ChartResultProcessor)],
                    validators=[("chart.validator", ("chart.render",), ChartArtifactValidator)],
                )
            )
        if include_disabled or (
            _tool_enabled(settings, "bash_execute", default=False)
            and _provider_enabled(settings, "astra.shell")
        ):
            contributions.append(
                _provider(
                    "astra.shell",
                    [BashExecuteTool(settings)],
                    configuration_revision=_configuration_revision(settings, "astra.shell"),
                    analyzers=[("shell.effect", ("bash_execute",), BashEffectAnalyzer)],
                    processors=[("shell.result", ("bash_execute",), BashResultProcessor)],
                    presenters=[("shell.approval", ("bash_execute",), BashApprovalPresenter)],
                )
            )
    return tuple(contributions)


def _provider_enabled(settings: AstraRuntimeSettings, provider_id: str) -> bool:
    return settings.tool_provider_states.get(provider_id, True)


def _tool_enabled(
    settings: AstraRuntimeSettings,
    tool_name: str,
    *,
    default: bool = True,
) -> bool:
    return settings.tool_enabled(tool_name, default=default)


def _configuration_revision(settings: AstraRuntimeSettings, provider_id: str) -> str:
    return settings.tool_provider_configuration_revisions.get(provider_id, "default")


def _provider(
    provider_id: str,
    tools: Iterable[AstraTool],
    *,
    analyzers=(),
    processors=(),
    validators=(),
    presenters=(),
    configuration_schema: dict | None = None,
    configuration_revision: str = "default",
) -> PluginContribution:
    descriptor = PluginDescriptor(
        plugin_id=f"{provider_id}.builtin",
        provider_id=provider_id,
        version="1",
        digest="builtin",
        source="builtin",
        trust_level="platform",
        configuration_schema=configuration_schema or {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        configuration_revision=configuration_revision,
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
            PluginToolContribution(
                tool=tool,
                executor_id="in_process",
            )
        )

    def components(entries):
        return tuple(
            PluginComponentContribution(
                identity=PluginComponentIdentity(
                    component_id=f"{provider_id}.{component_id}",
                    provider_id=provider_id,
                    version="1",
                    digest="builtin",
                ),
                applicability=PluginApplicabilityBinding(tool_names=tool_names),
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
