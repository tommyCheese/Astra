import pytest
from pydantic import ValidationError

from app.agent_profile import ModelOperation
from app.runner.model_reasoning import (
    model_thinking_capability,
    normalize_model_thinking,
    resolve_model_reasoning,
)
from app.schemas.models import (
    MODEL_THINKING_CAPABILITY_VERSION,
    ModelThinkingSelection,
)


@pytest.mark.parametrize(
    ("provider", "model", "toggle", "depths", "adapter"),
    [
        (
            "openai",
            "gpt-5",
            "always_on",
            ["minimal", "low", "medium", "high"],
            "openai-gpt5",
        ),
        (
            "openai",
            "gpt-5.1",
            "optional",
            ["low", "medium", "high"],
            "openai-gpt5-modern",
        ),
        (
            "openai",
            "gpt-5.2",
            "optional",
            ["low", "medium", "high", "xhigh"],
            "openai-gpt5-modern",
        ),
        (
            "openai",
            "gpt-5.6-sol",
            "optional",
            ["low", "medium", "high", "xhigh", "max"],
            "openai-gpt5-modern",
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            "optional",
            ["low", "medium", "high", "max"],
            "anthropic-adaptive-thinking",
        ),
        (
            "anthropic",
            "claude-opus-5",
            "optional",
            ["low", "medium", "high", "xhigh", "max"],
            "anthropic-adaptive-thinking",
        ),
        (
            "qwen",
            "qwen3.7-plus",
            "optional",
            ["low", "medium", "high"],
            "qwen-hybrid-thinking",
        ),
        (
            "qwen",
            "qwen3.7-max-preview",
            "always_on",
            ["low", "medium", "high"],
            "qwen-thinking-only-budget",
        ),
        (
            "qwen",
            "qwq-plus",
            "always_on",
            ["medium"],
            "qwen-thinking-only-intrinsic",
        ),
        (
            "deepseek",
            "deepseek-v4-pro",
            "optional",
            ["high", "xhigh"],
            "deepseek-v4-thinking",
        ),
    ],
)
def test_model_thinking_capability_matrix(provider, model, toggle, depths, adapter):
    capability = model_thinking_capability(provider=provider, model=model)

    assert capability.supported is True
    assert capability.toggle == toggle
    assert [item.id for item in capability.depths] == depths
    assert capability.adapter == adapter
    assert capability.default_depth in depths
    assert capability.capability_version == MODEL_THINKING_CAPABILITY_VERSION


@pytest.mark.parametrize(
    ("provider", "model", "reason"),
    [
        ("openai", "gpt-4o", "model_not_allowlisted_for_thinking_control"),
        ("openai", "gpt-5-pro", "responses_api_required_for_pro_model"),
        ("openai", "gpt-5.fake", "model_not_allowlisted_for_thinking_control"),
        ("qwen", "qwen3-not-a-model", "model_not_allowlisted_for_thinking_control"),
        ("deepseek", "deepseek-v4-pro-invalid", "model_not_selected_for_native_reasoning"),
        (
            "deepseek",
            "deepseek-reasoner",
            "model_retired_migrate_to_deepseek_v4",
        ),
        (
            "anthropic",
            "foo-sonnet-4-6-bar",
            "model_not_allowlisted_for_thinking_control",
        ),
        ("google", "gemini-2.5-pro", "native_generate_content_transport_not_implemented"),
        ("unknown", "model", "provider_has_no_declared_reasoning_adapter"),
    ],
)
def test_unknown_model_thinking_capability_is_safe(provider, model, reason):
    capability = model_thinking_capability(provider=provider, model=model)

    assert capability.supported is False
    assert capability.toggle == "unavailable"
    assert capability.depths == []
    assert capability.reason == reason


def test_normalize_model_thinking_falls_back_from_stale_depth_and_version():
    snapshot = normalize_model_thinking(
        provider="qwen",
        model="qwen3.7-plus",
        selection={"enabled": True, "depth": "xhigh", "capability_version": 9},
    )

    assert snapshot.effective.enabled is True
    assert snapshot.effective.depth == "medium"
    assert [item.reason for item in snapshot.adjustments] == [
        "capability_version_changed",
        "thinking_depth_not_supported",
    ]


