import pytest

from app.agent_profile import ModelOperation
from app.runner.model_reasoning import attach_reasoning_usage, resolve_model_reasoning


@pytest.mark.parametrize(
    ("effort", "expected"),
    [("fast", "medium"), ("balanced", "medium"), ("deep", "medium")],
)
def test_openai_gpt5_uses_model_default_independently_of_agent_effort(effort, expected):
    config = resolve_model_reasoning(
        provider="openai",
        model="gpt-5",
        effort=effort,
        operation=ModelOperation.DECISION_WITH_ANSWER,
    )

    assert config.request_params == {"reasoning_effort": expected}
    assert config.include_json_mode is True
    assert config.applied is True


def test_modern_openai_gpt_uses_standard_effort_levels():
    config = resolve_model_reasoning(
        provider="openai",
        model="gpt-5.6",
        effort="balanced",
        operation=ModelOperation.PLAN,
    )

    assert config.request_params == {"reasoning_effort": "medium"}


@pytest.mark.parametrize(
    ("effort", "expected_params", "json_mode"),
    [
        ("fast", {"enable_thinking": True, "thinking_budget": 2048}, False),
        (
            "balanced",
            {"enable_thinking": True, "thinking_budget": 2048},
            False,
        ),
        ("deep", {"enable_thinking": True, "thinking_budget": 2048}, False),
    ],
)
def test_qwen_hybrid_thinking_uses_model_default_independently_of_agent_effort(
    effort, expected_params, json_mode
):
    config = resolve_model_reasoning(
        provider="qwen",
        model="qwen3.7-plus",
        effort=effort,
        operation=ModelOperation.CONTRACT,
    )

    assert config.request_params == expected_params
    assert config.include_json_mode is json_mode


def test_anthropic_supported_model_maps_output_effort():
    config = resolve_model_reasoning(
        provider="anthropic",
        model="claude-sonnet-4-6",
        effort="fast",
        operation=ModelOperation.SYNTHESIS,
    )

    assert config.request_params == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize(
    ("provider", "model", "reason"),
    [
        ("deepseek", "deepseek-reasoner", "model_retired_migrate_to_deepseek_v4"),
        ("google", "gemini-3.1-pro", "native_generate_content_transport_not_implemented"),
        ("compatible", "custom-model", "provider_has_no_declared_reasoning_adapter"),
        (
            "openai",
            "gpt-5.4-pro",
            "responses_api_required_for_pro_model",
        ),
    ],
)
def test_unsupported_combinations_omit_parameters(provider, model, reason):
    config = resolve_model_reasoning(
        provider=provider,
        model=model,
        effort="deep",
        operation=ModelOperation.REFLECTION,
    )

    assert config.request_params == {}
    assert config.applied is False
    assert config.reason == reason


def test_usage_metadata_preserves_provider_usage_without_sensitive_content():
    config = resolve_model_reasoning(
        provider="openai",
        model="gpt-5",
        effort="fast",
        operation=ModelOperation.MEMORY,
    )

    usage = attach_reasoning_usage({"total_tokens": 12}, config)

    assert usage["total_tokens"] == 12
    assert usage["astra_reasoning"]["request_params"] == {"reasoning_effort": "medium"}
    assert "messages" not in usage["astra_reasoning"]
    assert "api_key" not in usage["astra_reasoning"]
