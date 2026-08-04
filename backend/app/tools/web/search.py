"""Search the web through configured providers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.tools.base import Tool, ToolSpec
from app.tools.web.providers import SearchProviderClient
from app.tools.web.results import SearchResultNormalizer, combine_outputs

if TYPE_CHECKING:
    from app.core.config import Settings


class WebSearchTool(Tool):
    spec = ToolSpec(
        name="web_search",
        version="0.4.0",
        description="Search the web through a configured provider and return candidate sources.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "queries": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "required": ["query"],
                                "properties": {
                                    "query": {"type": "string"},
                                    "purpose": {"type": "string"},
                                },
                            },
                        ]
                    },
                },
                "num_results": {"type": "integer"},
                "language": {"type": "string"},
                "region": {"type": "string"},
                "filters": {
                    "type": "object",
                    "properties": {
                        "after": {"type": "string"},
                        "before": {"type": "string"},
                        "include_domains": {"type": "array", "items": {"type": "string"}},
                        "exclude_domains": {"type": "array", "items": {"type": "string"}},
                        "content_types": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
        output_schema={"type": "object", "required": ["query", "provider", "candidates"]},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.search", "source.discover"],
        timeout_seconds=20,
        retry_policy={"max_attempts": 1},
        error_categories=["invalid_input", "missing_credentials", "search_failed"],
    )

    def __init__(self, settings: Settings):
        self.provider_client = SearchProviderClient(settings)
        self.result_normalizer = SearchResultNormalizer(self.provider_client.search_parameters)

    async def run(self, tool_input: dict[str, Any], *, context=None) -> dict[str, Any]:
        requests = self.result_normalizer.logical_queries(tool_input)
        constraints = self.result_normalizer.constraints(tool_input)
        invocation_scope = str(getattr(context, "tool_call_id", "") or "") or None
        normalized_input = {
            **tool_input,
            "num_results": constraints["max_results"],
            "language": constraints["language"],
            "region": constraints["region"],
        }
        outputs = await asyncio.gather(
            *(
                self.provider_client.search_one(request["query"], normalized_input)
                for request in requests
            )
        )
        decorated = [
            self.result_normalizer.decorate(
                output,
                request=request,
                ordinal=index,
                constraints=constraints,
                invocation_scope=invocation_scope,
            )
            for index, (request, output) in enumerate(zip(requests, outputs, strict=True))
        ]
        if len(decorated) == 1:
            return decorated[0]
        return combine_outputs(decorated)
