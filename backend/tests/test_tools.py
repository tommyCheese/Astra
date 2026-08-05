import os
from unittest.mock import AsyncMock

import httpx
import pytest

from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolArtifactReference,
    ToolExecutionError,
    ToolResultEnvelope,
)
from app.infrastructure.tools.web import build_web_registry
from app.infrastructure.tools.web.content import extract_source
from app.infrastructure.tools.web.fetching import WebFetchTool
from app.infrastructure.tools.web.providers import (
    DuckDuckGoHTMLParser,
    normalize_bing_rss,
    normalize_google_items,
    normalize_search_result_url,
)
from app.infrastructure.tools.web.search import WebSearchTool


async def test_unconfigured_web_search_provider_is_rejected():
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="unsupported"))
    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})
    assert exc_info.value.category == "provider_not_configured"


async def test_web_search_rejects_empty_query():
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="mock"))

    with pytest.raises(ToolExecutionError):
        await tool.run({"query": ""})


async def test_web_search_batches_logical_queries_and_preserves_lineage(monkeypatch):
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="google"))

    async def fake_search(query, _tool_input):
        return {
            "query": query,
            "provider": "google",
            "provider_mode": "explicit",
            "provider_attempts": [
                {"provider": "google", "status": "succeeded", "candidate_count": 1}
            ],
            "degraded": False,
            "parameters": {},
            "candidate_count": 1,
            "warnings": [],
            "candidates": [
                {
                    "url": f"https://docs.example.com/{query.lower()}?utm_source=test",
                    "title": query,
                    "snippet": "Candidate only",
                    "rank": 1,
                    "provider": "google",
                }
            ],
        }

    monkeypatch.setattr(tool.provider_client, "search_one", fake_search)
    output = await tool.run(
        {
            "queries": [
                {"query": "Alpha", "purpose": "primary"},
                {"query": "Beta", "purpose": "comparison"},
            ],
            "num_results": 3,
            "filters": {
                "include_domains": ["example.com"],
                "after": "2026-01-01",
            },
        }
    )

    assert output["query_count"] == 2
    assert len(output["search_traces"]) == 2
    assert len(output["candidates"]) == 2
    assert {item["search_trace_id"] for item in output["candidates"]} == {
        item["id"] for item in output["search_traces"]
    }
    assert output["constraint_audit"]["post_filtered"] == ["include_domains"]
    assert output["constraint_audit"]["unsupported"] == ["after"]
    assert all(
        item["evidence_strength"] == "candidate_only"
        for item in output["candidates"]
    )


async def test_web_search_reports_provider_unsupported_region_truthfully(monkeypatch):
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="bing"))

    async def fake_search(query, _tool_input):
        return {
            "query": query,
            "provider": "bing",
            "provider_mode": "explicit",
            "provider_attempts": [],
            "degraded": True,
            "candidates": [],
        }

    monkeypatch.setattr(tool.provider_client, "search_one", fake_search)
    output = await tool.run({"query": "Astra", "region": "CN"})

    assert "region" not in output["constraint_audit"]["applied"]
    assert "region" in output["constraint_audit"]["unsupported"]


def test_duckduckgo_html_results_are_normalized():
    parser = DuckDuckGoHTMLParser()
    parser.feed(
        """
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">
            Example article
          </a>
          <a class="result__snippet">A useful result snippet.</a>
        </div>
        """
    )

    assert parser.results == [
        {
            "url": "https://example.com/article",
            "title": "Example article",
            "snippet": "A useful result snippet.",
        }
    ]
    assert normalize_search_result_url("https://example.org/path") == "https://example.org/path"


