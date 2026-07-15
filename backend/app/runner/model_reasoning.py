from dataclasses import dataclass, field
from typing import Any

from app.agent_profile import ModelOperation
from app.schemas.agent import ReasoningEffort


@dataclass(frozen=True)
class ModelReasoningConfig:
    adapter: str
    effort: ReasoningEffort
    operation: ModelOperation
    request_params: dict[str, Any] = field(default_factory=dict)
    include_json_mode: bool = True
    reason: str | None = None

    @property
    def applied(self) -> bool:
        return bool(self.request_params)

    def usage_metadata(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "effort": self.effort.value,
            "operation": self.operation.value,
            "applied": self.applied,
            "request_params": self.request_params,
            "json_mode": self.include_json_mode,
            "reason": self.reason,
        }


_ASTRA_TO_OPENAI_LEGACY = {
    ReasoningEffort.fast: "minimal",
    ReasoningEffort.balanced: "low",
    ReasoningEffort.deep: "high",
}

_ASTRA_TO_STANDARD_EFFORT = {
    ReasoningEffort.fast: "low",
    ReasoningEffort.balanced: "medium",
    ReasoningEffort.deep: "high",
}

_QWEN_THINKING_BUDGET = {
    ReasoningEffort.balanced: 2048,
    ReasoningEffort.deep: 8192,
}

_CLAUDE_EFFORT_MODEL_MARKERS = (
    "fable-5",
    "mythos",
    "opus-4-5",
    "opus-4-6",
    "opus-4-7",
    "opus-4-8",
    "sonnet-4-6",
    "sonnet-5",
)


def resolve_model_reasoning(
    *,
    provider: str,
    model: str,
    effort: ReasoningEffort | str,
    operation: ModelOperation,
) -> ModelReasoningConfig:
    normalized_provider = provider.strip().lower()
    normalized_model = model.strip().lower()
    normalized_effort = ReasoningEffort(effort)

    if normalized_provider == "openai":
        if _is_legacy_gpt5(normalized_model):
            return ModelReasoningConfig(
                adapter="openai-gpt5",
                effort=normalized_effort,
                operation=operation,
                request_params={
                    "reasoning_effort": _ASTRA_TO_OPENAI_LEGACY[normalized_effort]
                },
            )
        if normalized_model.startswith("gpt-5."):
            return ModelReasoningConfig(
                adapter="openai-gpt5-modern",
                effort=normalized_effort,
                operation=operation,
                request_params={
                    "reasoning_effort": _ASTRA_TO_STANDARD_EFFORT[normalized_effort]
                },
            )
        return _unsupported(
            normalized_effort,
            operation,
            adapter="openai-unsupported-model",
            reason="model_not_allowlisted_for_reasoning_effort",
        )

    if normalized_provider == "anthropic":
        if any(marker in normalized_model for marker in _CLAUDE_EFFORT_MODEL_MARKERS):
            return ModelReasoningConfig(
                adapter="anthropic-effort",
                effort=normalized_effort,
                operation=operation,
                request_params={
                    "output_config": {
                        "effort": _ASTRA_TO_STANDARD_EFFORT[normalized_effort]
                    }
                },
            )
        return _unsupported(
            normalized_effort,
            operation,
            adapter="anthropic-unsupported-model",
            reason="model_not_allowlisted_for_output_config_effort",
        )

    if normalized_provider == "qwen":
        if "thinking" in normalized_model:
            return _unsupported(
                normalized_effort,
                operation,
                adapter="qwen-thinking-only",
                reason="thinking_only_model_cannot_apply_unified_effort_safely",
            )
        if normalized_model.startswith(("qwen3", "qwen-plus")):
            if normalized_effort == ReasoningEffort.fast:
                return ModelReasoningConfig(
                    adapter="qwen-hybrid-thinking",
                    effort=normalized_effort,
                    operation=operation,
                    request_params={"enable_thinking": False},
                )
            return ModelReasoningConfig(
                adapter="qwen-hybrid-thinking",
                effort=normalized_effort,
                operation=operation,
                request_params={
                    "enable_thinking": True,
                    "thinking_budget": _QWEN_THINKING_BUDGET[normalized_effort],
                },
                include_json_mode=False,
                reason="thinking_mode_uses_prompt_enforced_json",
            )
        return _unsupported(
            normalized_effort,
            operation,
            adapter="qwen-unsupported-model",
            reason="model_not_allowlisted_for_hybrid_thinking",
        )

    if normalized_provider == "deepseek":
        return _unsupported(
            normalized_effort,
            operation,
            adapter="deepseek-model-selected-reasoning",
            reason="native_api_controls_reasoning_by_model_selection",
        )

    if normalized_provider == "google":
        return _unsupported(
            normalized_effort,
            operation,
            adapter="gemini-transport-unsupported",
            reason="native_generate_content_transport_not_implemented",
        )

    return _unsupported(
        normalized_effort,
        operation,
        adapter="unsupported-provider",
        reason="provider_has_no_declared_reasoning_adapter",
    )


def attach_reasoning_usage(
    usage: dict[str, Any] | None, config: ModelReasoningConfig
) -> dict[str, Any]:
    return {**(usage or {}), "astra_reasoning": config.usage_metadata()}


def _is_legacy_gpt5(model: str) -> bool:
    return model == "gpt-5" or model.startswith(
        ("gpt-5-2025-", "gpt-5-mini", "gpt-5-nano")
    )


def _unsupported(
    effort: ReasoningEffort,
    operation: ModelOperation,
    *,
    adapter: str,
    reason: str,
) -> ModelReasoningConfig:
    return ModelReasoningConfig(
        adapter=adapter,
        effort=effort,
        operation=operation,
        reason=reason,
    )
