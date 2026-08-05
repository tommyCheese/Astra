"""One-version mapping between legacy fixed settings and canonical plugin identities."""

from app.common.core.config import AstraRuntimeSettings

LEGACY_TOOL_SETTING_FIELDS = {
    "web_search": "tool_web_search_enabled",
    "web_fetch": "tool_web_fetch_enabled",
    "chart_render": "tool_chart_render_enabled",
    "bash_execute": "tool_bash_execute_enabled",
    "swarm": "tool_swarm_enabled",
}
LEGACY_CANONICAL_ALIASES = {"chart.render": "chart_render"}


def default_tool_states(settings: AstraRuntimeSettings) -> dict[str, bool]:
    return {
        name: bool(getattr(settings, field))
        for name, field in LEGACY_TOOL_SETTING_FIELDS.items()
    }


def apply_tool_states(
    settings: AstraRuntimeSettings,
    states: dict[str, bool],
) -> AstraRuntimeSettings:
    result = settings.model_copy(update={"tool_states": dict(states)}, deep=True)
    for name, field in LEGACY_TOOL_SETTING_FIELDS.items():
        if name in states:
            setattr(result, field, states[name])
    return result


def persisted_tool_name(tool_name: str) -> str:
    return LEGACY_CANONICAL_ALIASES.get(tool_name, tool_name)