async def test_duckduckgo_web_search_uses_real_provider_response(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            request = httpx.Request("GET", "https://html.duckduckgo.com/html/")
            return httpx.Response(
                200,
                request=request,
                text=(
                    '<a class="result__a" href="https://example.com/a">Example</a>'
                    '<a class="result__snippet">Snippet</a>'
                ),
            )

    monkeypatch.setattr("app.infrastructure.tools.web.providers.httpx.AsyncClient", FakeClient)
    output = await WebSearchTool(AstraRuntimeSettings(web_search_provider="duckduckgo")).run(
        {"query": "Astra", "num_results": 1}
    )

    assert output["provider"] == "duckduckgo"
    assert output["candidate_count"] == 1
    assert output["candidates"][0]["url"] == "https://example.com/a"


def test_bing_rss_results_are_normalized():
    candidates = normalize_bing_rss(
        """<?xml version="1.0"?><rss><channel><item>
        <title>Example</title><link>https://example.com/a</link>
        <description>Snippet</description></item></channel></rss>"""
    )

    assert candidates[0]["provider"] == "bing"
    assert candidates[0]["url"] == "https://example.com/a"
    assert candidates[0]["snippet"] == "Snippet"


async def test_bing_web_search_uses_rss_response(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def get(self, *args, **kwargs):
            request = httpx.Request("GET", "https://www.bing.com/search")
            return httpx.Response(
                200,
                request=request,
                text=(
                    "<rss><channel><item><title>Example</title>"
                    "<link>https://example.com/a</link>"
                    "<description>Snippet</description></item></channel></rss>"
                ),
            )

    monkeypatch.setattr("app.infrastructure.tools.web.providers.httpx.AsyncClient", FakeClient)
    output = await WebSearchTool(AstraRuntimeSettings(web_search_provider="bing")).run(
        {"query": "Astra", "num_results": 1}
    )

    assert output["provider"] == "bing"
    assert output["candidate_count"] == 1
    assert output["provider_mode"] == "explicit"
    assert output["provider_attempts"] == [
        {"provider": "bing", "status": "succeeded", "candidate_count": 1}
    ]
    assert output["degraded"] is True


def search_output(provider: str, candidate_count: int = 1) -> dict:
    return {
        "query": "Astra",
        "provider": provider,
        "parameters": {},
        "candidate_count": candidate_count,
        "warnings": [],
        "candidates": [{}] * candidate_count,
    }


async def test_auto_web_search_selects_google_with_dedicated_credentials(monkeypatch):
    tool = WebSearchTool(
        AstraRuntimeSettings(
            web_search_provider="auto",
            google_search_api_key="google-secret",
            google_search_engine_id="cx",
            web_search_api_key="brave-secret",
        )
    )
    google = AsyncMock(return_value=search_output("google"))
    brave = AsyncMock(return_value=search_output("brave"))
    monkeypatch.setattr(tool.provider_client, "_google_search", google)
    monkeypatch.setattr(tool.provider_client, "_brave_search", brave)

    output = await tool.run({"query": "Astra"})

    assert output["provider"] == "google"
    assert output["provider_mode"] == "auto"
    assert output["degraded"] is False
    assert "google-secret" not in str(output)
    google.assert_awaited_once()
    brave.assert_not_awaited()


async def test_auto_web_search_selects_brave_from_generic_search_key(monkeypatch):
    tool = WebSearchTool(
        AstraRuntimeSettings(
            web_search_provider="auto",
            google_search_api_key="",
            google_search_engine_id="",
            web_search_api_key="brave-secret",
        )
    )
    brave = AsyncMock(return_value=search_output("brave"))
    bing = AsyncMock(return_value=search_output("bing"))
    monkeypatch.setattr(tool.provider_client, "_brave_search", brave)
    monkeypatch.setattr(tool.provider_client, "_bing_search", bing)

    output = await tool.run({"query": "Astra"})

    assert output["provider"] == "brave"
    assert output["provider_attempts"][0]["provider"] == "brave"
    assert output["degraded"] is False
    brave.assert_awaited_once()
    bing.assert_not_awaited()


async def test_auto_keyless_search_stops_after_successful_bing(monkeypatch):
    tool = WebSearchTool(
        AstraRuntimeSettings(
            web_search_provider="auto",
            google_search_api_key="",
            google_search_engine_id="",
            web_search_api_key="",
        )
    )
    bing = AsyncMock(return_value=search_output("bing"))
    duckduckgo = AsyncMock(return_value=search_output("duckduckgo"))
    monkeypatch.setattr(tool.provider_client, "_bing_search", bing)
    monkeypatch.setattr(tool.provider_client, "_duckduckgo_search", duckduckgo)

    output = await tool.run({"query": "Astra"})

    assert output["provider"] == "bing"
    assert output["provider_attempts"] == [
        {"provider": "bing", "status": "succeeded", "candidate_count": 1}
    ]
    assert output["degraded"] is True
    assert any("不保证商业生产环境" in warning for warning in output["warnings"])
    duckduckgo.assert_not_awaited()


@pytest.mark.parametrize("bing_failure", ["empty", "error"])
async def test_auto_keyless_search_falls_back_to_duckduckgo(monkeypatch, bing_failure):
    tool = WebSearchTool(
        AstraRuntimeSettings(
            web_search_provider="auto",
            google_search_api_key="",
            google_search_engine_id="",
            web_search_api_key="",
        )
    )
    if bing_failure == "empty":
        bing = AsyncMock(return_value=search_output("bing", candidate_count=0))
    else:
        bing = AsyncMock(side_effect=ToolExecutionError("search_failed", "provider detail"))
    duckduckgo = AsyncMock(return_value=search_output("duckduckgo"))
    monkeypatch.setattr(tool.provider_client, "_bing_search", bing)
    monkeypatch.setattr(tool.provider_client, "_duckduckgo_search", duckduckgo)

    output = await tool.run({"query": "Astra"})

    assert output["provider"] == "duckduckgo"
    assert [attempt["provider"] for attempt in output["provider_attempts"]] == [
        "bing",
        "duckduckgo",
    ]
    assert output["provider_attempts"][0]["status"] == (
        "empty" if bing_failure == "empty" else "failed"
    )
    assert any("回退到 DuckDuckGo" in warning for warning in output["warnings"])


async def test_auto_keyless_search_aggregates_provider_failures_without_details(monkeypatch):
    tool = WebSearchTool(
        AstraRuntimeSettings(
            web_search_provider="auto",
            google_search_api_key="",
            google_search_engine_id="",
            web_search_api_key="",
        )
    )
    monkeypatch.setattr(
        tool.provider_client,
        "_bing_search",
        AsyncMock(side_effect=ToolExecutionError("search_failed", "bing-secret-detail")),
    )
    monkeypatch.setattr(
        tool.provider_client,
        "_duckduckgo_search",
        AsyncMock(side_effect=ToolExecutionError("search_failed", "duck-secret-detail")),
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})

    assert exc_info.value.category == "search_failed"
    assert "bing:search_failed" in exc_info.value.message
    assert "duckduckgo:search_failed" in exc_info.value.message
    assert "secret-detail" not in exc_info.value.message


async def test_explicit_provider_failure_does_not_fall_back(monkeypatch):
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="google"))
    bing = AsyncMock(return_value=search_output("bing"))
    duckduckgo = AsyncMock(return_value=search_output("duckduckgo"))
    monkeypatch.setattr(tool.provider_client, "_bing_search", bing)
    monkeypatch.setattr(tool.provider_client, "_duckduckgo_search", duckduckgo)

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})

    assert exc_info.value.category == "missing_credentials"
    bing.assert_not_awaited()
    duckduckgo.assert_not_awaited()


