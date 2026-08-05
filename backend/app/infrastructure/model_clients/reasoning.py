"""Provider-specific model thinking capabilities and request configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.common.schemas.agent.types import ReasoningEffort
from app.common.schemas.models import (
    MODEL_THINKING_CAPABILITY_VERSION,
    EffectiveModelThinking,
    ModelThinkingAdjustment,
    ModelThinkingCapability,
    ModelThinkingDepth,
    ModelThinkingDepthOption,
    ModelThinkingSelection,
    ModelThinkingSnapshot,
    ModelThinkingToggle,
)
from app.domain.agent_profile import ModelOperation


@dataclass(frozen=True)
class ModelReasoningConfig:
    adapter: str
    effort: ReasoningEffort
    operation: ModelOperation
    request_params: dict[str, Any] = field(default_factory=dict)
    include_json_mode: bool = True
    reason: str | None = None
    source: str = "model_default"
    enabled: bool = False
    depth: ModelThinkingDepth | None = None
    intrinsic: bool = False
    adjustments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return bool(self.request_params)

    def usage_metadata(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "effort": self.effort.value,
            "operation": self.operation.value,
            "source": self.source,
            "enabled": self.enabled,
            "depth": self.depth,
            "applied": self.applied,
            "intrinsic": self.intrinsic,
            "request_params": self.request_params,
            "json_mode": self.include_json_mode,
            "reason": self.reason,
            "adjustments": self.adjustments,
            "thinking_content_visibility": self.thinking_content_visibility,
        }

    @property
    def thinking_content_visibility(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.adapter.startswith("anthropic-"):
            return "summary"
        if self.adapter.startswith("qwen-") or self.adapter == "deepseek-v4-thinking":
            return "reasoning"
        return "unavailable"


_QWEN_THINKING_BUDGET: dict[ModelThinkingDepth, int] = {
    "low": 1024,
    "medium": 2048,
    "high": 8192,
}

_CLAUDE_MANUAL_THINKING_BUDGET: dict[ModelThinkingDepth, int] = {
    "low": 2048,
    "medium": 8192,
    "high": 16_384,
}

_OPENAI_PRO_MODEL_BASES = (
    "gpt-5-pro",
    "gpt-5.2-pro",
    "gpt-5.4-pro",
    "gpt-5.5-pro",
)

_QWEN_THINKING_ONLY_MODELS = {
    "qwen3.8-max-preview",
    "qwen3.7-max-preview",
    "qwen3.7-max-2026-05-17",
}


def model_thinking_capability(*, provider: str, model: str) -> ModelThinkingCapability:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()

    if normalized_provider == "openai":
        capability = _openai_thinking_capability(provider, model, normalized_model)
        if capability is not None:
            return capability

    if normalized_provider == "anthropic":
        capability = _anthropic_thinking_capability(provider, model, normalized_model)
        if capability is not None:
            return capability

    if normalized_provider == "qwen":
        capability = _qwen_thinking_capability(provider, model, normalized_model)
        if capability is not None:
            return capability
    if normalized_provider == "deepseek":
        capability = _deepseek_thinking_capability(provider, model, normalized_model)
        if capability is not None:
            return capability

    reason = _unsupported_reason(normalized_provider)
    return _unavailable_capability(
        provider,
        model,
        adapter=_unsupported_adapter(normalized_provider),
        reason=reason,
    )


def _openai_thinking_capability(provider, model, normalized_model):
    if _is_openai_pro_model(normalized_model):
        return _unavailable_capability(
            provider,
            model,
            adapter="openai-responses-required",
            reason="responses_api_required_for_pro_model",
        )
    if _is_openai_gpt5_base_family(normalized_model):
        return _capability(
            provider,
            model,
            toggle="always_on",
            depths=("minimal", "low", "medium", "high"),
            default_depth="medium",
            adapter="openai-gpt5",
        )
    families = (
        (("gpt-5.1",), ("low", "medium", "high"), False),
        (
            ("gpt-5.2", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"),
            ("low", "medium", "high", "xhigh"),
            False,
        ),
        (("gpt-5.5",), ("low", "medium", "high", "xhigh"), True),
    )
    for bases, depths, enabled in families:
        if _is_openai_family(normalized_model, bases):
            return _capability(
                provider,
                model,
                toggle="optional",
                depths=depths,
                default_depth="medium",
                default_enabled=enabled,
                adapter="openai-gpt5-modern",
            )
    if normalized_model in {"gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
        return _capability(
            provider,
            model,
            toggle="optional",
            depths=("low", "medium", "high", "xhigh", "max"),
            default_depth="medium",
            default_enabled=True,
            adapter="openai-gpt5-modern",
        )
    return None


def _anthropic_thinking_capability(provider, model, normalized_model):
    profile = _anthropic_thinking_profile(normalized_model)
    if profile is None:
        return None
    toggle, depths, default_enabled, adapter, default_depth = profile
    return _capability(
        provider,
        model,
        toggle=toggle,
        depths=depths,
        default_depth=default_depth,
        default_enabled=default_enabled,
        adapter=adapter,
    )


def _qwen_thinking_capability(provider, model, normalized_model):
    if _is_qwen_thinking_only(normalized_model):
        return _capability(
            provider,
            model,
            toggle="always_on",
            depths=("low", "medium", "high"),
            default_depth="medium",
            adapter="qwen-thinking-only-budget",
            reason="thinking_only_model_cannot_be_disabled",
        )
    if normalized_model == "qwq-plus":
        return _capability(
            provider,
            model,
            toggle="always_on",
            depths=("medium",),
            default_depth="medium",
            adapter="qwen-thinking-only-intrinsic",
            reason="thinking_only_model_has_no_confirmed_budget_control",
        )
    default_enabled = _qwen_hybrid_default_enabled(normalized_model)
    if default_enabled is None:
        return None
    return _capability(
        provider,
        model,
        toggle="optional",
        depths=("low", "medium", "high"),
        default_depth="medium",
        default_enabled=default_enabled,
        adapter="qwen-hybrid-thinking",
    )


def _deepseek_thinking_capability(provider, model, normalized_model):
    if normalized_model == "deepseek-reasoner":
        return _unavailable_capability(
            provider,
            model,
            adapter="deepseek-retired-model",
            reason="model_retired_migrate_to_deepseek_v4",
        )
    if normalized_model not in {"deepseek-v4-pro", "deepseek-v4-flash"}:
        return None
    return _capability(
        provider,
        model,
        toggle="optional",
        depths=("high", "xhigh"),
        default_depth="high",
        default_enabled=True,
        adapter="deepseek-v4-thinking",
    )


def normalize_model_thinking(
    *,
    provider: str,
    model: str,
    selection: ModelThinkingSelection | dict[str, Any] | None,
) -> ModelThinkingSnapshot:
    capability = model_thinking_capability(provider=provider, model=model)
    requested = _thinking_selection(selection)
    source = "explicit_model_control" if requested is not None else "model_default"
    adjustments: list[ModelThinkingAdjustment] = []
    if not capability.supported:
        return _unsupported_thinking_snapshot(capability, requested, source)
    enabled, depth = _normalize_effective_thinking(capability, requested, adjustments)

    return ModelThinkingSnapshot(
        requested=requested,
        effective=EffectiveModelThinking(enabled=enabled, depth=depth if enabled else None),
        source=source,
        adapter=capability.adapter,
        adjustments=adjustments,
        capability_version=capability.capability_version,
    )


def _thinking_selection(selection):
    if isinstance(selection, ModelThinkingSelection):
        return selection
    return ModelThinkingSelection.model_validate(selection) if selection is not None else None


def _unsupported_thinking_snapshot(capability, requested, source):
    adjustments = []
    if requested is not None:
        adjustments.append(
            ModelThinkingAdjustment(
                field="enabled",
                requested=requested.enabled,
                effective=False,
                reason=capability.reason or "thinking_control_unavailable",
            )
        )
    return ModelThinkingSnapshot(
        requested=requested,
        effective=EffectiveModelThinking(enabled=False, depth=None),
        source=source,
        adapter=capability.adapter,
        adjustments=adjustments,
        capability_version=capability.capability_version,
    )


def _normalize_effective_thinking(capability, requested, adjustments):
    if requested is None:
        return capability.default_enabled, (
            capability.default_depth if capability.default_enabled else None
        )
    enabled = requested.enabled
    depth = requested.depth if enabled else None
    if requested.capability_version != capability.capability_version:
        adjustments.append(
            ModelThinkingAdjustment(
                field="capability_version",
                requested=requested.capability_version,
                effective=capability.capability_version,
                reason="capability_version_changed",
            )
        )
    if capability.toggle == "always_on" and not enabled:
        adjustments.append(
            ModelThinkingAdjustment(
                field="enabled",
                requested=False,
                effective=True,
                reason="model_thinking_always_on",
            )
        )
        enabled, depth = True, requested.depth or capability.default_depth
    supported_depths = [item.id for item in capability.depths]
    if enabled and depth not in supported_depths:
        adjustments.append(
            ModelThinkingAdjustment(
                field="depth",
                requested=depth,
                effective=capability.default_depth,
                reason="thinking_depth_not_supported",
            )
        )
        depth = capability.default_depth
    return enabled, depth


def resolve_model_reasoning(
    *,
    provider: str,
    model: str,
    effort: ReasoningEffort | str,
    operation: ModelOperation,
    thinking: ModelThinkingSnapshot | dict[str, Any] | None = None,
) -> ModelReasoningConfig:
    normalized_effort = ReasoningEffort(effort)
    snapshot = (
        thinking
        if isinstance(thinking, ModelThinkingSnapshot)
        else ModelThinkingSnapshot.model_validate(thinking)
        if thinking is not None
        else normalize_model_thinking(provider=provider, model=model, selection=None)
    )
    effective = snapshot.effective
    adapter = snapshot.adapter
    capability = model_thinking_capability(provider=provider, model=model)
    adjustment_reason = _first_adjustment_reason(snapshot)
    common = {
        "adapter": adapter,
        "effort": normalized_effort,
        "operation": operation,
        "source": snapshot.source,
        "enabled": effective.enabled,
        "depth": effective.depth,
        "adjustments": [item.model_dump(mode="json") for item in snapshot.adjustments],
    }

    if adapter in {"openai-gpt5", "openai-gpt5-modern"}:
        return _openai_reasoning_config(common, effective, adjustment_reason)
    if adapter in {
        "anthropic-effort",
        "anthropic-adaptive-thinking",
        "anthropic-manual-thinking",
        "anthropic-manual-thinking-effort",
    }:
        return _anthropic_reasoning_config(common, effective, adjustment_reason, adapter)
    if adapter == "deepseek-v4-thinking":
        return _deepseek_reasoning_config(common, effective, adjustment_reason)
    if adapter.startswith("qwen-"):
        return _qwen_reasoning_config(common, effective, adjustment_reason, adapter)

    return ModelReasoningConfig(
        **common,
        reason=adjustment_reason or capability.reason,
    )


def _openai_reasoning_config(common, effective, reason):
    effort = effective.depth if effective.enabled else "none"
    return ModelReasoningConfig(
        **common, request_params={"reasoning_effort": effort}, reason=reason
    )


def _anthropic_reasoning_config(common, effective, reason, adapter):
    if adapter == "anthropic-effort":
        params = {"output_config": {"effort": effective.depth}} if effective.enabled else {}
    elif not effective.enabled:
        params = {"thinking": {"type": "disabled"}}
    elif adapter == "anthropic-adaptive-thinking":
        params = {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": effective.depth},
        }
    else:
        depth = effective.depth or "medium"
        params = {
            "max_tokens": 32_768,
            "thinking": {
                "type": "enabled",
                "budget_tokens": _CLAUDE_MANUAL_THINKING_BUDGET[depth],
                "display": "summarized",
            },
        }
        if adapter == "anthropic-manual-thinking-effort":
            params["output_config"] = {"effort": depth}
    return ModelReasoningConfig(**common, request_params=params, reason=reason)


def _deepseek_reasoning_config(common, effective, reason):
    params: dict[str, Any] = {"thinking": {"type": "enabled" if effective.enabled else "disabled"}}
    if effective.enabled:
        params["reasoning_effort"] = "max" if effective.depth == "xhigh" else "high"
    return ModelReasoningConfig(**common, request_params=params, reason=reason)


def _qwen_reasoning_config(common, effective, reason, adapter):
    depth = effective.depth or "medium"
    if adapter == "qwen-hybrid-thinking":
        params = {"enable_thinking": effective.enabled}
        if effective.enabled:
            params["thinking_budget"] = _QWEN_THINKING_BUDGET[depth]
        return ModelReasoningConfig(
            **common,
            request_params=params,
            include_json_mode=not effective.enabled,
            reason=reason
            or ("thinking_mode_uses_prompt_enforced_json" if effective.enabled else None),
        )
    if adapter == "qwen-thinking-only-budget":
        return ModelReasoningConfig(
            **common,
            request_params={"thinking_budget": _QWEN_THINKING_BUDGET[depth]},
            include_json_mode=False,
            intrinsic=True,
            reason=reason or "thinking_only_model_cannot_be_disabled",
        )
    return ModelReasoningConfig(
        **common,
        intrinsic=effective.enabled,
        reason=reason or "thinking_only_model_has_no_confirmed_budget_control",
    )


def attach_reasoning_usage(
    usage: dict[str, Any] | None, config: ModelReasoningConfig
) -> dict[str, Any]:
    return {**(usage or {}), "astra_reasoning": config.usage_metadata()}


def _capability(
    provider: str,
    model: str,
    *,
    toggle: ModelThinkingToggle,
    depths: tuple[ModelThinkingDepth, ...],
    default_depth: ModelThinkingDepth,
    adapter: str,
    default_enabled: bool = True,
    reason: str | None = None,
) -> ModelThinkingCapability:
    return ModelThinkingCapability(
        provider=provider,
        model=model,
        supported=True,
        toggle=toggle,
        depths=[ModelThinkingDepthOption(id=depth, label=depth) for depth in depths],
        default_enabled=default_enabled,
        default_depth=default_depth,
        reason=reason,
        adapter=adapter,
        capability_version=MODEL_THINKING_CAPABILITY_VERSION,
    )


def _unavailable_capability(
    provider: str,
    model: str,
    *,
    adapter: str,
    reason: str,
) -> ModelThinkingCapability:
    return ModelThinkingCapability(
        provider=provider,
        model=model,
        supported=False,
        toggle="unavailable",
        depths=[],
        default_enabled=False,
        default_depth=None,
        reason=reason,
        adapter=adapter,
        capability_version=MODEL_THINKING_CAPABILITY_VERSION,
    )


def _is_openai_family(model: str, bases: tuple[str, ...]) -> bool:
    return any(
        model == base
        or re.fullmatch(rf"{re.escape(base)}-\d{{4}}-\d{{2}}-\d{{2}}", model) is not None
        for base in bases
    )


def _is_openai_pro_model(model: str) -> bool:
    return _is_openai_family(model, _OPENAI_PRO_MODEL_BASES)


def _anthropic_thinking_profile(
    model: str,
) -> (
    tuple[
        ModelThinkingToggle,
        tuple[ModelThinkingDepth, ...],
        bool,
        str,
        ModelThinkingDepth,
    ]
    | None
):
    if model in {"claude-fable-5", "claude-mythos-5", "claude-mythos-preview"}:
        return (
            "always_on",
            ("low", "medium", "high", "xhigh", "max"),
            True,
            "anthropic-adaptive-thinking",
            "high",
        )
    if model in {"claude-opus-5", "claude-sonnet-5"}:
        return (
            "optional",
            ("low", "medium", "high", "xhigh", "max"),
            True,
            "anthropic-adaptive-thinking",
            "high",
        )
    if model in {"claude-opus-4-8", "claude-opus-4-7"}:
        return (
            "optional",
            ("low", "medium", "high", "xhigh", "max"),
            False,
            "anthropic-adaptive-thinking",
            "high",
        )
    if model in {"claude-opus-4-6", "claude-sonnet-4-6"}:
        return (
            "optional",
            ("low", "medium", "high", "max"),
            False,
            "anthropic-adaptive-thinking",
            "high",
        )
    if _is_claude_45_model(model, "opus"):
        return (
            "optional",
            ("low", "medium", "high"),
            False,
            "anthropic-manual-thinking-effort",
            "medium",
        )
    if _is_claude_45_model(model, "sonnet") or _is_claude_45_model(model, "haiku"):
        return (
            "optional",
            ("low", "medium", "high"),
            False,
            "anthropic-manual-thinking",
            "medium",
        )
    return None


def _is_claude_45_model(model: str, family: str) -> bool:
    base = f"claude-{family}-4-5"
    return model == base or re.fullmatch(rf"{base}-\d{{8}}", model) is not None


def _is_qwen_thinking_only(model: str) -> bool:
    return (
        model in _QWEN_THINKING_ONLY_MODELS
        or re.fullmatch(
            r"qwen3-(?:next-80b-a3b|235b-a22b|30b-a3b)-thinking(?:-\d{4})?",
            model,
        )
        is not None
    )


def _qwen_hybrid_default_enabled(model: str) -> bool | None:
    if re.fullmatch(
        r"qwen3\.7-(?:max|plus|flash)(?:-us|-\d{4}-\d{2}-\d{2})?",
        model,
    ):
        return True
    if (
        model == "qwen3.6-max-preview"
        or re.fullmatch(
            r"qwen3\.6-(?:plus|flash)(?:-\d{4}-\d{2}-\d{2})?",
            model,
        )
        or re.fullmatch(
            r"qwen3\.5-(?:plus|flash)(?:-\d{4}-\d{2}-\d{2})?",
            model,
        )
        or model
        in {
            "qwen3.5-397b-a17b",
            "qwen3.5-122b-a10b",
            "qwen3.5-27b",
            "qwen3.5-35b-a3b",
            "qwen3.6-35b-a3b",
            "qwen3-235b-a22b",
            "qwen3-32b",
            "qwen3-30b-a3b",
            "qwen3-14b",
            "qwen3-8b",
        }
    ):
        return True
    if re.fullmatch(
        r"qwen3-max(?:-preview|-\d{4}-\d{2}-\d{2})?",
        model,
    ) or re.fullmatch(
        r"qwen-(?:plus|flash|turbo)(?:-latest|-\d{4}-\d{2}-\d{2})?",
        model,
    ):
        return False
    return None


def _unsupported_reason(provider: str) -> str:
    if provider == "google":
        return "native_generate_content_transport_not_implemented"
    if provider == "deepseek":
        return "model_not_selected_for_native_reasoning"
    if provider in {"openai", "anthropic", "qwen"}:
        return "model_not_allowlisted_for_thinking_control"
    return "provider_has_no_declared_reasoning_adapter"


def _unsupported_adapter(provider: str) -> str:
    if provider == "google":
        return "gemini-transport-unsupported"
    if provider in {"openai", "anthropic", "qwen"}:
        return f"{provider}-unsupported-model"
    return "unsupported-provider"


def _first_adjustment_reason(snapshot: ModelThinkingSnapshot) -> str | None:
    return snapshot.adjustments[0].reason if snapshot.adjustments else None


def _is_openai_gpt5_base_family(model: str) -> bool:
    return _is_openai_family(
        model,
        ("gpt-5", "gpt-5-mini", "gpt-5-nano"),
    )
