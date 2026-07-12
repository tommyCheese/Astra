from app.core.config import Settings
from app.tools.base import ToolRegistry
from app.tools.chart import ChartRenderTool
from app.tools.web import build_web_registry


def build_tool_registry(settings: Settings) -> ToolRegistry:
    registries = [build_web_registry(settings)]
    if settings.sandbox_enabled and sandbox_available(settings):
        chart = ToolRegistry().extend([ChartRenderTool(settings)])
        registries.append(chart)
    return ToolRegistry.compose(*registries)


def sandbox_available(settings: Settings) -> bool:
    if settings.sandbox_skip_availability_check:
        return True
    return settings.sandbox_provider == "e2b" and bool(settings.e2b_api_key and settings.e2b_template_id)
