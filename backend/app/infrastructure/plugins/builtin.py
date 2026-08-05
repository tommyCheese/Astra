from __future__ import annotations

from collections.abc import Iterable

from app.application.permissions.effects import (
    BashEffectAnalyzer,
    ChartEffectAnalyzer,
    WebEffectAnalyzer,
)
from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.plugins.builtin_components import (
    BashApprovalPresenter,
    BashResultProcessor,
    ChartArtifactValidator,
    ChartResultProcessor,
    WebEvidenceValidator,
    WebResultProcessor,
    LegacyRawResultAdapter,
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
from app.infrastructure.tools.sandboxed import SandboxedWebTool
from app.infrastructure.tools.web import build_web_registry


def builtin_contributions(settings: AstraRuntimeSettings) -> tuple[PluginContribution, ...]:
    contributions = []
    if settings.sandbox_enabled:
        native_web = build_web_registry(settings)
        web_tools = [
            SandboxedWebTool(
                native_web.get(name),
                settings,
                _web_runtime_config(settings, name),
            )
            for name in native_web.specs()
        ]
        web_tools = [tool for tool in web_tools if _tool_enabled(settings, tool.spec.name)]
        if web_tools and _provider_enabled(settings, "astra.web"):
            contributions.append(
                _provider(
                    "astra.web",
                    web_tools,
                    configuration_schema=_web_configuration_schema(),
                    configuration_revision=_configuration_revision(settings, "astra.web"),
                    result_adapter=("legacy.raw.v0", LegacyRawResultAdapter),
                    analyzers=[("web.effect", ("web_search", "web_fetch"), WebEffectAnalyzer)],
                    processors=[("web.result", ("web_search", "web_fetch"), WebResultProcessor)],
                    validators=[
                        ("web.validator", ("web_search", "web_fetch"), WebEvidenceValidator)
                    ],
                )
            )
        if (
            settings.tool_chart_render_enabled
            and _tool_enabled(settings, "chart.render")
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
        if (
            settings.tool_bash_execute_enabled
            and _tool_enabled(settings, "bash_execute")
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


def _tool_enabled(settings: AstraRuntimeSettings, tool_name: str) -> bool:
    aliases = {"chart.render": "chart_render"}
    return settings.tool_states.get(
        tool_name,
        settings.tool_states.get(aliases.get(tool_name, tool_name), True),
    )


def _configuration_revision(settings: AstraRuntimeSettings, provider_id: str) -> str:
    return settings.tool_provider_configuration_revisions.get(provider_id, "default")


def _web_configuration_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "search_provider": {
                "type": "string",
                "enum": ["auto", "google", "duckduckgo"],
                "title": "Search provider",
            },
            "search_credential": {
                "type": "object",
                "title": "Search credential",
                "x-secret": True,
                "properties": {
                    "credential_ref": {"type": "string", "minLength": 1, "maxLength": 240}
                },
                "required": ["credential_ref"],
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }


def _web_runtime_config(settings: AstraRuntimeSettings, tool_name: str) -> dict[str, str]:
    """Provider-owned configuration allowlist for its isolated executors."""
    common = {"ALLOW_NETWORK_READ": "true"}
    configs = {
        "web_search": {
            "WEB_SEARCH_PROVIDER": settings.web_search_provider,
            "WEB_SEARCH_API_KEY": settings.web_search_api_key,
            "GOOGLE_SEARCH_API_KEY": settings.google_search_api_key,
            "GOOGLE_SEARCH_ENGINE_ID": settings.google_search_engine_id,
            "GOOGLE_SEARCH_RESULT_COUNT": str(settings.google_search_result_count),
            "GOOGLE_SEARCH_LANGUAGE": settings.google_search_language,
            "GOOGLE_SEARCH_REGION": settings.google_search_region,
            "GOOGLE_SEARCH_SAFE": settings.google_search_safe,
        },
        "web_fetch": {
            "CRAWLER_MAX_CONTENT_CHARS": str(settings.crawler_max_content_chars),
            "CRAWLER_MAX_RESPONSE_BYTES": str(settings.crawler_max_response_bytes),
            "CRAWLER_MIN_QUALITY_CHARS": str(settings.crawler_min_quality_chars),
            "CRAWLER_ALLOW_PROXY_FAKE_IP": (
                "true" if settings.crawler_allow_proxy_fake_ip else "false"
            ),
        },
    }
    if tool_name not in configs:
        raise ValueError(f"Unsupported astra.web tool: {tool_name}")
    return {**common, **configs[tool_name]}


def _provider(
    provider_id: str,
    tools: Iterable[AstraTool],
    *,
    analyzers=(),
    processors=(),
    validators=(),
    presenters=(),
    result_adapter: tuple[str, type] | None = None,
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
                result_adapter_id=(result_adapter[0] if result_adapter else "envelope.v1"),
                result_adapter_factory=(result_adapter[1] if result_adapter else None),
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
