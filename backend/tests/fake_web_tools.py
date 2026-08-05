from app.infrastructure.tools.base import Tool, ToolRegistry, ToolSpec


class FakeSearch(Tool):
    spec = ToolSpec(
        name="web_search",
        version="test",
        input_schema={"required": ["query"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.search", "source.discover"],
    )

    async def run(self, tool_input, *, context=None):
        self.last_context = context
        return {
            "query": tool_input["query"],
            "provider": "test",
            "candidate_count": 1,
            "warnings": [],
            "candidates": [
                {
                    "url": "https://test.invalid/source",
                    "title": "Test",
                    "snippet": "Evidence",
                    "provider": "test",
                    "retrieved_at": "now",
                }
            ],
        }


class FakeFetch(Tool):
    spec = ToolSpec(
        name="web_fetch",
        version="test",
        input_schema={"required": ["url"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["information.read", "source.retrieve", "evidence.extract"],
    )

    async def run(self, tool_input, *, context=None):
        return {
            "url": tool_input["url"],
            "status_code": 200,
            "title": "Test",
            "content": "Deterministic test evidence",
            "quality_score": 0.9,
            "extraction_strategy": "test",
            "warnings": [],
            "retrieved_at": "now",
        }


def fake_web_registry():
    registry = ToolRegistry()
    registry.register(FakeSearch())
    registry.register(FakeFetch())
    return registry
