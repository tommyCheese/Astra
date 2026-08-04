"""Public web search and secure retrieval tools."""

from typing import TYPE_CHECKING

from app.tools.web.fetching import WebFetchTool
from app.tools.web.search import WebSearchTool

if TYPE_CHECKING:
    from app.core.config import Settings


def build_web_registry(settings: "Settings"):
    from app.tools.base import ToolRegistry

    registry = ToolRegistry()
    if settings.tool_web_search_enabled:
        registry.register(WebSearchTool(settings))
    if settings.tool_web_fetch_enabled:
        registry.register(WebFetchTool(settings))
    return registry