async def test_google_web_search_requires_credentials():
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="google"))

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

    monkeypatch.setattr("app.infrastructure.tools.web.providers.httpx.AsyncClient", FakeClient)
    tool = WebSearchTool(
        AstraRuntimeSettings(
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
        AstraRuntimeSettings(
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

    monkeypatch.setattr("app.infrastructure.tools.web.providers.httpx.AsyncClient", FakeClient)
    tool = WebSearchTool(AstraRuntimeSettings(web_search_provider="brave", web_search_api_key="secret"))

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"query": "Astra"})

    assert exc_info.value.category == "search_failed"


@pytest.mark.skipif(
    os.getenv("ASTRA_RUN_GOOGLE_INTEGRATION") != "1",
    reason="Set ASTRA_RUN_GOOGLE_INTEGRATION=1 with Google credentials to run.",
)
async def test_google_web_search_real_integration():
    tool = WebSearchTool(
        AstraRuntimeSettings(
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
    specs = build_web_registry(AstraRuntimeSettings()).specs()

    assert specs["web_search"].description
    assert specs["web_search"].timeout_seconds > 0
    assert specs["web_search"].retry_policy
    assert "missing_credentials" in specs["web_search"].error_categories
    assert specs["web_fetch"].description
    assert specs["web_search"].capabilities == ["network_read"]
    assert specs["web_search"].permissions == ["network_read"]


def test_web_tool_switches_control_registration():
    registry = build_web_registry(
        AstraRuntimeSettings(tool_web_search_enabled=False, tool_web_fetch_enabled=True)
    )

    assert "web_search" not in registry.specs()
    assert "web_fetch" in registry.specs()
    with pytest.raises(ToolExecutionError) as exc_info:
        registry.get("web_search")
    assert exc_info.value.category == "tool_not_allowed"


def test_tool_contract_serializes_artifact_envelope_and_legacy_permissions():
    spec = AstraToolSpec(
        name="legacy",
        version="1",
        input_schema={},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
    )
    result = ToolResultEnvelope(
        artifacts=[ToolArtifactReference(id="a1", type="chart", mime_type="image/png")]
    )
    assert spec.capabilities == ["network_read"]
    assert result.model_dump()["artifacts"][0]["id"] == "a1"


def test_tool_registries_are_composable():
    class ExampleTool(AstraTool):
        spec = AstraToolSpec(
            name="example",
            version="1",
            input_schema={},
            output_schema={},
            permission="network_read",
            side_effect_level="read_only",
        )

        async def run(self, tool_input, *, context=None):
            return {}

    extra = AstraToolRegistry().extend([ExampleTool()])
    registry = AstraToolRegistry.compose(build_web_registry(AstraRuntimeSettings()), extra)
    assert {"web_search", "web_fetch", "example"} <= set(registry.specs())


async def test_web_fetch_respects_network_permission():
    tool = WebFetchTool(AstraRuntimeSettings(allow_network_read=False))
    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"url": "https://example.com/source"})
    assert exc_info.value.category == "permission_denied"


async def test_web_fetch_rejects_empty_url():
    tool = WebFetchTool(AstraRuntimeSettings())

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
    tool = WebFetchTool(AstraRuntimeSettings())

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run({"url": url})

    assert exc_info.value.category in {"invalid_input", "permission_denied"}


async def test_web_fetch_revalidates_redirect_targets(monkeypatch):
    class Resolver:
        async def getaddrinfo(self, _host, port, **_kwargs):
            return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr("app.infrastructure.tools.web.security.asyncio.get_running_loop", lambda: Resolver())

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://127.0.0.1/private"}, request=request
        )

    tool = WebFetchTool(AstraRuntimeSettings())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool._get_with_safe_redirects(client, "https://example.com/source")

    assert exc_info.value.category == "permission_denied"


