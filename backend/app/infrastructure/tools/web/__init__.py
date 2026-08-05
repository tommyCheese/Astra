"""Public web search and secure retrieval tools."""

from typing import TYPE_CHECKING

from app.infrastructure.tools.web.fetching import WebFetchTool
from app.infrastructure.tools.web.search import WebSearchTool

if TYPE_CHECKING:
    from app.common.core.config import AstraRuntimeSettings


def build_web_registry(settings: "AstraRuntimeSettings"):
    from app.infrastructure.tools.base import AstraToolRegistry

    registry = AstraToolRegistry()
    if settings.tool_web_search_enabled:
        registry.register(WebSearchTool(settings))
    if settings.tool_web_fetch_enabled:
        registry.register(WebFetchTool(settings))
    return registry
