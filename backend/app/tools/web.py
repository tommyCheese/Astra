from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx

from app.core.config import Settings
from app.tools.base import Tool, ToolExecutionError, ToolSpec


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebSearchTool(Tool):
    spec = ToolSpec(
        name="web_search",
        version="0.1.0",
        input_schema={"type": "object", "required": ["query"]},
        output_schema={"type": "object", "required": ["candidates"]},
        permission="network_read",
        side_effect_level="read_only",
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        query = str(tool_input.get("query", "")).strip()
        if not query:
            raise ToolExecutionError("invalid_input", "web_search requires a non-empty query")
        if self.settings.web_search_provider == "mock":
            return {
                "query": query,
                "candidates": [
                    {
                        "url": "https://example.com/astra-data-query",
                        "title": "Astra mock source",
                        "snippet": f"Mock search result for: {query}",
                        "provider": "mock",
                        "retrieved_at": iso_now(),
                    }
                ],
            }
        if self.settings.web_search_provider == "brave":
            return await self._brave_search(query)
        raise ToolExecutionError(
            "provider_not_configured",
            f"Unsupported web search provider: {self.settings.web_search_provider}",
        )

    async def _brave_search(self, query: str) -> Dict[str, Any]:
        if not self.settings.web_search_api_key:
            raise ToolExecutionError("missing_credentials", "WEB_SEARCH_API_KEY is required")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": 5},
                headers={"X-Subscription-Token": self.settings.web_search_api_key},
            )
            response.raise_for_status()
        data = response.json()
        candidates: List[Dict[str, Any]] = []
        for item in data.get("web", {}).get("results", []):
            candidates.append(
                {
                    "url": item.get("url", ""),
                    "title": item.get("title", ""),
                    "snippet": item.get("description", ""),
                    "provider": "brave",
                    "retrieved_at": iso_now(),
                }
            )
        return {"query": query, "candidates": candidates}


class WebFetchTool(Tool):
    spec = ToolSpec(
        name="web_fetch",
        version="0.1.0",
        input_schema={"type": "object", "required": ["url"]},
        output_schema={"type": "object", "required": ["url", "status_code", "content"]},
        permission="network_read",
        side_effect_level="read_only",
    )

    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(self, tool_input: Dict[str, Any]) -> Dict[str, Any]:
        url = str(tool_input.get("url", "")).strip()
        if not url:
            raise ToolExecutionError("invalid_input", "web_fetch requires a URL")
        if url.startswith("https://example.com/"):
            return {
                "url": url,
                "status_code": 200,
                "title": "Astra mock source",
                "content": (
                    "This deterministic mock source lets Astra verify the web data query "
                    "run loop locally without external network access."
                ),
                "metadata": {"content_type": "text/plain", "provider": "mock"},
                "retrieved_at": iso_now(),
            }
        if not self.settings.allow_network_read:
            raise ToolExecutionError("permission_denied", "Network read is disabled")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError("fetch_failed", str(exc)) from exc
        content_type = response.headers.get("content-type", "")
        return {
            "url": url,
            "status_code": response.status_code,
            "title": None,
            "content": response.text[:20000],
            "metadata": {"content_type": content_type},
            "retrieved_at": iso_now(),
        }


def build_web_registry(settings: Settings):
    from app.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(WebSearchTool(settings))
    registry.register(WebFetchTool(settings))
    return registry