async def test_web_fetch_rejects_hostnames_resolving_to_private_addresses(monkeypatch):
    class Resolver:
        async def getaddrinfo(self, _host, port, **_kwargs):
            return [(2, 1, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr("app.infrastructure.tools.web.security.asyncio.get_running_loop", lambda: Resolver())
    tool = WebFetchTool(AstraRuntimeSettings())
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as client:
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool._get_with_safe_redirects(client, "https://public.example/source")

    assert exc_info.value.category == "permission_denied"


async def test_web_fetch_proxy_fake_ip_compatibility_is_explicit(monkeypatch):
    class Resolver:
        async def getaddrinfo(self, _host, port, **_kwargs):
            return [(2, 1, 6, "", ("198.18.0.42", port))]

    monkeypatch.setattr("app.infrastructure.tools.web.security.asyncio.get_running_loop", lambda: Resolver())

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"public content",
            request=request,
        )

    strict_tool = WebFetchTool(AstraRuntimeSettings(crawler_allow_proxy_fake_ip=False))
    compatible_tool = WebFetchTool(AstraRuntimeSettings(crawler_allow_proxy_fake_ip=True))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ToolExecutionError):
            await strict_tool._get_with_safe_redirects(client, "https://public.example/")
        response = await compatible_tool._get_with_safe_redirects(client, "https://public.example/")

    assert response.body == b"public content"


