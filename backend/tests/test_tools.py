import pytest

from app.core.config import Settings
from app.tools.base import ToolExecutionError
from app.tools.web import WebFetchTool, WebSearchTool


async def test_mock_web_search_returns_candidate():
    tool = WebSearchTool(Settings(web_search_provider="mock"))
    output = await tool.run({"query": "Astra"})

    assert output["candidates"][0]["url"].startswith("https://example.com/")


async def test_web_search_rejects_empty_query():
    tool = WebSearchTool(Settings(web_search_provider="mock"))

    with pytest.raises(ToolExecutionError):
        await tool.run({"query": ""})


async def test_mock_web_fetch_returns_content():
    tool = WebFetchTool(Settings())
    output = await tool.run({"url": "https://example.com/astra-data-query"})

    assert output["status_code"] == 200
    assert "mock source" in output["content"]


async def test_web_fetch_rejects_empty_url():
    tool = WebFetchTool(Settings())

    with pytest.raises(ToolExecutionError):
        await tool.run({"url": ""})