def test_normalize_model_thinking_keeps_forced_model_enabled():
    snapshot = normalize_model_thinking(
        provider="qwen",
        model="qwen3.7-max-preview",
        selection={
            "enabled": False,
            "capability_version": MODEL_THINKING_CAPABILITY_VERSION,
        },
    )

    assert snapshot.effective.enabled is True
    assert snapshot.effective.depth == "medium"
    assert [item.reason for item in snapshot.adjustments] == [
        "model_thinking_always_on"
    ]


def test_disabled_qwen_thinking_maps_to_provider_switch():
    snapshot = normalize_model_thinking(
        provider="qwen",
        model="qwen3.7-plus",
        selection={
            "enabled": False,
            "capability_version": MODEL_THINKING_CAPABILITY_VERSION,
        },
    )
    config = resolve_model_reasoning(
        provider="qwen",
        model="qwen3.7-plus",
        effort="deep",
        operation=ModelOperation.DECISION,
        thinking=snapshot,
    )

    assert config.request_params == {"enable_thinking": False}
    assert config.source == "explicit_model_control"
    assert config.depth is None


def test_optional_models_keep_provider_specific_defaults():
    qwen = model_thinking_capability(provider="qwen", model="qwen-plus")
    openai = model_thinking_capability(provider="openai", model="gpt-5.1")
    openai_latest = model_thinking_capability(
        provider="openai", model="gpt-5.6"
    )
    anthropic = model_thinking_capability(
        provider="anthropic", model="claude-sonnet-4-6"
    )

    assert qwen.default_enabled is False
    assert openai.default_enabled is False
    assert openai_latest.default_enabled is True
    assert anthropic.default_enabled is False


@pytest.mark.parametrize(
    ("provider", "model", "depth", "expected"),
    [
        (
            "openai",
            "gpt-5.1",
            None,
            {"reasoning_effort": "none"},
        ),
        (
            "deepseek",
            "deepseek-v4-pro",
            None,
            {"thinking": {"type": "disabled"}},
        ),
        (
            "deepseek",
            "deepseek-v4-pro",
            "xhigh",
            {
                "thinking": {"type": "enabled"},
                "reasoning_effort": "max",
            },
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            None,
            {"thinking": {"type": "disabled"}},
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            "max",
            {
                "thinking": {"type": "adaptive", "display": "omitted"},
                "output_config": {"effort": "max"},
            },
        ),
        (
            "qwen",
            "qwen3.7-max-preview",
            "high",
            {"thinking_budget": 8192},
        ),
        (
            "openai",
            "gpt-5.6-sol",
            "max",
            {"reasoning_effort": "max"},
        ),
    ],
)
def test_optional_thinking_maps_to_provider_native_fields(
    provider, model, depth, expected
):
    selection = {
        "enabled": depth is not None,
        "depth": depth,
        "capability_version": MODEL_THINKING_CAPABILITY_VERSION,
    }
    snapshot = normalize_model_thinking(
        provider=provider,
        model=model,
        selection=selection,
    )
    config = resolve_model_reasoning(
        provider=provider,
        model=model,
        effort="fast",
        operation=ModelOperation.SYNTHESIS,
        thinking=snapshot,
    )

    assert config.request_params == expected


def test_explicit_depth_is_independent_of_agent_effort():
    selection = ModelThinkingSelection(
        enabled=True,
        depth="low",
        capability_version=MODEL_THINKING_CAPABILITY_VERSION,
    )
    snapshot = normalize_model_thinking(
        provider="openai",
        model="gpt-5.2",
        selection=selection,
        legacy_effort="deep",
    )
    config = resolve_model_reasoning(
        provider="openai",
        model="gpt-5.2",
        effort="deep",
        operation=ModelOperation.PLAN,
        thinking=snapshot,
    )

    assert config.effort.value == "deep"
    assert config.request_params == {"reasoning_effort": "low"}


def test_model_thinking_selection_rejects_provider_native_fields():
    with pytest.raises(ValidationError):
        ModelThinkingSelection.model_validate(
            {
                "enabled": True,
                "depth": "high",
                "thinking_budget": 999_999,
            }
        )