async def test_web_fetch_streams_with_a_hard_response_limit(monkeypatch):
    async def allow_public_target(_url, **_kwargs):
        return {"93.184.216.34"}

    monkeypatch.setattr("app.infrastructure.tools.web.fetching.validate_public_http_target", allow_public_target)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"0123456789",
            request=request,
        )

    tool = WebFetchTool(AstraRuntimeSettings(crawler_max_response_bytes=8))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool._get_with_safe_redirects(
                client, "https://example.com/large", max_response_bytes=8
            )

    assert exc_info.value.category == "response_too_large"


async def test_web_fetch_rejects_non_text_content(monkeypatch):
    async def allow_public_target(_url, **_kwargs):
        return {"93.184.216.34"}

    monkeypatch.setattr("app.infrastructure.tools.web.fetching.validate_public_http_target", allow_public_target)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"not-an-image",
            request=request,
        )

    tool = WebFetchTool(AstraRuntimeSettings())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ToolExecutionError) as exc_info:
            await tool._get_with_safe_redirects(client, "https://example.com/image")

    assert exc_info.value.category == "unsupported_content_type"


async def test_web_fetch_records_the_validated_final_url(monkeypatch):
    async def allow_public_target(_url, **_kwargs):
        return {"93.184.216.34"}

    monkeypatch.setattr("app.infrastructure.tools.web.fetching.validate_public_http_target", allow_public_target)

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body><main>done</main></body></html>",
            request=request,
        )

    tool = WebFetchTool(AstraRuntimeSettings())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        response = await tool._get_with_safe_redirects(client, "https://example.com/start")

    assert response.requested_url == "https://example.com/start"
    assert response.final_url == "https://example.com/final"
    assert response.redirect_count == 1


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
    assert output["source_id"].startswith("src_")
    assert output["snapshot_id"].startswith("snap_")
    assert len(output["content_digest"]) == 64
    assert output["segmentation_version"] == "passages.v1"
    assert output["passages"]
    assert output["passages"][0]["source_id"] == output["source_id"]


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


def test_trafilatura_extracts_main_content_and_audits_fetch_metadata():
    output = extract_source(
        url="https://example.com/final",
        requested_url="http://example.com/start",
        redirect_count=1,
        response_bytes=512,
        status_code=200,
        body="""
        <html><head><title>Focused report</title></head><body>
          <nav>Navigation boilerplate that should not become evidence.</nav>
          <article>
            <h1>Focused report</h1>
            <p>This is the substantive Astra article content with enough detail to extract.</p>
            <p>It contains a second paragraph so the main-content detector has context.</p>
          </article>
          <footer>Footer boilerplate that should not become evidence.</footer>
        </body></html>
        """,
        content_type="text/html; charset=utf-8",
        query="Astra article",
        snippet="",
        crawler_plan={"strategy": "trafilatura"},
        max_chars=12000,
        min_quality_chars=20,
    )

    assert output["extraction_strategy"] == "trafilatura"
    assert "substantive Astra article" in output["content"]
    assert "Navigation boilerplate" not in output["content"]
    assert output["requested_url"] == "http://example.com/start"
    assert output["final_url"] == "https://example.com/final"
    assert output["metadata"]["redirect_count"] == 1
    assert output["metadata"]["response_bytes"] == 512
