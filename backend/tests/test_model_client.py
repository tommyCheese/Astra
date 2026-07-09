import pytest

from app.core.config import Settings
from app.runner.model_client import ModelConfigurationError, MockModelClient, build_model_client


async def test_mock_model_client_returns_structured_outputs():
    client = MockModelClient()
    plan = await client.plan("查询 Astra")
    answer = await client.synthesize(
        "查询 Astra",
        [{"url": "https://example.com/a", "content": "示例内容", "retrieved_at": "now"}],
    )

    assert plan.steps
    assert "web_search" in plan.required_tools
    assert answer.sources[0].url == "https://example.com/a"


def test_real_model_requires_credentials():
    settings = Settings(model_provider="openai", model_api_key="")

    with pytest.raises(ModelConfigurationError):
        build_model_client(settings)
