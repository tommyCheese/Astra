import os

import httpx
import pytest

from app.core.config import Settings
from app.tools.base import (
    ArtifactRef,
    Tool,
    ToolExecutionError,
    ToolRegistry,
    ToolResultEnvelope,
    ToolSpec,
)
from app.tools.web import (
    WebFetchTool,
    WebSearchTool,
    build_web_registry,
    extract_source,
    normalize_google_items,
)


async def test_unconfigured_web_search_provider_is_rejected():
    tool = WebSearchTool(Settings(web_search_provider="unsupported"))
    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})
    assert exc_info.value.category == "provider_not_configured"


async def test_web_search_rejects_empty_query():
    tool = WebSearchTool(Settings(web_search_provider="mock"))

    with pytest.raises(ToolExecutionError):
        await tool.run({"query": ""})


async def test_google_web_search_requires_credentials():
    tool = WebSearchTool(Settings(web_search_provider="google"))

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})

    assert exc_info.value.category == "missing_credentials"


def test_google_items_are_normalized_without_secrets():
    candidates = normalize_google_items(
        {
            "items": [
                {
                    "link": "https://example.com/a?utm_source=x",
                    "title": "Example A",
                    "snippet": "Snippet",
                    "displayLink": "example.com",
                    "cacheId": "cache-1",
                    "pagemap": {"metatags": [{"og:title": "A"}]},
                }
            ]
        }
    )

    assert candidates[0]["rank"] == 1
    assert candidates[0]["provider"] == "google"
    assert "key" not in str(candidates[0])


async def test_google_web_search_api_error(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            request = httpx.Request("GET", "https://www.googleapis.com/customsearch/v1")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

    monkeypatch.setattr("app.tools.web.httpx.AsyncClient", FakeClient)
    tool = WebSearchTool(
        Settings(
            web_search_provider="google",
            google_search_api_key="secret",
            google_search_engine_id="cx",
        )
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})

    assert exc_info.value.category == "search_failed"


async def test_google_web_search_rejects_invalid_result_count():
    tool = WebSearchTool(
        Settings(
            web_search_provider="google",
            google_search_api_key="secret",
            google_search_engine_id="cx",
        )
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra", "num_results": "many"})

    assert exc_info.value.category == "invalid_input"


async def test_brave_web_search_wraps_provider_errors(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            request = httpx.Request("GET", "https://api.search.brave.com/res/v1/web/search")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)

    monkeypatch.setattr("app.tools.web.httpx.AsyncClient", FakeClient)
    tool = WebSearchTool(Settings(web_search_provider="brave", web_search_api_key="secret"))

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})

    assert exc_info.value.category == "search_failed"


@pytest.mark.skipif(
    os.getenv("ASTRA_RUN_GOOGLE_INTEGRATION") != "1",
    reason="Set ASTRA_RUN_GOOGLE_INTEGRATION=1 with Google credentials to run.",
)
async def test_google_web_search_real_integration():
    tool = WebSearchTool(
        Settings(
            web_search_provider="google",
            google_search_api_key=os.environ["GOOGLE_SEARCH_API_KEY"],
            google_search_engine_id=os.environ["GOOGLE_SEARCH_ENGINE_ID"],
            google_search_result_count=1,
        )
    )

    output = await tool.run({"query": "Astra"})

    assert output["provider"] == "google"
    assert output["candidate_count"] >= 0


def test_web_tool_manifest_contains_operational_fields():
    specs = build_web_registry(Settings()).specs()

    assert specs["web_search"].description
    assert specs["web_search"].timeout_seconds > 0
    assert specs["web_search"].retry_policy
    assert "missing_credentials" in specs["web_search"].error_categories
    assert specs["web_fetch"].description
    assert specs["web_search"].capabilities == ["network_read"]
    assert specs["web_search"].permissions == ["network_read"]


def test_tool_contract_serializes_artifact_envelope_and_legacy_permissions():
    spec = ToolSpec(
        name="legacy",
        version="1",
        input_schema={},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
    )
    result = ToolResultEnvelope(
        artifacts=[ArtifactRef(id="a1", type="chart", mime_type="image/png")]
    )
    assert spec.capabilities == ["network_read"]
    assert result.model_dump()["artifacts"][0]["id"] == "a1"


def test_tool_registries_are_composable():
    class ExampleTool(Tool):
        spec = ToolSpec(
            name="example",
            version="1",
            input_schema={},
            output_schema={},
            permission="network_read",
            side_effect_level="read_only",
        )

        async def run(self, tool_input, *, context=None):
            return {}

    extra = ToolRegistry().extend([ExampleTool()])
    registry = ToolRegistry.compose(build_web_registry(Settings()), extra)
    assert {"web_search", "web_fetch", "example"} <= set(registry.specs())


async def test_web_fetch_respects_network_permission():
    tool = WebFetchTool(Settings(allow_network_read=False))
    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"url": "https://example.com/source"})
    assert exc_info.value.category == "permission_denied"


async def test_web_fetch_rejects_empty_url():
    tool = WebFetchTool(Settings())

    with pytest.raises(ToolExecutionError):
        await tool.run({"url": ""})


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://user:password@example.com/",
    ],
)
async def test_web_fetch_rejects_unsafe_targets(url):
    tool = WebFetchTool(Settings())

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"url": url})

    assert exc_info.value.category in {"invalid_input", "permission_denied"}


async def test_web_fetch_revalidates_redirect_targets():
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/private"}, request=request
        )

    tool = WebFetchTool(Settings())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool._get_with_safe_redirects(client, "https://example.com/source")

    assert exc_info.value.category == "permission_denied"


def test_selector_extraction_and_metadata_fixture():
    output = extract_source(
        url="https://example.com/article",
        status_code=200,
        body="""
        <html>
          <head>
            <title>Fixture</title>
            <meta name="description" content="Description" />
            <meta property="article:published_time" content="2026-07-10" />
          </head>
          <body>
            <nav>Ignore</nav>
            <article class="story"><p>Selected article body about Astra search and crawl.</p></article>
          </body>
        </html>
        """,
        content_type="text/html",
        query="Astra search",
        snippet="Fallback snippet",
        crawler_plan={
            "strategy": "selector_extract",
            "selectors": [".story", "script:bad"],
            "exclude_selectors": [],
            "target": "article",
        },
        max_chars=12000,
        min_quality_chars=20,
    )

    assert output["title"] == "Fixture"
    assert output["metadata"]["published_at"] == "2026-07-10"
    assert output["extraction_strategy"] == "selector_extract"
    assert "Selected article body" in output["content"]
    assert output["source_type"] == "article"


def test_low_quality_page_reports_warning():
    output = extract_source(
        url="https://example.com/short",
        status_code=200,
        body="<html><body><main><p>Short.</p></main></body></html>",
        content_type="text/html",
        query="Astra",
        snippet="",
        crawler_plan={"strategy": "readability"},
        max_chars=12000,
        min_quality_chars=100,
    )

    assert output["quality_score"] < 0.5
    assert output["warnings"]
